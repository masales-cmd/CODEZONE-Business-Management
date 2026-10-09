from datetime import datetime, date, timezone
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import func, inspect
from app.models.models import (
    Account, JournalEntry, JournalLine, Sale, SaleItem, Purchase, PurchaseItem,
    Expense, CustomerPayment, SupplierPayment, SalesReturn, SalesReturnItem,
    PurchaseReturn, PurchaseReturnItem, SalePayment, PaymentTransaction
)

DEFAULT_ACCOUNTS = [
    ('1000','Cash','asset'),('1010','Bank','asset'),('1020','Mobile Money','asset'),('1030','Card Clearing','asset'),('1040','Cheques Receivable','asset'),('1100','Accounts Receivable','asset'),
    ('3000',"Owner's Equity",'equity'),('1200','Inventory','asset'),('2000','Accounts Payable','liability'),
    ('4000','Sales Revenue','revenue'),('4010','Sales Returns','contra_revenue'),
    ('5000','Cost of Goods Sold','expense'),('5010','Purchase Returns','contra_expense'),('2100','Tax Payable','liability'),
    ('6000','Operating Expenses','expense'),('6100','Inventory Adjustments','expense'),
]

def seed_accounts(db: Session):
    for code,name,typ in DEFAULT_ACCOUNTS:
        if not db.query(Account).filter(Account.code==code).first():
            db.add(Account(code=code,name=name,account_type=typ))
    db.commit()

def account(db, code):
    a=db.query(Account).filter(Account.code==code).first()
    if not a: raise ValueError(f'Account {code} not configured.')
    return a

def post_journal(db, description, reference_type, reference_id, lines, entry_date=None):
    existing=db.query(JournalEntry).filter(
        JournalEntry.reference_type==reference_type,
        JournalEntry.reference_id==reference_id,
        JournalEntry.status=='posted'
    ).first()
    if existing:
        return existing
    debit=sum((Decimal(str(x.get('debit',0))) for x in lines), Decimal('0'))
    credit=sum((Decimal(str(x.get('credit',0))) for x in lines), Decimal('0'))
    if debit != credit:
        raise ValueError(f'Journal is not balanced: debit={debit} credit={credit}.')
    entry=JournalEntry(
        entry_date=entry_date or datetime.now(timezone.utc).replace(tzinfo=None),
        reference_type=reference_type, reference_id=reference_id,
        description=description, status='posted'
    )
    db.add(entry); db.flush()
    for x in lines:
        db.add(JournalLine(
            journal_entry_id=entry.id, account_id=x['account_id'],
            debit=x.get('debit',0), credit=x.get('credit',0),
            description=x.get('description')
        ))
    return entry

def build_sales_journal(db,sale_id):
    sale=db.get(Sale,sale_id)
    if not sale: raise ValueError('Sale not found.')
    ar=account(db,'1100'); revenue=account(db,'4000'); cogs=account(db,'5000'); inventory=account(db,'1200')
    tax_account=account(db,'2100') if Decimal(getattr(sale,'tax_amount',0) or 0) else None
    payment_rows=db.query(SalePayment).filter(SalePayment.sale_id==sale.id).all() if inspect(db.get_bind()).has_table('sale_payments') else []
    lines=[]
    paid=sum((Decimal(x.amount) for x in payment_rows),Decimal('0')) if payment_rows else Decimal(sale.paid or 0)
    payment_account_codes={'cash':'1000','bank':'1010','mobile_money':'1020','card':'1030','cheque':'1040'}
    if payment_rows:
        for x in payment_rows:
            if x.payment_method == 'credit':
                continue
            code=payment_account_codes.get(x.payment_method)
            if not code: raise ValueError(f'Unsupported payment method for accounting: {x.payment_method}')
            lines.append({'account_id':account(db,code).id,'debit':x.amount,'credit':0})
    elif paid:
        code=payment_account_codes.get(sale.payment_method,'1000')
        lines.append({'account_id':account(db,code).id,'debit':paid,'credit':0})
    credit=Decimal(sale.total or 0)-paid
    if credit: lines.append({'account_id':ar.id,'debit':credit,'credit':0})
    tax=Decimal(getattr(sale,'tax_amount',0) or 0)
    revenue_amount=Decimal(sale.total or 0)-tax
    if revenue_amount: lines.append({'account_id':revenue.id,'debit':0,'credit':revenue_amount})
    if tax and tax_account: lines.append({'account_id':tax_account.id,'debit':0,'credit':tax})
    cost=sum((Decimal(i.quantity)*Decimal(i.cost_price) for i in db.query(SaleItem).filter(SaleItem.sale_id==sale.id).all()),Decimal('0'))
    if cost: lines += [{'account_id':cogs.id,'debit':cost,'credit':0},{'account_id':inventory.id,'debit':0,'credit':cost}]
    return post_journal(db,'Sale '+sale.invoice_no,'sale',sale.id,lines,sale.sale_date)

