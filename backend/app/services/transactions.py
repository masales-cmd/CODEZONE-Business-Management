import json
from datetime import date, datetime, timezone
from decimal import Decimal
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.models.models import (Customer,Supplier,Sale,SaleItem,Purchase,PurchaseItem,Product,StockMovement,SyncOutbox,
    CustomerPayment,SupplierPayment,SalesReturn,SalesReturnItem,PurchaseReturn,PurchaseReturnItem,ProductUnitConversion,StockBalance,
    CashTransaction,BankTransaction,BankAccount,AuditLog,PaymentTransaction,Shift)
from app.services.accounting import build_customer_payment_journal,build_supplier_payment_journal,build_sales_return_journal,build_purchase_return_journal,post_journal,account
from app.services.sync import queue_sync_event

def audit(db,action,entity_type,entity_id=None,before=None,after=None,user_id=None):
    before_json=json.dumps(before,default=str) if before is not None else None
    after_json=json.dumps(after,default=str) if after is not None else None
    db.add(AuditLog(user_id=user_id,action=action,entity_type=entity_type,entity_id=entity_id,before_json=before_json,after_json=after_json))
    try:
        from uuid import uuid4
        db.add(SyncOutbox(event_id=uuid4().hex,entity_type=entity_type,entity_id=entity_id,action=action,payload_json=json.dumps({'before':before,'after':after},default=str)))
    except Exception:
        pass

def ensure_cash_available(db, amount):
    amount=Decimal(str(amount))
    balance=sum((Decimal(str(x.amount)) for x in db.query(CashTransaction).all()), Decimal('0'))
    if balance < amount:
        raise ValueError(f'Insufficient cash balance. Available: {balance}, required: {amount}.')

def active_shift_id(db, user_id):
    if not user_id:
        return None
    row=db.query(Shift).filter(Shift.user_id==user_id,Shift.status=='open').order_by(Shift.opened_at.desc()).first()
    return row.id if row else None

def money_flow(db,amount,method,bank_account_id,in_type,out_type,reference,description,user_id=None,shift_id=None):
    method=method or 'cash'; amount=Decimal(str(amount))
    if method not in ('cash','bank','mobile_money','card','cheque'): raise ValueError('Unsupported payment method.')
    if method=='cash':
        if amount < 0: ensure_cash_available(db, -amount)
        db.add(CashTransaction(transaction_type=in_type if amount>=0 else out_type,amount=amount,reference=reference,description=description,shift_id=shift_id or active_shift_id(db,user_id)))
    elif method=='bank':
        if not bank_account_id or not db.get(BankAccount,bank_account_id): raise ValueError('Bank account not found.')
        db.add(BankTransaction(bank_account_id=bank_account_id,transaction_type=in_type if amount>=0 else out_type,
            amount=amount,reference=reference,description=description))
    else:
        db.add(PaymentTransaction(direction='in' if amount>=0 else 'out',payment_method=method,amount=abs(amount),reference=reference,bank_account_id=bank_account_id))

def _sale_return_totals(db: Session, sale_id: int):
    rows=db.query(SalesReturn).filter(SalesReturn.sale_id==sale_id, SalesReturn.status=='posted').all()
    total=Decimal('0'); cash_refund=Decimal('0')
    for r in rows:
        total += Decimal(r.total or 0)
        if r.refund_method in ('cash','bank'):
            cash_refund += Decimal(r.total or 0)
    return total, cash_refund

def sale_balance(db: Session, sale: Sale):
    returns, cash_refunds = _sale_return_totals(db, sale.id)
    return Decimal(sale.total or 0) - returns - Decimal(sale.paid or 0) + cash_refunds


def _purchase_return_totals(db: Session, purchase_id: int):
    rows=db.query(PurchaseReturn).filter(PurchaseReturn.purchase_id==purchase_id, PurchaseReturn.status=='posted').all()
    total=Decimal('0'); cash_refunds=Decimal('0')
    for r in rows:
        total += Decimal(r.total or 0)
        if r.settlement_method in ('cash','bank'):
            cash_refunds += Decimal(r.total or 0)
    return total, cash_refunds

def purchase_balance(db: Session, purchase: Purchase):
    returns, refunds=_purchase_return_totals(db,purchase.id)
    return Decimal(purchase.total or 0)-returns-Decimal(purchase.paid or 0)+refunds

