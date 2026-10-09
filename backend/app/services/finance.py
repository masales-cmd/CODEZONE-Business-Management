from datetime import datetime, date
from decimal import Decimal
from sqlalchemy.orm import Session
from app.models.models import (
    Purchase,PurchaseItem,Product,StockMovement,Expense,CashTransaction,BankTransaction,BankAccount,
    ProductUnitConversion, StockBalance, Warehouse, ProductBatch, PaymentTransaction
)
from app.services.transactions import audit, ensure_cash_available, active_shift_id
from app.services.accounting import build_purchase_journal,build_expense_journal
from app.services.sync import queue_sync_event


def create_purchase(db:Session,payload,user_id=None):
    if not payload.items: raise ValueError('A purchase must contain at least one item.')
    invoice_no=payload.invoice_no.strip()
    if not invoice_no: raise ValueError('Purchase invoice number is required.')
    dup=db.query(Purchase).filter(Purchase.invoice_no==invoice_no)
    if payload.supplier_id is None: dup=dup.filter(Purchase.supplier_id.is_(None))
    else: dup=dup.filter(Purchase.supplier_id==payload.supplier_id)
    if dup.first(): raise ValueError('Purchase invoice number already exists for this supplier.')

    rows=[]; total=Decimal('0')
    for item in payload.items:
        product=db.query(Product).filter(Product.id==item.product_id).with_for_update().first()
        if not product or not product.is_active: raise ValueError(f'Product {item.product_id} is not available for purchase.')
        factor=Decimal('1')
        if item.unit_id and product.base_unit_id and item.unit_id != product.base_unit_id:
            conv=db.query(ProductUnitConversion).filter_by(product_id=product.id,from_unit_id=item.unit_id,to_unit_id=product.base_unit_id).first()
            if not conv: raise ValueError(f'No unit conversion configured for {product.name}.')
            factor=Decimal(conv.factor)
        base_qty=Decimal(item.quantity)*factor
        line=Decimal(item.quantity)*Decimal(item.unit_price); total+=line
        rows.append((item,product,line,base_qty,factor))

    payments=list(payload.payments or [])
    if payments:
        paid=sum((Decimal(x.amount) for x in payments),Decimal('0'))
        if payload.paid and Decimal(payload.paid)!=paid: raise ValueError('The explicit paid amount does not match payment lines.')
    else:
        paid=Decimal(payload.paid)
        payments=[] if paid==0 else [type('PaymentLine',(),{'payment_method':payload.payment_method,'amount':paid,'bank_account_id':payload.bank_account_id,'reference':payload.reference,'external_reference':None})()]
    if paid>total: raise ValueError('Paid amount cannot exceed purchase total.')
    if total>paid and not payload.supplier_id: raise ValueError('Credit or partial purchase requires a supplier.')
    for pl in payments:
        if pl.payment_method=='bank' and (not pl.bank_account_id or not db.get(BankAccount,pl.bank_account_id)): raise ValueError('Bank account not found.')
        if pl.payment_method=='credit': raise ValueError('Credit is the unpaid balance, not a payment line.')
        if pl.payment_method not in ('cash','bank','mobile_money','card','cheque'): raise ValueError('Unsupported purchase payment method.')

    methods={x.payment_method for x in payments}
    payment_method=next(iter(methods)) if len(methods)==1 else ('split' if methods else 'credit')
    purchase=Purchase(invoice_no=invoice_no,supplier_id=payload.supplier_id,total=total,paid=paid,
                       payment_method=payment_method,bank_account_id=payload.bank_account_id,
                       branch_id=payload.branch_id,warehouse_id=payload.warehouse_id)
    db.add(purchase); db.flush()

    for item,product,line,base_qty,factor in rows:
        db.add(PurchaseItem(purchase_id=purchase.id,product_id=product.id,unit_id=item.unit_id,quantity=item.quantity,unit_price=item.unit_price,line_total=line))
        if getattr(product,'track_inventory',True):
            if payload.warehouse_id:
                warehouse=db.get(Warehouse,payload.warehouse_id)
                if not warehouse: raise ValueError('Warehouse not found.')
                bal=db.query(StockBalance).filter_by(warehouse_id=payload.warehouse_id,product_id=product.id).with_for_update().first()
                if not bal:
                    bal=StockBalance(warehouse_id=payload.warehouse_id,product_id=product.id,quantity=0,reorder_level=0); db.add(bal); db.flush()
                bal.quantity=Decimal(bal.quantity or 0)+base_qty
            old_stock=Decimal(product.current_stock or 0); old_cost=Decimal(product.buying_price or 0)
            new_stock=old_stock+base_qty
            product.current_stock=new_stock
            base_purchase_cost=Decimal(item.unit_price)/factor
            product.buying_price=((old_stock*old_cost)+(base_qty*base_purchase_cost))/new_stock if new_stock else base_purchase_cost
            db.add(StockMovement(product_id=product.id,movement_type='purchase',quantity=base_qty,reference_type='purchase',reference_id=purchase.id))
            if getattr(item,'batch_no',None):
                existing=db.query(ProductBatch).filter(ProductBatch.product_id==product.id,ProductBatch.batch_no==item.batch_no).with_for_update().first()
                expiry=date.fromisoformat(item.expiry_date) if getattr(item,'expiry_date',None) else None
                if existing: existing.quantity=Decimal(existing.quantity or 0)+base_qty; existing.cost_price=base_purchase_cost; existing.expiry_date=expiry
                else: db.add(ProductBatch(product_id=product.id,batch_no=item.batch_no,expiry_date=expiry,quantity=base_qty,cost_price=base_purchase_cost))

    for pl in payments:
        method=pl.payment_method
        if method=='cash':
            ensure_cash_available(db,Decimal(pl.amount))
            db.add(CashTransaction(transaction_type='supplier_payment',amount=-Decimal(pl.amount),reference=pl.reference or invoice_no,description='Purchase payment',shift_id=active_shift_id(db,user_id)))
        elif method=='bank':
            db.add(BankTransaction(bank_account_id=pl.bank_account_id,transaction_type='supplier_payment',amount=-Decimal(pl.amount),reference=pl.reference or invoice_no,description='Purchase payment'))
        else:
            db.add(PaymentTransaction(direction='out',payment_method=method,amount=pl.amount,reference=pl.reference or invoice_no,external_reference=getattr(pl,'external_reference',None),bank_account_id=pl.bank_account_id))
    audit(db,'purchase_created','purchase',purchase.id,after={'invoice_no':purchase.invoice_no,'total':str(total),'paid':str(paid),'warehouse_id':payload.warehouse_id},user_id=user_id)
    build_purchase_journal(db,purchase.id)
    queue_sync_event(db,'purchase',purchase.id,'created',{'invoice_no':purchase.invoice_no,'supplier_id':purchase.supplier_id,'total':str(total),'paid':str(paid),'warehouse_id':payload.warehouse_id})
    db.commit(); db.refresh(purchase); return purchase