def build_purchase_journal(db,purchase_id):
    purchase=db.get(Purchase,purchase_id)
    if not purchase: raise ValueError('Purchase not found.')
    inv=account(db,'1200'); ap=account(db,'2000')
    payment_account_codes={'cash':'1000','bank':'1010','mobile_money':'1020','card':'1030','cheque':'1040'}
    rows=db.query(PaymentTransaction).filter(PaymentTransaction.direction=='out',PaymentTransaction.reference==purchase.invoice_no).all()
    paid=sum((Decimal(x.amount) for x in rows),Decimal('0')) if rows else Decimal(purchase.paid or 0)
    lines=[{'account_id':inv.id,'debit':purchase.total,'credit':0}]
    if rows:
        for x in rows:
            code=payment_account_codes.get(x.payment_method)
            if not code: raise ValueError(f'Unsupported purchase payment method: {x.payment_method}')
            lines.append({'account_id':account(db,code).id,'debit':0,'credit':x.amount})
    elif paid:
        code=payment_account_codes.get(purchase.payment_method,'1000')
        lines.append({'account_id':account(db,code).id,'debit':0,'credit':paid})
    credit=Decimal(purchase.total or 0)-paid
    if credit: lines.append({'account_id':ap.id,'debit':0,'credit':credit})
    return post_journal(db,'Purchase '+purchase.invoice_no,'purchase',purchase.id,lines,purchase.purchase_date)

def build_expense_journal(db,expense_id):
    e=db.get(Expense,expense_id)
    if not e: raise ValueError('Expense not found.')
    expense=account(db,'6000'); credit=account(db,{'cash':'1000','bank':'1010','mobile_money':'1020','card':'1030','cheque':'1040'}.get(e.payment_method,'1000'))
    return post_journal(db,'Expense '+str(e.id),'expense',e.id,[
        {'account_id':expense.id,'debit':e.amount,'credit':0},
        {'account_id':credit.id,'debit':0,'credit':e.amount}
    ],datetime.combine(e.expense_date,datetime.min.time()) if e.expense_date else None)

def build_customer_payment_journal(db,payment_id):
    p=db.get(CustomerPayment,payment_id)
    if not p: raise ValueError('Customer payment not found.')
    cash=account(db,{'cash':'1000','bank':'1010','mobile_money':'1020','card':'1030','cheque':'1040'}.get(p.payment_method,'1000')); ar=account(db,'1100')
    return post_journal(db,'Customer payment '+str(p.id),'customer_payment',p.id,[
        {'account_id':cash.id,'debit':p.amount,'credit':0},
        {'account_id':ar.id,'debit':0,'credit':p.amount}
    ],p.payment_date)

def build_supplier_payment_journal(db,payment_id):
    p=db.get(SupplierPayment,payment_id)
    if not p: raise ValueError('Supplier payment not found.')
    cash=account(db,{'cash':'1000','bank':'1010','mobile_money':'1020','card':'1030','cheque':'1040'}.get(p.payment_method,'1000')); ap=account(db,'2000')
    return post_journal(db,'Supplier payment '+str(p.id),'supplier_payment',p.id,[
        {'account_id':ap.id,'debit':p.amount,'credit':0},
        {'account_id':cash.id,'debit':0,'credit':p.amount}
    ],p.payment_date)