def record_customer_payment(db:Session,payload,user_id=None):
    customer=db.get(Customer,payload.customer_id)
    if not customer: raise ValueError('Customer not found.')
    if payload.amount <= 0: raise ValueError('Payment amount must be greater than zero.')
    if payload.payment_method not in ('cash','bank','mobile_money','card','cheque'): raise ValueError('Unsupported payment method.')
    if payload.payment_method=='bank' and (not payload.bank_account_id or not db.get(BankAccount,payload.bank_account_id)): raise ValueError('Valid bank account is required for bank payment.')
    sale=db.query(Sale).filter(Sale.id==payload.sale_id).with_for_update().first() if payload.sale_id else None
    if sale:
        if sale.customer_id!=customer.id: raise ValueError('Sale does not belong to this customer.')
        if sale.status!='posted': raise ValueError('Sale is not posted.')
        balance=sale_balance(db,sale)
        if payload.amount>balance: raise ValueError('Payment exceeds invoice balance.')
        sale.paid+=payload.amount
    else:
        opening=Decimal(customer.opening_balance or 0)
        if payload.amount>opening: raise ValueError(f'Payment exceeds opening balance ({opening}).')
        customer.opening_balance=opening-payload.amount
    payment=CustomerPayment(customer_id=customer.id,sale_id=payload.sale_id,amount=payload.amount,payment_method=payload.payment_method,bank_account_id=payload.bank_account_id,reference=payload.reference,notes=payload.notes)
    db.add(payment); db.flush()
    money_flow(db,payload.amount,payload.payment_method,payload.bank_account_id,'customer_payment','customer_refund',payload.reference,payload.notes,user_id=user_id)
    audit(db,'customer_payment_created','customer_payment',payment.id,after={'customer_id':customer.id,'sale_id':payload.sale_id,'opening_balance_payment':payload.sale_id is None,'amount':str(payload.amount)},user_id=user_id)
    build_customer_payment_journal(db,payment.id)
    db.commit(); db.refresh(payment); return payment

def record_supplier_payment(db:Session,payload,user_id=None):
    supplier=db.get(Supplier,payload.supplier_id)
    if not supplier: raise ValueError('Supplier not found.')
    if payload.amount <= 0: raise ValueError('Payment amount must be greater than zero.')
    if payload.payment_method not in ('cash','bank','mobile_money','card','cheque'): raise ValueError('Unsupported payment method.')
    if payload.payment_method=='bank' and (not payload.bank_account_id or not db.get(BankAccount,payload.bank_account_id)): raise ValueError('Valid bank account is required for bank payment.')
    purchase=db.query(Purchase).filter(Purchase.id==payload.purchase_id).with_for_update().first() if payload.purchase_id else None
    if purchase:
        if purchase.supplier_id!=supplier.id: raise ValueError('Purchase does not belong to this supplier.')
        if purchase.status!='posted': raise ValueError('Purchase is not posted.')
        balance=max(purchase_balance(db,purchase),Decimal('0'))
        if payload.amount>balance: raise ValueError(f'Payment exceeds supplier balance ({balance}).')
        purchase.paid+=payload.amount
    else:
        opening=Decimal(supplier.opening_balance or 0)
        if payload.amount>opening: raise ValueError(f'Payment exceeds opening balance ({opening}).')
        supplier.opening_balance=opening-payload.amount
    payment=SupplierPayment(supplier_id=supplier.id,purchase_id=payload.purchase_id,amount=payload.amount,payment_method=payload.payment_method,bank_account_id=payload.bank_account_id,reference=payload.reference,notes=payload.notes)
    db.add(payment); db.flush()
    money_flow(db,-payload.amount,payload.payment_method,payload.bank_account_id,'supplier_refund','supplier_payment',payload.reference,payload.notes,user_id=user_id)
    audit(db,'supplier_payment_created','supplier_payment',payment.id,after={'supplier_id':supplier.id,'purchase_id':payload.purchase_id,'opening_balance_payment':payload.purchase_id is None,'amount':str(payload.amount)},user_id=user_id)
    build_supplier_payment_journal(db,payment.id)
    db.commit(); db.refresh(payment); return payment


