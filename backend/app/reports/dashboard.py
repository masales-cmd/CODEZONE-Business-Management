from datetime import datetime, time, timezone, timedelta, date
from decimal import Decimal
from zoneinfo import ZoneInfo
from sqlalchemy import select, func, or_
from sqlalchemy.orm import Session
from app.models.models import Sale, SalesReturn, Purchase, PurchaseReturn, Expense, Product, Customer, Supplier, ProductBatch, ApprovalRequest
from app.services.transactions import sale_balance, purchase_balance

TZ = ZoneInfo("Africa/Dar_es_Salaam")

def _utc_window():
    now = datetime.now(TZ)
    start_local = datetime.combine(now.date(), time.min, tzinfo=TZ)
    end_local = datetime.combine(now.date(), time.max, tzinfo=TZ)
    return start_local.astimezone(timezone.utc).replace(tzinfo=None), end_local.astimezone(timezone.utc).replace(tzinfo=None)

def _dec(value):
    return Decimal(str(value or 0))

def dashboard_summary(db: Session, user=None):
    start_utc, end_utc = _utc_window()
    now_local = datetime.now(TZ)
    today = now_local.date()
    week_start_local = today - timedelta(days=today.weekday())
    week_start_utc = datetime.combine(week_start_local, time.min, tzinfo=TZ).astimezone(timezone.utc).replace(tzinfo=None)

    sales_rows = db.execute(select(Sale).where(Sale.status == 'posted', Sale.sale_date >= start_utc, Sale.sale_date <= end_utc)).scalars().all()
    sale_returns = db.execute(select(SalesReturn).where(SalesReturn.status == 'posted', SalesReturn.return_date >= start_utc, SalesReturn.return_date <= end_utc)).scalars().all()
    week_sales_rows = db.execute(select(Sale).where(Sale.status == 'posted', Sale.sale_date >= week_start_utc, Sale.sale_date <= end_utc)).scalars().all()
    week_returns = db.execute(select(SalesReturn).where(SalesReturn.status == 'posted', SalesReturn.return_date >= week_start_utc, SalesReturn.return_date <= end_utc)).scalars().all()
    recent_rows = db.execute(select(Sale, Customer.name.label('customer_name')).outerjoin(Customer, Customer.id == Sale.customer_id).where(Sale.status == 'posted').order_by(Sale.sale_date.desc(), Sale.id.desc()).limit(5)).all()

    expenses = db.execute(select(func.coalesce(func.sum(Expense.amount), 0)).where(Expense.expense_date == today)).scalar_one()
    stock = db.execute(select(func.coalesce(func.sum(Product.current_stock * Product.buying_price), 0)).where(Product.is_active == True)).scalar_one()

    returned_sales_total = sum((_dec(r.total) for r in sale_returns), Decimal('0'))
    returned_cash = sum((_dec(r.total) for r in sale_returns if r.refund_method in ('cash','bank')), Decimal('0'))
    today_sales = sum((_dec(s.total) for s in sales_rows), Decimal('0')) - returned_sales_total
    today_paid = sum((_dec(s.paid) for s in sales_rows), Decimal('0')) - returned_cash

    week_totals = {week_start_local + timedelta(days=i): Decimal('0') for i in range((today - week_start_local).days + 1)}
    for sale in week_sales_rows:
        local_day = sale.sale_date.replace(tzinfo=timezone.utc).astimezone(TZ).date() if sale.sale_date.tzinfo is None else sale.sale_date.astimezone(TZ).date()
        if local_day in week_totals:
            week_totals[local_day] += _dec(sale.total)
    for ret in week_returns:
        local_day = ret.return_date.replace(tzinfo=timezone.utc).astimezone(TZ).date() if ret.return_date.tzinfo is None else ret.return_date.astimezone(TZ).date()
        if local_day in week_totals:
            week_totals[local_day] -= _dec(ret.total)

    recent_sales = []
    for sale, customer_name in recent_rows:
        balance = max(_dec(sale_balance(db, sale)), Decimal('0'))
        paid = _dec(sale.paid)
        status = 'PAID' if balance <= 0 else ('CREDIT' if paid <= 0 else 'PARTIAL')
        recent_sales.append({
            'invoice_no': sale.invoice_no,
            'customer_name': customer_name or 'Walk-in',
            'total': float(_dec(sale.total)),
            'payment_status': status,
            'sale_date': (sale.sale_date.replace(tzinfo=timezone.utc).astimezone(TZ).date().isoformat() if sale.sale_date.tzinfo is None else sale.sale_date.astimezone(TZ).date().isoformat())
        })

    customer_debt = sum((max(_dec(sale_balance(db, s)), Decimal('0')) for s in db.execute(select(Sale).where(Sale.status == 'posted', Sale.customer_id.is_not(None))).scalars()), Decimal('0'))
    customer_debt += sum((_dec(x.opening_balance) for x in db.execute(select(Customer).where(Customer.opening_balance > 0)).scalars()), Decimal('0'))
    supplier_debt = sum((max(_dec(purchase_balance(db, p)), Decimal('0')) for p in db.execute(select(Purchase).where(Purchase.status == 'posted', Purchase.supplier_id.is_not(None))).scalars()), Decimal('0'))
    supplier_debt += sum((_dec(x.opening_balance) for x in db.execute(select(Supplier).where(Supplier.opening_balance > 0)).scalars()), Decimal('0'))

    low_stock = db.execute(select(Product).where(Product.is_active == True, Product.item_type == 'product', Product.track_inventory == True, or_(Product.current_stock <= Product.reorder_level, Product.current_stock <= Product.minimum_stock)).order_by(Product.current_stock.asc()).limit(20)).scalars().all()
    expiring_cutoff = today + timedelta(days=30)
    expiring_batches = db.execute(select(ProductBatch, Product.name.label('product_name')).join(Product, Product.id == ProductBatch.product_id).where(ProductBatch.is_active == True, ProductBatch.quantity > 0, ProductBatch.expiry_date.is_not(None), ProductBatch.expiry_date <= expiring_cutoff).order_by(ProductBatch.expiry_date.asc()).limit(20)).all()
    overdue_count = 0
    overdue_amount = Decimal('0')
    overdue_customers = set()
    for sale, customer in db.execute(select(Sale, Customer).join(Customer, Customer.id == Sale.customer_id).where(Sale.status == 'posted', Sale.customer_id.is_not(None))).all():
        balance = max(_dec(sale_balance(db, sale)), Decimal('0'))
        if balance <= 0:
            continue
        due_date = (sale.sale_date.replace(tzinfo=timezone.utc).astimezone(TZ).date() if sale.sale_date.tzinfo is None else sale.sale_date.astimezone(TZ).date()) + timedelta(days=int(customer.payment_terms_days or 0))
        if due_date < today:
            overdue_count += 1
            overdue_amount += balance
            overdue_customers.add(customer.id)
    pending_approvals = db.query(ApprovalRequest).filter(ApprovalRequest.status == 'pending').count()

    attention = []
    if low_stock:
        attention.append({'type':'low_stock','count':len(low_stock),'title':'Low stock','detail':f"{len(low_stock)} item{'s' if len(low_stock)!=1 else ''} at or below reorder level.",'page':'products'})
    if expiring_batches:
        attention.append({'type':'expiring','count':len(expiring_batches),'title':'Expiring batches','detail':f"{len(expiring_batches)} batch{'es' if len(expiring_batches)!=1 else ''} expires within 30 days.",'page':'enterprise'})
    if overdue_count:
        attention.append({'type':'overdue','count':overdue_count,'title':'Overdue receivables','detail':f"{len(overdue_customers)} customer{'s' if len(overdue_customers)!=1 else ''} owe {_dec(overdue_amount):,.0f}.",'page':'customers'})
    if pending_approvals:
        attention.append({'type':'approvals','count':pending_approvals,'title':'Pending approvals','detail':f"{pending_approvals} approval request{'s' if pending_approvals!=1 else ''} need review.",'page':'approvals'})

    role = getattr(getattr(user, 'role', None), 'name', None) if user else None
    privileged_finance = role in ('owner','admin','manager','accountant')
    return {
        'today_sales': today_sales,
        'today_paid': today_paid,
        'today_expenses': _dec(expenses),
        'stock_value': _dec(stock) if privileged_finance else None,
        'customer_debt': customer_debt if role in ('owner','admin','manager','accountant','salesperson','cashier') else None,
        'supplier_debt': supplier_debt if privileged_finance else None,
        'week_sales': [{'date': d.isoformat(), 'label': d.strftime('%a'), 'total': float(v)} for d,v in week_totals.items()],
        'recent_sales': recent_sales,
        'attention': attention[:4],
        'overdue_receivables': float(overdue_amount),
        'low_stock_count': len(low_stock),
        'expiring_batch_count': len(expiring_batches),
        'pending_approval_count': pending_approvals
    }