def create_expense(db:Session,category,amount,payment_method,description=None,expense_date=None,bank_account_id=None,reference=None,user_id=None):
    if amount<=0: raise ValueError('Expense amount must be greater than zero.')
    if payment_method not in ('cash','bank','mobile_money','card','cheque'): raise ValueError('Unsupported expense payment method.')
    if payment_method=='bank' and not db.get(BankAccount,bank_account_id): raise ValueError('Bank account not found.')
    expense=Expense(category=category,amount=amount,payment_method=payment_method,description=description,expense_date=expense_date); db.add(expense); db.flush()
    if payment_method=='cash':
        ensure_cash_available(db,amount)
        db.add(CashTransaction(transaction_type='expense',amount=-amount,reference=reference or f'expense:{expense.id}',description=description,shift_id=active_shift_id(db,user_id)))
    elif payment_method=='bank':
        db.add(BankTransaction(bank_account_id=bank_account_id,transaction_type='expense',amount=-amount,reference=reference or f'expense:{expense.id}',description=description))
    else:
        db.add(PaymentTransaction(direction='out',payment_method=payment_method,amount=amount,reference=reference or f'expense:{expense.id}',bank_account_id=bank_account_id))
    audit(db,'expense_created','expense',expense.id,after={'category':category,'amount':str(amount),'payment_method':payment_method},user_id=user_id)
    build_expense_journal(db,expense.id)
    queue_sync_event(db,'expense',expense.id,'created',{'category':category,'amount':str(amount),'payment_method':payment_method})
    db.commit(); db.refresh(expense); return expense