def _return_base_quantity(db: Session, product_id: int, qty: Decimal, unit_id: int | None):
    if not unit_id:
        return Decimal(qty)
    product=db.get(Product,product_id)
    if not product or not product.base_unit_id or unit_id==product.base_unit_id:
        return Decimal(qty)
    conv=db.query(ProductUnitConversion).filter_by(product_id=product_id,from_unit_id=unit_id,to_unit_id=product.base_unit_id).first()
    if not conv: raise ValueError(f'No unit conversion configured for {product.name}.')
    return Decimal(qty)*Decimal(conv.factor)


def _sold_factor(db, item):
    if not item.unit_id:
        return Decimal('1')
    product=db.get(Product,item.product_id)
    if not product or not product.base_unit_id or item.unit_id==product.base_unit_id:
        return Decimal('1')
    conv=db.query(ProductUnitConversion).filter_by(product_id=item.product_id,from_unit_id=item.unit_id,to_unit_id=product.base_unit_id).first()
    if not conv: raise ValueError(f'No unit conversion configured for {product.name}.')
    return Decimal(conv.factor)


def sales_return(db:Session,payload,user_id=None):
    sale=db.get(Sale,payload.sale_id)
    if not sale or sale.status!='posted': raise ValueError('Posted sale not found.')
    if not payload.items: raise ValueError('A return must contain at least one item.')
    original={}
    for i in db.query(SaleItem).filter(SaleItem.sale_id==sale.id).order_by(SaleItem.id).all(): original.setdefault(i.product_id,[]).append(i)
    returned={}
    old=db.query(SalesReturnItem).join(SalesReturn,SalesReturn.id==SalesReturnItem.sales_return_id).filter(SalesReturn.sale_id==sale.id,SalesReturn.status=='posted').all()
    for r in old: returned[r.product_id]=returned.get(r.product_id,Decimal('0'))+Decimal(r.quantity)
    requested={}
    input_meta={}
    for row in payload.items:
        bq=_return_base_quantity(db,row.product_id,Decimal(row.quantity),getattr(row,'unit_id',None)); requested[row.product_id]=requested.get(row.product_id,Decimal('0'))+bq
        meta=input_meta.setdefault(row.product_id, {'unit_id': getattr(row,'unit_id',None), 'requested_quantity': Decimal('0')})
        if meta['unit_id'] == getattr(row,'unit_id',None):
            meta['requested_quantity'] += Decimal(row.quantity)
        else:
            meta['unit_id'] = None
            meta['requested_quantity'] = Decimal('0')
    total=Decimal('0'); rows=[]
    for product_id,base_qty in requested.items():
        candidates=original.get(product_id)
        if not candidates: raise ValueError(f'Product {product_id} was not on the sale.')
        available=sum((Decimal(x.quantity)*_sold_factor(db,x) for x in candidates),Decimal('0'))-returned.get(product_id,Decimal('0'))
        if base_qty>available: raise ValueError('Return quantity exceeds sold quantity.')
        remaining=base_qty; line_total=Decimal('0'); cost_total=Decimal('0')
        for item in candidates:
            factor=_sold_factor(db,item); available_base=Decimal(item.quantity)*factor; take_base=min(remaining,available_base)
            if take_base:
                sold_qty=take_base/factor; line_total+=sold_qty*Decimal(item.unit_price); cost_total+=take_base*Decimal(item.cost_price)/factor; remaining-=take_base
            if remaining<=0: break
        total+=line_total; meta=input_meta.get(product_id,{'unit_id':None,'requested_quantity':base_qty}); rows.append((product_id,base_qty,line_total,cost_total,meta))
    if payload.refund_method not in ('cash','credit','bank'): raise ValueError('Refund method must be cash, bank or credit.')
    if payload.refund_method in ('cash','bank'):
        _, cash_refunds=_sale_return_totals(db,sale.id); available_paid=Decimal(sale.paid or 0)-cash_refunds
        if total>available_paid: raise ValueError('Refund exceeds amount paid on the invoice.')
        if payload.refund_method=='bank' and (not getattr(payload,'bank_account_id',None) or not db.get(BankAccount,payload.bank_account_id)): raise ValueError('Valid bank account is required for bank refunds.')
    elif total>sale_balance(db,sale): raise ValueError('Credit return exceeds the outstanding invoice balance.')
    ret=SalesReturn(sale_id=sale.id,customer_id=sale.customer_id,reason=payload.reason,total=total,refund_method=payload.refund_method,bank_account_id=getattr(payload,'bank_account_id',None))
    db.add(ret); db.flush()
    for product_id,base_qty,line_total,cost_total,meta in rows:
        db.add(SalesReturnItem(sales_return_id=ret.id,product_id=product_id,unit_id=meta.get('unit_id'),requested_quantity=meta.get('requested_quantity') or base_qty,quantity=base_qty,unit_price=(line_total/base_qty if base_qty else 0),cost_price=(cost_total/base_qty if base_qty else 0),line_total=line_total))
        product=db.get(Product,product_id); product.current_stock+=base_qty
        if sale.warehouse_id:
            b=db.query(StockBalance).filter_by(warehouse_id=sale.warehouse_id,product_id=product_id).with_for_update().first()
            if not b: b=StockBalance(warehouse_id=sale.warehouse_id,product_id=product_id,quantity=0,reorder_level=product.reorder_level or 0); db.add(b); db.flush()
            b.quantity+=base_qty
        db.add(StockMovement(product_id=product.id,movement_type='sales_return',quantity=base_qty,reference_type='sales_return',reference_id=ret.id))
    if payload.refund_method in ('cash','bank') and total: money_flow(db,-total,payload.refund_method,getattr(payload,'bank_account_id',None),'customer_refund','customer_refund',f'sales_return:{ret.id}','Sales return refund',user_id=user_id,shift_id=getattr(sale,'shift_id',None))
    audit(db,'sales_return_created','sales_return',ret.id,after={'sale_id':sale.id,'total':str(total),'refund_method':payload.refund_method},user_id=user_id)
    build_sales_return_journal(db,ret.id)
    queue_sync_event(db,'sales_return',ret.id,'created',{'sale_id':sale.id,'customer_id':sale.customer_id,'total':str(total)})
    db.commit(); db.refresh(ret); return ret