def build_sales_return_journal(db,return_id):
    r=db.get(SalesReturn,return_id)
    if not r: raise ValueError('Sales return not found.')
    rev=account(db,'4010'); ar=account(db,'1100')
    settlement = ar
    if r.refund_method=='cash': settlement=account(db,'1000')
    elif r.refund_method=='bank': settlement=account(db,'1010')
    lines=[
        {'account_id':rev.id,'debit':r.total,'credit':0},
        {'account_id':settlement.id,'debit':0,'credit':r.total}
    ]
    returned_cost=sum((Decimal(ri.quantity)*Decimal(ri.cost_price or 0)
                       for ri in db.query(SalesReturnItem).filter(SalesReturnItem.sales_return_id==r.id).all()),Decimal('0'))
    if returned_cost:
        lines += [
            {'account_id':account(db,'1200').id,'debit':returned_cost,'credit':0},
            {'account_id':account(db,'5000').id,'debit':0,'credit':returned_cost}
        ]
    return post_journal(db,'Sales return '+str(r.id),'sales_return',r.id,lines,r.return_date)

def build_purchase_return_journal(db,return_id):
    r=db.get(PurchaseReturn,return_id)
    if not r: raise ValueError('Purchase return not found.')
    inv=account(db,'1200'); ap=account(db,'2000')
    target=ap if r.settlement_method=='credit' else account(db,'1000' if r.settlement_method=='cash' else '1010')
    carrying_cost=sum((Decimal(i.quantity)*Decimal(i.cost_price or 0) for i in db.query(PurchaseReturnItem).filter(PurchaseReturnItem.purchase_return_id==r.id).all()),Decimal('0'))
    variance=Decimal(r.total or 0)-carrying_cost
    lines=[{'account_id':target.id,'debit':r.total,'credit':0}, {'account_id':inv.id,'debit':0,'credit':carrying_cost}]
    variance_account=account(db,'5010')
    if variance>0:
        lines.append({'account_id':variance_account.id,'debit':0,'credit':variance})
    elif variance<0:
        lines.append({'account_id':variance_account.id,'debit':-variance,'credit':0})
    return post_journal(db,'Purchase return '+str(r.id),'purchase_return',r.id,lines,r.return_date)

def trial_balance(db,start:date|None=None,end:date|None=None):
    q=db.query(
        Account.code,Account.name,Account.account_type,
        func.coalesce(func.sum(JournalLine.debit),0).label('debit'),
        func.coalesce(func.sum(JournalLine.credit),0).label('credit')
    ).join(JournalLine,JournalLine.account_id==Account.id).join(
        JournalEntry,JournalEntry.id==JournalLine.journal_entry_id
    ).filter(JournalEntry.status=='posted')
    if db.bind and db.bind.dialect.name == 'postgresql':
        # Journal dates are stored as naive UTC timestamps; reports use Tanzania/EAT business dates.
        local_date = func.date(func.timezone('Africa/Dar_es_Salaam', func.timezone('UTC', JournalEntry.entry_date)))
        if start: q=q.filter(local_date>=start)
        if end: q=q.filter(local_date<=end)
    else:
        if start: q=q.filter(JournalEntry.entry_date>=datetime.combine(start,datetime.min.time()))
        if end: q=q.filter(JournalEntry.entry_date<datetime.combine(end,datetime.max.time()))
    rows=q.group_by(Account.code,Account.name,Account.account_type).order_by(Account.code).all()
    return [{'code':r.code,'name':r.name,'account_type':r.account_type,'debit':str(r.debit),'credit':str(r.credit),'net':str(Decimal(r.debit)-Decimal(r.credit))} for r in rows]

def profit_loss(db,start:date,end:date):
    rows=trial_balance(db,start,end)
    revenue=returns=cogs=expenses=purchase_return_variance=Decimal('0')
    for r in rows:
        n=Decimal(r['net'])
        if r['account_type']=='revenue': revenue += -n
        elif r['account_type']=='contra_revenue': returns += n
        elif r['code']=='5000': cogs += n
        elif r['code']=='5010': purchase_return_variance += n
        elif r['code']=='6000': expenses += n
    cogs += purchase_return_variance
    gross=revenue-returns-cogs
    return {'period':{'start':str(start),'end':str(end)},'sales_revenue':str(revenue),'sales_returns':str(returns),'purchase_return_variance':str(purchase_return_variance),
            'net_revenue':str(revenue-returns),'cogs':str(cogs),'gross_profit':str(gross),
            'operating_expenses':str(expenses),'net_profit':str(gross-expenses)}
