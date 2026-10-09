from decimal import Decimal
from datetime import datetime
from uuid import uuid4
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.models.models import (
    Sale, SaleItem, Product, StockMovement, CashTransaction, BankTransaction,
    BankAccount, Customer, CustomerGroup, ProductUnitConversion, EmptyContainerType, EmptyContainerMovement,
    StockBalance, Warehouse, PriceListItem, TaxRate, SalePayment, PaymentTransaction,
    ProductBatch, ProductSerial, Shift
)
from app.services.accounting import build_sales_journal
from app.services.transactions import audit, sale_balance, active_shift_id
from app.services.sync import queue_sync_event
from app.schemas.common import SaleCreate


def generate_invoice_no(db: Session) -> str:
    prefix = datetime.now().strftime('INV-%Y%m%d')
    for _ in range(40):
        candidate = f'{prefix}-{uuid4().hex[:8].upper()}'
        if not db.query(Sale).filter(Sale.invoice_no == candidate).first():
            return candidate
    raise ValueError('Unable to allocate a unique invoice number. Please retry the sale.')


def _price_from_list(db, price_list_id, product_id, unit_id):
    if not price_list_id:
        return None
    row = db.query(PriceListItem).filter(
        PriceListItem.price_list_id == price_list_id,
        PriceListItem.product_id == product_id,
        PriceListItem.unit_id == unit_id,
    ).first()
    if row:
        return Decimal(row.price)
    if unit_id is not None:
        row = db.query(PriceListItem).filter(
            PriceListItem.price_list_id == price_list_id,
            PriceListItem.product_id == product_id,
            PriceListItem.unit_id.is_(None),
        ).first()
        if row:
            return Decimal(row.price)
    return None


def _warehouse_balance(db, product_id, warehouse_id, fallback_stock):
    warehouse = db.get(Warehouse, warehouse_id)
    if not warehouse:
        raise ValueError('Warehouse not found.')
    row = db.query(StockBalance).filter_by(warehouse_id=warehouse_id, product_id=product_id).with_for_update().first()
    if row is None:
        existing_total = db.query(func.coalesce(func.sum(StockBalance.quantity), 0)).filter(StockBalance.product_id == product_id).scalar() or 0
        opening = Decimal(fallback_stock or 0) if Decimal(str(existing_total)) == 0 else Decimal('0')
        row = StockBalance(warehouse_id=warehouse_id, product_id=product_id, quantity=opening, reorder_level=0)
        db.add(row); db.flush()
    return row


def _payment_account_method(method):
    return method if method in {'cash','bank','mobile_money','card','cheque'} else None