def purchase_return(db:Session,payload,user_id=None):
    purchase=db.get(Purchase,payload.purchase_id)
    if not purchase or purchase.status!='posted': raise ValueError('Posted purchase not found.')
    if not payload.items: raise ValueError('A return must contain at least one item.')
    original={}
    for i in db.query(PurchaseItem).filter(PurchaseItem.purchase_id==purchase.id).order_by(PurchaseItem.id).all(): original.setdefault(i.product_id,[]).append(i)
    returned={}
    old=db.query(PurchaseReturnItem).join(PurchaseReturn,PurchaseReturn.id==PurchaseReturnItem.purchase_return_id).filter(PurchaseReturn.purchase_id==purchase.id,PurchaseReturn.status=='posted').all()
    for r in old: returned[r.product_id]=returned.get(r.product_id,Decimal('0'))+Decimal(r.quantity)
    requested={}
    input_meta={}
    for row in payload.items:
        bq=_return_base_quantity(db,row.product_id,Decimal(row.quantity),getattr(row,'unit_id',None)); requested[row.product_id]=requested.get(row.product_id,Decimal('0'))+bq
        meta=input_meta.setdefault(row.product_id, {'unit_id': getattr(row,'unit_id',None), 'requested_quantity': Decimal('0')})
        if meta['unit_id'] == getattr(row,'unit_id',None):
            meta['requested_quantity'] += Decimal(row.quantity)
        else:
            meta['unit_id'] = None
            meta['requested_quantity'] = Decimal('0')
    total=Decimal('0'); rows=[]
    for product_id,base_qty in requested.items():
        candidates=original.get(product_id)
        if not candidates: raise ValueError(f'Product {product_id} was not on the purchase.')
        available=sum((Decimal(x.quantity)*_sold_factor(db,x) for x in candidates),Decimal('0'))-returned.get(product_id,Decimal('0'))
        if base_qty>available: raise ValueError('Return quantity exceeds purchased quantity.')
        product=db.get(Product,product_id)
        if Decimal(product.current_stock or 0)<base_qty: raise ValueError(f'Insufficient stock to return {product.name}.')
        remaining=base_qty; line_total=Decimal('0'); carrying_cost=Decimal('0')
        for item in candidates:
            factor=_sold_factor(db,item); available_base=Decimal(item.quantity)*factor; take_base=min(remaining,available_base)
            if take_base:
                sold_qty=take_base/factor; line_total+=sold_qty*Decimal(item.unit_price); carrying_cost+=take_base*Decimal(item.unit_price)/factor; remaining-=take_base
            if remaining<=0: break
        total+=line_total; meta=input_meta.get(product_id,{'unit_id':None,'requested_quantity':base_qty}); rows.append((product_id,base_qty,line_total,carrying_cost,product,meta))
    if payload.settlement_method not in ('cash','bank','credit'): raise ValueError('Settlement method must be cash, bank or credit.')
    if payload.settlement_method in ('cash','bank'):
        _, cash_refunds=_purchase_return_totals(db,purchase.id); available_refund=Decimal(purchase.paid or 0)-cash_refunds
        if total>available_refund: raise ValueError(f'Refund exceeds the amount actually paid and not already refunded ({available_refund}).')
    if payload.settlement_method=='bank' and (not getattr(payload,'bank_account_id',None) or not db.get(BankAccount,payload.bank_account_id)): raise ValueError('Valid bank account is required for bank refunds.')
    if payload.settlement_method=='credit' and total>purchase_balance(db,purchase): raise ValueError('Credit return exceeds the outstanding supplier balance.')
    ret=PurchaseReturn(purchase_id=purchase.id,supplier_id=purchase.supplier_id,reason=payload.reason,total=total,settlement_method=payload.settlement_method,bank_account_id=getattr(payload,'bank_account_id',None)); db.add(ret); db.flush()
    for product_id,base_qty,line_total,carrying_cost,product,meta in rows:
        db.add(PurchaseReturnItem(purchase_return_id=ret.id,product_id=product_id,unit_id=meta.get('unit_id'),requested_quantity=meta.get('requested_quantity') or base_qty,quantity=base_qty,unit_price=(line_total/base_qty if base_qty else 0),cost_price=(carrying_cost/base_qty if base_qty else 0),line_total=line_total))
        product.current_stock-=base_qty
        if purchase.warehouse_id:
            b=db.query(StockBalance).filter_by(warehouse_id=purchase.warehouse_id,product_id=product_id).with_for_update().first()
            if b: b.quantity-=base_qty
        db.add(StockMovement(product_id=product.id,movement_type='purchase_return',quantity=-base_qty,reference_type='purchase_return',reference_id=ret.id))
    if payload.settlement_method in ('cash','bank') and total: money_flow(db,total,payload.settlement_method,getattr(payload,'bank_account_id',None),'supplier_refund','supplier_refund',f'purchase_return:{ret.id}','Supplier return refund',user_id=user_id)
    audit(db,'purchase_return_created','purchase_return',ret.id,after={'purchase_id':purchase.id,'total':str(total),'settlement_method':payload.settlement_method},user_id=user_id)
    build_purchase_return_journal(db,ret.id)
    queue_sync_event(db,'purchase_return',ret.id,'created',{'purchase_id':purchase.id,'supplier_id':purchase.supplier_id,'total':str(total)})
    db.commit(); db.refresh(ret); return ret

