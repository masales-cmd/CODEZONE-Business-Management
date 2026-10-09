from __future__ import annotations
from datetime import datetime, date, timedelta
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.models import SalesQuote, SalesQuoteItem, SalesOrder, SalesOrderItem, Product, Customer, Sale
from app.services.sales import create_sale
from app.schemas.common import QuoteCreate, OrderConvertToSale
from app.schemas.common import SaleCreate, SaleItemCreate
from app.services.transactions import audit


def _unique_number(db: Session, model, field_name: str, prefix: str) -> str:
    field = getattr(model, field_name)
    stamp = datetime.now().strftime('%Y%m%d')
    for _ in range(30):
        value = f'{prefix}-{stamp}-{uuid4().hex[:8].upper()}'
        if not db.query(model).filter(field == value).first():
            return value
    raise ValueError(f'Unable to allocate a unique {prefix} number. Please retry.')


def create_quote(db: Session, payload: QuoteCreate, user_id: int | None = None) -> SalesQuote:
    if payload.discount < 0:
        raise ValueError('Discount cannot be negative.')
    subtotal = Decimal('0')
    seen = set()
    rows = []
    for item in payload.items:
        if item.product_id in seen:
            raise ValueError('Each product can appear only once on a quote.')
        seen.add(item.product_id)
        product = db.get(Product, item.product_id)
        if not product or not product.is_active:
            raise ValueError(f'Product {item.product_id} is inactive or not found.')
        if item.unit_price <= 0 or item.quantity <= 0:
            raise ValueError('Quote quantities and prices must be greater than zero.')
        line = Decimal(item.quantity) * Decimal(item.unit_price)
        subtotal += line
        rows.append((item, line))
    total = subtotal - Decimal(payload.discount)
    if total <= 0:
        raise ValueError('Quote total must be greater than zero.')
    if payload.customer_id and not db.get(Customer, payload.customer_id):
        raise ValueError('Customer not found.')
    quote = SalesQuote(
        quote_no=(payload.quote_no or '').strip() or _unique_number(db, SalesQuote, 'quote_no', 'QT'),
        customer_id=payload.customer_id,
        issue_date=datetime.utcnow(),
        valid_until=payload.valid_until,
        subtotal=subtotal,
        discount=payload.discount,
        total=total,
        status='draft',
        notes=(payload.notes or '').strip() or None,
        created_by=user_id,
    )
    db.add(quote)
    db.flush()
    for item, line in rows:
        db.add(SalesQuoteItem(quote_id=quote.id, product_id=item.product_id, unit_id=item.unit_id,
                              quantity=item.quantity, unit_price=item.unit_price, line_total=line))
    audit(db, 'quote_created', 'sales_quote', quote.id,
          after={'quote_no': quote.quote_no, 'total': str(quote.total)}, user_id=user_id)
    db.commit(); db.refresh(quote)
    return quote


def convert_quote_to_order(db: Session, quote_id: int, user_id: int | None = None) -> SalesOrder:
    quote = db.get(SalesQuote, quote_id)
    if not quote:
        raise ValueError('Quote not found.')
    if quote.status not in ('accepted', 'sent'):
        raise ValueError('Only sent or accepted quotes can be converted to an order.')
    existing = db.query(SalesOrder).filter(SalesOrder.quote_id == quote.id).first()
    if existing:
        return existing
    items = db.query(SalesQuoteItem).filter(SalesQuoteItem.quote_id == quote.id).all()
    order = SalesOrder(
        order_no=_unique_number(db, SalesOrder, 'order_no', 'SO'),
        quote_id=quote.id,
        customer_id=quote.customer_id,
        order_date=datetime.utcnow(),
        subtotal=quote.subtotal,
        discount=quote.discount,
        total=quote.total,
        status='confirmed',
        notes=quote.notes,
        created_by=user_id,
    )
    db.add(order); db.flush()
    for item in items:
        db.add(SalesOrderItem(order_id=order.id, product_id=item.product_id, unit_id=item.unit_id,
                              quantity=item.quantity, unit_price=item.unit_price, line_total=item.line_total))
    quote.status='accepted'
    audit(db, 'quote_converted_to_order', 'sales_quote', quote.id,
          after={'order_id': order.id, 'order_no': order.order_no}, user_id=user_id)
    db.commit(); db.refresh(order)
    return order


def convert_order_to_sale(db: Session, order_id: int, payload: OrderConvertToSale, user_id: int | None = None) -> Sale:
    order = db.get(SalesOrder, order_id)
    if not order:
        raise ValueError('Sales order not found.')
    if order.status in ('cancelled', 'fulfilled'):
        raise ValueError('This sales order has already been cancelled or fulfilled.')
    existing = db.query(Sale).filter(Sale.invoice_no.like(f'SO-{order.order_no}-%')).first()
    if existing:
        return existing
    items = db.query(SalesOrderItem).filter(SalesOrderItem.order_id == order.id).all()
    if not items:
        raise ValueError('Sales order has no items.')
    paid = Decimal(payload.paid)
    if paid > order.total:
        raise ValueError('Paid amount cannot exceed the order total.')
    sale_items=[SaleItemCreate(product_id=i.product_id, quantity=i.quantity, unit_price=i.unit_price, unit_id=i.unit_id) for i in items]
    # A walk-in order can be converted only when paid in full; credit/partial sales need a customer.
    if paid < order.total and not order.customer_id:
        raise ValueError('This order needs a customer for credit or partial payment.')
    sale = create_sale(db, SaleCreate(
        invoice_no=None,
        customer_id=order.customer_id,
        items=sale_items,
        discount=order.discount,
        paid=paid,
        payment_method=payload.payment_method,
        bank_account_id=payload.bank_account_id,
        reference=payload.reference or order.order_no,
        allow_below_cost=False,
    ), user_id=user_id)
    order.status='fulfilled'
    db.add(order)
    audit(db, 'sales_order_converted_to_sale', 'sales_order', order.id,
          after={'sale_id': sale.id, 'invoice_no': sale.invoice_no}, user_id=user_id)
    db.commit()
    return sale