def create_sale(db: Session, payload: SaleCreate, user_id=None) -> Sale:
    resolved_shift_id = payload.shift_id or active_shift_id(db, user_id)
    if not resolved_shift_id:
        raise ValueError('Open a shift before making a sale.')
    active_shift = db.get(Shift, resolved_shift_id)
    if not active_shift or active_shift.status != 'open' or active_shift.user_id != user_id:
        raise ValueError('The selected shift is not open for this user.')
    invoice_no = (payload.invoice_no or '').strip() or generate_invoice_no(db)
    if db.query(Sale).filter(Sale.invoice_no == invoice_no).first():
        raise ValueError('Invoice number already exists.')
    if not payload.items:
        raise ValueError('A sale must contain at least one item.')
    try:
        customer = None
        group_discount = Decimal('0')
        resolved_price_list_id = payload.price_list_id
        if payload.customer_id:
            customer = db.execute(select(Customer).where(Customer.id == payload.customer_id).with_for_update()).scalar_one_or_none()
            if not customer:
                raise ValueError('Customer not found.')
            if not resolved_price_list_id and customer.group_id:
                group = db.get(CustomerGroup, customer.group_id)
                if group and group.is_active:
                    resolved_price_list_id = group.default_price_list_id
                    group_discount = Decimal(group.discount_percent or 0)
        subtotal = Decimal('0'); cost_total = Decimal('0'); items = []; seen = set(); requested = {}; locked = {}
        for row in payload.items:
            qty = Decimal(row.quantity)
            if row.product_id in seen:
                raise ValueError('Each product can appear only once on a sale. Combine duplicate lines.')
            seen.add(row.product_id)
            if qty <= 0:
                raise ValueError('Sale quantity must be greater than zero.')
            product = db.execute(select(Product).where(Product.id == row.product_id).with_for_update()).scalar_one_or_none()
            if not product or not product.is_active:
                raise ValueError(f'Product {row.product_id} is inactive or not found.')
            factor = Decimal('1')
            if row.unit_id and row.unit_id != product.base_unit_id:
                if not product.base_unit_id:
                    raise ValueError(f'No base unit configured for {product.name}.')
                conv = db.query(ProductUnitConversion).filter_by(product_id=product.id, from_unit_id=row.unit_id, to_unit_id=product.base_unit_id).first()
                if not conv:
                    raise ValueError(f'No unit conversion configured for {product.name}.')
                factor = Decimal(conv.factor)
            price = Decimal(row.unit_price)
            list_price = _price_from_list(db, resolved_price_list_id, product.id, row.unit_id)
            if list_price is not None and price <= 0:
                price = list_price
            if price <= 0:
                price = Decimal(product.selling_price or 0)
                if group_discount and resolved_price_list_id is None:
                    price = (price * (Decimal('100') - group_discount) / Decimal('100')).quantize(Decimal('0.01'))
            if price <= 0:
                raise ValueError(f'Sale price for {product.name} must be greater than zero.')
            base_qty = qty * factor
            if getattr(product, 'track_inventory', True):
                requested[product.id] = requested.get(product.id, Decimal('0')) + base_qty
            locked[product.id] = (product, factor, price)

        for pid, base_qty in requested.items():
            product, _, _ = locked[pid]
            if payload.warehouse_id:
                row = _warehouse_balance(db, pid, payload.warehouse_id, product.current_stock)
                available = Decimal(row.quantity or 0) - Decimal(row.reserved_quantity or 0)
            else:
                row = None
                available = Decimal(product.current_stock or 0)
            if available < base_qty:
                raise ValueError(f'Insufficient stock for {product.name}. Required: {base_qty}, available: {available}.')

        for row, qty in [(r, Decimal(r.quantity)) for r in payload.items]:
            product, factor, price = locked[row.product_id]
            base_qty = qty * factor
            line = qty * price
            base_unit_cost = Decimal('0') if getattr(product, 'item_type', 'product') == 'service' else Decimal(product.buying_price or 0)
            if price < base_unit_cost * factor and not payload.allow_below_cost:
                raise ValueError(f'Sale price for {product.name} cannot be below current stock cost. Manager/owner override is required.')
            subtotal += line
            cost_total += base_qty * base_unit_cost
            items.append((row, product, line, base_qty, base_unit_cost))

        discount = Decimal(payload.discount)
        if discount < 0 or discount > subtotal:
            raise ValueError('Invalid discount.')
        net = subtotal - discount
        tax_amount = Decimal('0'); total = net
        tax = db.get(TaxRate, payload.tax_rate_id) if payload.tax_rate_id else None
        if tax and tax.is_active and Decimal(tax.rate) > 0:
            rate = Decimal(tax.rate) / Decimal('100')
            if tax.inclusive:
                tax_amount = (net * rate / (Decimal('1') + rate)).quantize(Decimal('0.01'))
                total = net
            else:
                tax_amount = (net * rate).quantize(Decimal('0.01'))
                total = net + tax_amount
        if total <= 0:
            raise ValueError('Sale total must be greater than zero.')

        lines = list(payload.payments or [])
        if lines:
            paid = sum((Decimal(x.amount) for x in lines), Decimal('0'))
            if payload.paid and Decimal(payload.paid) != paid:
                raise ValueError('The explicit paid amount does not match payment lines.')
        else:
            paid = Decimal(payload.paid)
            if paid:
                if payload.payment_method == 'credit':
                    raise ValueError('Credit sales do not require a paid amount.')
                lines = [type('PaymentLine', (), {
                    'payment_method': payload.payment_method,
                    'amount': paid,
                    'bank_account_id': payload.bank_account_id,
                    'reference': payload.reference,
                    'external_reference': None,
                })()]
        if paid > total:
            raise ValueError('Paid amount cannot exceed sale total.')
        if total > paid and not payload.customer_id:
            raise ValueError('Credit or partial sale requires a customer.')
        if total < cost_total and not payload.allow_below_cost:
            raise ValueError('Sale total is below the total cost of the goods. Manager/owner override is required.')

        if customer:
            credit = total - paid
            if credit > 0:
                open_sales = db.query(Sale).filter(Sale.customer_id == customer.id, Sale.status == 'posted').all()
                outstanding = Decimal(customer.opening_balance or 0) + sum((sale_balance(db, s) for s in open_sales), Decimal('0'))
                if Decimal(customer.credit_limit or 0) <= 0 or outstanding + credit > Decimal(customer.credit_limit):
                    raise ValueError('Customer credit limit would be exceeded.')

        for pl in lines:
            method = pl.payment_method
            amount = Decimal(pl.amount)
            if amount <= 0:
                raise ValueError('Payment amount must be greater than zero.')
            if method == 'bank':
                if not pl.bank_account_id or not db.get(BankAccount, pl.bank_account_id):
                    raise ValueError('Bank account is required for bank payments.')
            elif method == 'cash':
                pass
            elif method in ('mobile_money','card','cheque'):
                pass
            elif method == 'credit':
                raise ValueError('Credit is the unpaid balance, not a payment line.')
            else:
                raise ValueError('Unsupported payment method.')

        container_deposit = Decimal('0'); container = None
        if payload.container_type_id:
            container = db.execute(select(EmptyContainerType).where(EmptyContainerType.id == payload.container_type_id).with_for_update()).scalar_one_or_none()
            if not container or not container.is_active:
                raise ValueError('Empty container type not found.')
            if payload.containers_issued > Decimal(container.current_stock or 0) + Decimal(payload.containers_returned):
                raise ValueError('Not enough empty containers in stock.')
            container.current_stock = Decimal(container.current_stock or 0) + Decimal(payload.containers_returned) - Decimal(payload.containers_issued)
            container_deposit = max(Decimal('0'), Decimal(payload.containers_issued) - Decimal(payload.containers_returned)) * Decimal(container.deposit_value or 0)

        methods = {x.payment_method for x in lines}
        payment_method = next(iter(methods)) if len(methods) == 1 else ('split' if methods else 'credit')
        sale = Sale(invoice_no=invoice_no, customer_id=payload.customer_id,
                    container_type_id=payload.container_type_id, containers_issued=payload.containers_issued,
                    containers_returned=payload.containers_returned, container_deposit=container_deposit,
                    subtotal=subtotal, discount=discount, tax_amount=tax_amount, total=total, paid=paid,
                    payment_method=payment_method, bank_account_id=payload.bank_account_id,
                    branch_id=payload.branch_id, warehouse_id=payload.warehouse_id,
                    price_list_id=resolved_price_list_id, shift_id=resolved_shift_id,
                    tax_rate_id=payload.tax_rate_id)
        db.add(sale); db.flush()

        if container:
            if payload.containers_issued:
                db.add(EmptyContainerMovement(container_type_id=container.id, movement_type='sale_issued', quantity=-payload.containers_issued, reference_type='sale', reference_id=sale.id))
            if payload.containers_returned:
                db.add(EmptyContainerMovement(container_type_id=container.id, movement_type='sale_returned', quantity=payload.containers_returned, reference_type='sale', reference_id=sale.id))

        for row, product, line, base_qty, base_unit_cost in items:
            qty = Decimal(row.quantity)
            cost_per_sold_unit = (base_qty * base_unit_cost / qty if qty else base_unit_cost)
            batch = None
            selected_batch_id = row.batch_id
            if not selected_batch_id and getattr(product,'track_inventory',True):
                # FEFO default: when a caller does not select a batch, use the earliest
                # non-expired batch that can satisfy the complete line. If no single
                # batch can satisfy it, the client can split the sale into two lines.
                today = datetime.now().date()
                candidates = db.query(ProductBatch).filter(
                    ProductBatch.product_id == product.id,
                    ProductBatch.is_active == True,
                    ProductBatch.quantity >= base_qty,
                ).order_by(ProductBatch.expiry_date.asc().nullslast(), ProductBatch.id.asc()).all()
                for candidate in candidates:
                    if candidate.expiry_date and candidate.expiry_date < today:
                        continue
                    selected_batch_id = candidate.id
                    break
            if selected_batch_id:
                batch = db.query(ProductBatch).filter(ProductBatch.id == selected_batch_id, ProductBatch.product_id == product.id).with_for_update().first()
                if not batch or Decimal(batch.quantity or 0) < base_qty:
                    raise ValueError(f'Batch stock is insufficient for {product.name}.')
                if batch.expiry_date and batch.expiry_date < datetime.now().date():
                    raise ValueError(f'Batch {batch.batch_no} for {product.name} has expired.')
                batch.quantity = Decimal(batch.quantity) - base_qty
            serial = None
            if row.serial_id:
                if qty != 1 or factor != 1:
                    raise ValueError('A serialised product must be sold one unit at a time.')
                serial = db.query(ProductSerial).filter(ProductSerial.id == row.serial_id, ProductSerial.product_id == product.id).with_for_update().first()
                if not serial or serial.status != 'in_stock':
                    raise ValueError('Serial number is not available.')
                serial.status = 'sold'; serial.sold_sale_id = sale.id
            db.add(SaleItem(sale_id=sale.id, product_id=product.id, unit_id=row.unit_id, quantity=row.quantity,
                             unit_price=(line / qty if qty else row.unit_price), cost_price=cost_per_sold_unit,
                             line_total=line, batch_id=selected_batch_id, serial_id=row.serial_id))
            if getattr(product, 'track_inventory', True):
                if payload.warehouse_id:
                    bal = _warehouse_balance(db, product.id, payload.warehouse_id, product.current_stock)
                    bal.quantity = Decimal(bal.quantity) - base_qty
                product.current_stock = Decimal(product.current_stock or 0) - base_qty
                db.add(StockMovement(product_id=product.id, movement_type='sale', quantity=-base_qty, reference_type='sale', reference_id=sale.id))

        for pl in lines:
            db.add(SalePayment(sale_id=sale.id, payment_method=pl.payment_method, amount=pl.amount,
                               bank_account_id=pl.bank_account_id, reference=pl.reference or payload.reference))
            method = pl.payment_method
            if method == 'cash':
                db.add(CashTransaction(transaction_type='sale', amount=pl.amount, reference=pl.reference or invoice_no, description='Cash sale', shift_id=payload.shift_id or active_shift_id(db,user_id)))
            elif method == 'bank':
                db.add(BankTransaction(bank_account_id=pl.bank_account_id, transaction_type='sale', amount=pl.amount, reference=pl.reference or invoice_no, description='Bank sale'))
            elif method in ('mobile_money','card','cheque'):
                db.add(PaymentTransaction(direction='in', payment_method=method, amount=pl.amount,
                                          reference=pl.reference or invoice_no, external_reference=getattr(pl,'external_reference',None), bank_account_id=pl.bank_account_id))

        if paid and payload.shift_id:
            # Shift expected cash is recomputed on close; no denormalised mutation here.
            pass

        build_sales_journal(db, sale.id)
        queue_sync_event(db, 'sale', sale.id, 'created', {'invoice_no':sale.invoice_no,'customer_id':sale.customer_id,'total':str(sale.total),'paid':str(sale.paid),'warehouse_id':sale.warehouse_id})
        audit(db, 'sale_created', 'sale', sale.id,
              after={'invoice_no': sale.invoice_no, 'total': str(sale.total), 'paid': str(sale.paid), 'warehouse_id': sale.warehouse_id, 'tax_amount': str(sale.tax_amount)},
              user_id=user_id)
        db.commit(); db.refresh(sale); return sale
    except Exception:
        db.rollback(); raise