def aging(db,side):
    """Return invoice-level debt plus any remaining opening balance, in one consistent ledger."""
    today=datetime.now(timezone.utc).astimezone(__import__('zoneinfo').ZoneInfo('Africa/Dar_es_Salaam')).date()
    if side=='customer':
        sql="""SELECT s.id,s.invoice_no,s.customer_id,c.name customer_name,s.sale_date,
            (s.total-COALESCE((SELECT SUM(r.total) FROM sales_returns r WHERE r.sale_id=s.id AND r.status='posted'),0)) total,
            (s.paid-COALESCE((SELECT SUM(r.total) FROM sales_returns r WHERE r.sale_id=s.id AND r.status='posted' AND r.refund_method IN ('cash','bank')),0)) paid,
            (s.total-COALESCE((SELECT SUM(r.total) FROM sales_returns r WHERE r.sale_id=s.id AND r.status='posted'),0)-s.paid+COALESCE((SELECT SUM(r.total) FROM sales_returns r WHERE r.sale_id=s.id AND r.status='posted' AND r.refund_method IN ('cash','bank')),0)) balance,
            (((s.sale_date AT TIME ZONE 'UTC' AT TIME ZONE 'Africa/Dar_es_Salaam')::date)+c.payment_terms_days) due_date
            FROM sales s JOIN customers c ON c.id=s.customer_id
            WHERE s.status='posted' AND s.customer_id IS NOT NULL
            AND (s.total-COALESCE((SELECT SUM(r.total) FROM sales_returns r WHERE r.sale_id=s.id AND r.status='posted'),0)-s.paid+COALESCE((SELECT SUM(r.total) FROM sales_returns r WHERE r.sale_id=s.id AND r.status='posted' AND r.refund_method IN ('cash','bank')),0))>0
            ORDER BY due_date,s.id"""
        rows=db.execute(text(sql)).mappings().all()
        out=[]
        for r in rows:
            d=dict(r); due=r['due_date']; days=(today-due).days
            d.update(total=str(r['total']),paid=str(r['paid']),balance=str(r['balance']),due_date=str(due),days_overdue=days,bucket='current' if days<=0 else '1-30' if days<=30 else '31-60' if days<=60 else '61-90' if days<=90 else '91-120' if days<=120 else '120+')
            out.append(d)
        opening=db.query(Customer).filter(Customer.opening_balance>0).all()
        for c in opening:
            bal=Decimal(c.opening_balance or 0)
            if bal>0:
                days=0; out.append({'id':None,'invoice_no':'OPENING-BALANCE','customer_id':c.id,'customer_name':c.name,'sale_date':None,'total':str(bal),'paid':'0.00','balance':str(bal),'due_date':str(today),'days_overdue':days,'bucket':'current'})
        return out
    sql="""SELECT p.id,p.invoice_no,p.supplier_id,s.name supplier_name,p.purchase_date,
        (p.total-COALESCE((SELECT SUM(r.total) FROM purchase_returns r WHERE r.purchase_id=p.id AND r.status='posted'),0)) total,
        (p.paid-COALESCE((SELECT SUM(r.total) FROM purchase_returns r WHERE r.purchase_id=p.id AND r.status='posted' AND r.settlement_method IN ('cash','bank')),0)) paid,
        (p.total-COALESCE((SELECT SUM(r.total) FROM purchase_returns r WHERE r.purchase_id=p.id AND r.status='posted'),0)-p.paid+COALESCE((SELECT SUM(r.total) FROM purchase_returns r WHERE r.purchase_id=p.id AND r.status='posted' AND r.settlement_method IN ('cash','bank')),0)) balance,
        (((p.purchase_date AT TIME ZONE 'UTC' AT TIME ZONE 'Africa/Dar_es_Salaam')::date)+s.payment_terms_days) due_date
        FROM purchases p JOIN suppliers s ON s.id=p.supplier_id
        WHERE p.status='posted' AND p.supplier_id IS NOT NULL
        AND (p.total-COALESCE((SELECT SUM(r.total) FROM purchase_returns r WHERE r.purchase_id=p.id AND r.status='posted'),0)-p.paid+COALESCE((SELECT SUM(r.total) FROM purchase_returns r WHERE r.purchase_id=p.id AND r.status='posted' AND r.settlement_method IN ('cash','bank')),0))>0
        ORDER BY due_date,p.id"""
    rows=db.execute(text(sql)).mappings().all(); out=[]
    for r in rows:
        d=dict(r); due=r['due_date']; days=(today-due).days
        d.update(total=str(r['total']),paid=str(r['paid']),balance=str(r['balance']),due_date=str(due),days_overdue=days,bucket='current' if days<=0 else '1-30' if days<=30 else '31-60' if days<=60 else '61-90' if days<=90 else '91-120' if days<=120 else '120+')
        out.append(d)
    opening=db.query(Supplier).filter(Supplier.opening_balance>0).all()
    for s in opening:
        bal=Decimal(s.opening_balance or 0)
        if bal>0: out.append({'id':None,'invoice_no':'OPENING-BALANCE','supplier_id':s.id,'supplier_name':s.name,'purchase_date':None,'total':str(bal),'paid':'0.00','balance':str(bal),'due_date':str(today),'days_overdue':0,'bucket':'current'})
    return out
