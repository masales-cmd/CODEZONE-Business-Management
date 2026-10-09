from datetime import date, datetime, timedelta
from decimal import Decimal
from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.models.models import Sale, SaleItem, Product, Customer, CustomerPayment
from app.core.database import get_db
from app.core.security import current_user, require_roles
from app.schemas.finance import PurchaseCreate,ExpenseCreate,CustomerPaymentCreate,SupplierPaymentCreate,SalesReturnCreate,PurchaseReturnCreate,PurchaseOrderCreate,PurchaseOrderStatusUpdate,PurchaseOrderReceiveIn
from app.schemas.common import QuoteCreate, QuoteStatusUpdate, OrderStatusUpdate, OrderConvertToSale
from app.services.sales_pipeline import create_quote, convert_quote_to_order, convert_order_to_sale
from app.services.finance import create_purchase,create_expense
from app.services.transactions import record_customer_payment,record_supplier_payment,sales_return,purchase_return,aging,sale_balance,audit
router=APIRouter(prefix='/api',tags=['business'],dependencies=[Depends(current_user)])
def call(fn,db,payload):
    try:return fn(db,payload)
    except ValueError as e: raise HTTPException(400,str(e))

@router.get('/sales')
def sales_history(start_date: str|None=None, end_date: str|None=None, customer_id: int|None=None, status: str|None=None, q: str|None=None, db: Session=Depends(get_db), _: object=Depends(require_roles('owner','admin','manager','salesperson','cashier','accountant'))):
    query=db.query(Sale, Customer.name.label('customer_name')).outerjoin(Customer, Customer.id==Sale.customer_id)
    if start_date: query=query.filter(Sale.sale_date>=date.fromisoformat(start_date))
    if end_date: query=query.filter(Sale.sale_date < date.fromisoformat(end_date) + timedelta(days=1))
    if customer_id: query=query.filter(Sale.customer_id==customer_id)
    if q:
        like=f'%{q.strip()}%'
        query=query.filter(or_(Sale.invoice_no.ilike(like), Customer.name.ilike(like)))
    rows=[]
    for sale,customer_name in query.order_by(Sale.sale_date.desc(),Sale.id.desc()).limit(1000).all():
        balance=max(sale_balance(db,sale), Decimal('0'))
        paid=Decimal(str(sale.paid))
        payment_status='PAID' if balance<=0 else ('CREDIT' if paid<=0 else 'PARTIAL')
        if status and payment_status!=status.upper(): continue
        rows.append({'id':sale.id,'invoice_no':sale.invoice_no,'sale_date':sale.sale_date.isoformat()+'Z','customer_id':sale.customer_id,'customer_name':customer_name or 'Walk-in','subtotal':str(sale.subtotal),'discount':str(sale.discount),'total':str(sale.total),'paid':str(sale.paid),'balance':str(balance),'payment_method':sale.payment_method,'status':sale.status,'payment_status':payment_status})
    return rows

@router.get('/sales/{sale_id}')
def sale_detail(sale_id: int, db: Session=Depends(get_db), _: object=Depends(require_roles('owner','admin','manager','salesperson','cashier','accountant'))):
    sale=db.get(Sale,sale_id)
    if not sale: raise HTTPException(404,'Sale not found.')
    customer=db.get(Customer,sale.customer_id) if sale.customer_id else None
    items=[]
    for row in db.query(SaleItem).filter(SaleItem.sale_id==sale.id).all():
        product=db.get(Product,row.product_id)
        items.append({'product_id':row.product_id,'product_name':product.name if product else 'Unknown','sku':product.sku if product else '', 'quantity':str(row.quantity),'unit_price':str(row.unit_price),'line_total':str(row.line_total)})
    balance=max(sale_balance(db,sale),Decimal('0'))
    paid=Decimal(str(sale.paid)); payment_status='PAID' if balance<=0 else ('CREDIT' if paid<=0 else 'PARTIAL')
    return {'id':sale.id,'invoice_no':sale.invoice_no,'sale_date':sale.sale_date.isoformat()+'Z','customer_id':sale.customer_id,'customer_name':customer.name if customer else 'Walk-in','customer_phone':customer.phone if customer else None,'subtotal':str(sale.subtotal),'discount':str(sale.discount),'total':str(sale.total),'paid':str(sale.paid),'balance':str(balance),'payment_method':sale.payment_method,'status':sale.status,'payment_status':payment_status,'items':items}

@router.get('/customer-payments')
def customer_payments_history(customer_id: int|None=None, sale_id: int|None=None, db: Session=Depends(get_db), _: object=Depends(require_roles('owner','admin','manager','salesperson','cashier','accountant'))):
    query=db.query(CustomerPayment, Customer.name.label('customer_name')).join(Customer,Customer.id==CustomerPayment.customer_id)
    if customer_id: query=query.filter(CustomerPayment.customer_id==customer_id)
    if sale_id: query=query.filter(CustomerPayment.sale_id==sale_id)
    return [{'id':p.id,'customer_id':p.customer_id,'customer_name':name,'sale_id':p.sale_id,'amount':str(p.amount),'payment_method':p.payment_method,'reference':p.reference,'notes':p.notes,'payment_date':p.payment_date.isoformat()+'Z'} for p,name in query.order_by(CustomerPayment.payment_date.desc(),CustomerPayment.id.desc()).limit(1000).all()]

@router.get('/purchases')
def purchase_history(start_date: str|None=None, end_date: str|None=None, supplier_id: int|None=None, q: str|None=None, db: Session=Depends(get_db), _: object=Depends(require_roles('owner','admin','manager','storekeeper','accountant'))):
    from app.models.models import Purchase,PurchaseItem,Supplier,Product
    query=db.query(Purchase,Supplier.name.label('supplier_name')).outerjoin(Supplier,Supplier.id==Purchase.supplier_id).filter(Purchase.status=='posted')
    if start_date: query=query.filter(Purchase.purchase_date>=date.fromisoformat(start_date))
    if end_date: query=query.filter(Purchase.purchase_date < date.fromisoformat(end_date)+timedelta(days=1))
    if supplier_id: query=query.filter(Purchase.supplier_id==supplier_id)
    if q: query=query.filter(or_(Purchase.invoice_no.ilike(f'%{q.strip()}%'),Supplier.name.ilike(f'%{q.strip()}%')))
    out=[]
    from app.services.transactions import purchase_balance
    for pur,name in query.order_by(Purchase.purchase_date.desc(),Purchase.id.desc()).limit(1000).all():
        balance=max(purchase_balance(db,pur),Decimal('0'))
        out.append({'id':pur.id,'invoice_no':pur.invoice_no,'purchase_date':pur.purchase_date.isoformat()+'Z','supplier_id':pur.supplier_id,'supplier_name':name or 'Walk-in supplier','total':str(pur.total),'paid':str(pur.paid),'balance':str(balance),'payment_method':pur.payment_method})
    return out

@router.get('/purchases/{purchase_id}')
def purchase_detail(purchase_id:int,db:Session=Depends(get_db),_:object=Depends(require_roles('owner','admin','manager','storekeeper','accountant'))):
    from app.models.models import Purchase,PurchaseItem,Product,Supplier
    pur=db.get(Purchase,purchase_id)
    if not pur: raise HTTPException(404,'Purchase not found.')
    supplier=db.get(Supplier,pur.supplier_id) if pur.supplier_id else None
    items=[]
    for row in db.query(PurchaseItem).filter(PurchaseItem.purchase_id==pur.id).all():
        product=db.get(Product,row.product_id)
        items.append({'product_id':row.product_id,'product_name':product.name if product else 'Unknown','sku':product.sku if product else '','quantity':str(row.quantity),'unit_price':str(row.unit_price),'line_total':str(row.line_total)})
    from app.services.transactions import purchase_balance
    return {'id':pur.id,'invoice_no':pur.invoice_no,'purchase_date':pur.purchase_date.isoformat()+'Z','supplier_id':pur.supplier_id,'supplier_name':supplier.name if supplier else 'Walk-in supplier','total':str(pur.total),'paid':str(pur.paid),'balance':str(max(purchase_balance(db,pur),Decimal('0'))),'payment_method':pur.payment_method,'items':items}

@router.post('/purchases')
def purchases(payload:PurchaseCreate,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','manager','storekeeper','accountant'))): return call(lambda d,p:create_purchase(d,p,user.id),db,payload)
@router.post('/expenses')
def expenses(payload:ExpenseCreate,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','manager','accountant'))):
    try:return create_expense(db,payload.category,payload.amount,payload.payment_method,payload.description,date.fromisoformat(payload.expense_date) if payload.expense_date else date.today(),payload.bank_account_id,payload.reference,user.id)
    except ValueError as e: raise HTTPException(400,str(e))
@router.post('/customer-payments')
def customer_payments(payload:CustomerPaymentCreate,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','manager','accountant','cashier','salesperson'))): return call(lambda d,p:record_customer_payment(d,p,user.id),db,payload)

@router.get('/supplier-payments')
def supplier_payments_history(supplier_id:int|None=None,purchase_id:int|None=None,db:Session=Depends(get_db),_:object=Depends(require_roles('owner','admin','manager','accountant'))):
    query=db.query(SupplierPayment,Supplier.name.label('supplier_name')).join(Supplier,Supplier.id==SupplierPayment.supplier_id)
    if supplier_id: query=query.filter(SupplierPayment.supplier_id==supplier_id)
    if purchase_id: query=query.filter(SupplierPayment.purchase_id==purchase_id)
    return [{'id':p.id,'supplier_id':p.supplier_id,'supplier_name':name,'purchase_id':p.purchase_id,'amount':str(p.amount),'payment_method':p.payment_method,'reference':p.reference,'notes':p.notes,'payment_date':p.payment_date.isoformat()+'Z'} for p,name in query.order_by(SupplierPayment.payment_date.desc(),SupplierPayment.id.desc()).limit(2000).all()]

@router.get('/sales-returns')
def sales_returns_history(db:Session=Depends(get_db),_:object=Depends(require_roles('owner','admin','manager','cashier'))):
    rows=db.query(SalesReturn,Sale.invoice_no).join(Sale,Sale.id==SalesReturn.sale_id).order_by(SalesReturn.return_date.desc(),SalesReturn.id.desc()).limit(2000).all()
    return [{'id':r.id,'sale_id':r.sale_id,'invoice_no':invoice,'customer_id':r.customer_id,'reason':r.reason,'total':str(r.total),'refund_method':r.refund_method,'status':r.status,'return_date':r.return_date.isoformat()+'Z'} for r,invoice in rows]

@router.get('/purchase-returns')
def purchase_returns_history(db:Session=Depends(get_db),_:object=Depends(require_roles('owner','admin','manager','storekeeper'))):
    rows=db.query(PurchaseReturn,Purchase.invoice_no).join(Purchase,Purchase.id==PurchaseReturn.purchase_id).order_by(PurchaseReturn.return_date.desc(),PurchaseReturn.id.desc()).limit(2000).all()
    return [{'id':r.id,'purchase_id':r.purchase_id,'invoice_no':invoice,'supplier_id':r.supplier_id,'reason':r.reason,'total':str(r.total),'settlement_method':r.settlement_method,'status':r.status,'return_date':r.return_date.isoformat()+'Z'} for r,invoice in rows]

@router.post('/supplier-payments')
def supplier_payments(payload:SupplierPaymentCreate,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','accountant'))): return call(lambda d,p:record_supplier_payment(d,p,user.id),db,payload)
@router.post('/sales-returns')
def sales_returns(payload:SalesReturnCreate,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','manager','cashier'))): return call(lambda d,p:sales_return(d,p,user.id),db,payload)
@router.post('/purchase-returns')
def purchase_returns(payload:PurchaseReturnCreate,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','manager','storekeeper'))): return call(lambda d,p:purchase_return(d,p,user.id),db,payload)
@router.get('/reports/customer-aging')
def customer_aging(db:Session=Depends(get_db), _: object=Depends(require_roles('owner','admin','manager','accountant','salesperson','cashier'))): return aging(db,'customer')
@router.get('/reports/supplier-aging')
def supplier_aging(db:Session=Depends(get_db), _: object=Depends(require_roles('owner','admin','manager','accountant','storekeeper'))): return aging(db,'supplier')


@router.get('/quotes')
def quotes_history(status: str|None=None, customer_id: int|None=None, q: str|None=None, db: Session=Depends(get_db), _: object=Depends(require_roles('owner','admin','manager','salesperson','cashier'))):
    from app.models.models import SalesQuote, Customer
    query=db.query(SalesQuote, Customer.name.label('customer_name')).outerjoin(Customer, Customer.id==SalesQuote.customer_id)
    if status: query=query.filter(SalesQuote.status==status)
    if customer_id: query=query.filter(SalesQuote.customer_id==customer_id)
    if q:
        like=f'%{q.strip()}%'
        query=query.filter(or_(SalesQuote.quote_no.ilike(like), Customer.name.ilike(like)))
    rows=[]
    for row,name in query.order_by(SalesQuote.issue_date.desc(),SalesQuote.id.desc()).limit(1000).all():
        rows.append({'id':row.id,'quote_no':row.quote_no,'customer_id':row.customer_id,'customer_name':name or 'Walk-in','issue_date':row.issue_date.isoformat()+'Z','valid_until':row.valid_until.isoformat() if row.valid_until else None,'subtotal':str(row.subtotal),'discount':str(row.discount),'total':str(row.total),'status':row.status,'notes':row.notes})
    return rows

@router.post('/quotes')
def create_sales_quote(payload: QuoteCreate, db: Session=Depends(get_db), user=Depends(require_roles('owner','admin','manager','salesperson','cashier'))):
    try:
        quote=create_quote(db,payload,user.id)
        return {'id':quote.id,'quote_no':quote.quote_no,'total':str(quote.total),'status':quote.status}
    except ValueError as exc:
        raise HTTPException(400,str(exc))

@router.get('/quotes/{quote_id}')
def quote_detail(quote_id:int, db:Session=Depends(get_db), _:object=Depends(require_roles('owner','admin','manager','salesperson','cashier'))):
    from app.models.models import SalesQuote, SalesQuoteItem, Customer, Product
    quote=db.get(SalesQuote,quote_id)
    if not quote: raise HTTPException(404,'Quote not found.')
    customer=db.get(Customer,quote.customer_id) if quote.customer_id else None
    items=[]
    for i in db.query(SalesQuoteItem).filter(SalesQuoteItem.quote_id==quote.id).all():
        product=db.get(Product,i.product_id)
        items.append({'product_id':i.product_id,'product_name':product.name if product else 'Unknown','quantity':str(i.quantity),'unit_price':str(i.unit_price),'line_total':str(i.line_total),'unit_id':i.unit_id})
    return {'id':quote.id,'quote_no':quote.quote_no,'customer_id':quote.customer_id,'customer_name':customer.name if customer else 'Walk-in','issue_date':quote.issue_date.isoformat()+'Z','valid_until':quote.valid_until.isoformat() if quote.valid_until else None,'subtotal':str(quote.subtotal),'discount':str(quote.discount),'total':str(quote.total),'status':quote.status,'notes':quote.notes,'items':items}

@router.put('/quotes/{quote_id}/status')
def update_quote_status(quote_id:int,payload:QuoteStatusUpdate,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','manager','salesperson'))):
    from app.models.models import SalesQuote
    quote=db.get(SalesQuote,quote_id)
    if not quote: raise HTTPException(404,'Quote not found.')
    if quote.status in ('accepted','rejected','expired') and payload.status=='draft': raise HTTPException(400,'A finalized quote cannot return to draft.')
    quote.status=payload.status
    db.commit()
    return {'id':quote.id,'status':quote.status}

@router.post('/quotes/{quote_id}/convert-to-order')
def quote_to_order(quote_id:int,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','manager','salesperson'))):
    try:
        order=convert_quote_to_order(db,quote_id,user.id)
        return {'id':order.id,'order_no':order.order_no,'quote_id':order.quote_id,'status':order.status,'total':str(order.total)}
    except ValueError as exc:
        raise HTTPException(400,str(exc))

@router.get('/sales-orders')
def sales_orders_history(status: str|None=None, customer_id:int|None=None, q:str|None=None, db:Session=Depends(get_db), _:object=Depends(require_roles('owner','admin','manager','salesperson','cashier'))):
    from app.models.models import SalesOrder, Customer
    query=db.query(SalesOrder,Customer.name.label('customer_name')).outerjoin(Customer,Customer.id==SalesOrder.customer_id)
    if status: query=query.filter(SalesOrder.status==status)
    if customer_id: query=query.filter(SalesOrder.customer_id==customer_id)
    if q:
        like=f'%{q.strip()}%'
        query=query.filter(or_(SalesOrder.order_no.ilike(like),Customer.name.ilike(like)))
    return [{'id':o.id,'order_no':o.order_no,'quote_id':o.quote_id,'customer_id':o.customer_id,'customer_name':name or 'Walk-in','order_date':o.order_date.isoformat()+'Z','subtotal':str(o.subtotal),'discount':str(o.discount),'total':str(o.total),'status':o.status,'notes':o.notes} for o,name in query.order_by(SalesOrder.order_date.desc(),SalesOrder.id.desc()).limit(1000).all()]

@router.get('/sales-orders/{order_id}')
def sales_order_detail(order_id:int,db:Session=Depends(get_db),_:object=Depends(require_roles('owner','admin','manager','salesperson','cashier'))):
    from app.models.models import SalesOrder, SalesOrderItem, Customer, Product
    order=db.get(SalesOrder,order_id)
    if not order: raise HTTPException(404,'Sales order not found.')
    customer=db.get(Customer,order.customer_id) if order.customer_id else None
    items=[]
    for i in db.query(SalesOrderItem).filter(SalesOrderItem.order_id==order.id).all():
        product=db.get(Product,i.product_id)
        items.append({'product_id':i.product_id,'product_name':product.name if product else 'Unknown','quantity':str(i.quantity),'unit_price':str(i.unit_price),'line_total':str(i.line_total),'unit_id':i.unit_id})
    return {'id':order.id,'order_no':order.order_no,'quote_id':order.quote_id,'customer_id':order.customer_id,'customer_name':customer.name if customer else 'Walk-in','order_date':order.order_date.isoformat()+'Z','subtotal':str(order.subtotal),'discount':str(order.discount),'total':str(order.total),'status':order.status,'notes':order.notes,'items':items}

@router.put('/sales-orders/{order_id}/status')
def update_sales_order_status(order_id:int,payload:OrderStatusUpdate,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','manager'))):
    from app.models.models import SalesOrder
    order=db.get(SalesOrder,order_id)
    if not order: raise HTTPException(404,'Sales order not found.')
    if order.status=='fulfilled' and payload.status!='fulfilled': raise HTTPException(400,'A fulfilled order cannot be moved backwards.')
    order.status=payload.status
    db.commit()
    return {'id':order.id,'status':order.status}

@router.post('/sales-orders/{order_id}/convert-to-sale')
def sales_order_to_sale(order_id:int,payload:OrderConvertToSale,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','manager','salesperson','cashier'))):
    try:
        sale=convert_order_to_sale(db,order_id,payload,user.id)
        return {'id':sale.id,'invoice_no':sale.invoice_no,'total':str(sale.total),'paid':str(sale.paid),'status':sale.status}
    except ValueError as exc:
        raise HTTPException(400,str(exc))

@router.get('/customers/{customer_id}/360')
def customer_360(customer_id:int,db:Session=Depends(get_db),_:object=Depends(require_roles('owner','admin','manager','salesperson','cashier','accountant'))):
    from sqlalchemy import func
    from app.models.models import Sale, SaleItem, Product, CustomerPayment, SalePayment, SalesReturn
    customer=db.get(Customer,customer_id)
    if not customer: raise HTTPException(404,'Customer not found.')
    sales=db.query(Sale).filter(Sale.customer_id==customer.id,Sale.status=='posted').order_by(Sale.sale_date.desc(),Sale.id.desc()).all()
    payments=db.query(CustomerPayment).filter(CustomerPayment.customer_id==customer.id).order_by(CustomerPayment.payment_date.desc(),CustomerPayment.id.desc()).all()
    returns=db.query(SalesReturn).filter(SalesReturn.customer_id==customer.id,SalesReturn.status=='posted').order_by(SalesReturn.return_date.desc(),SalesReturn.id.desc()).all()
    total_sales=sum((Decimal(s.total or 0) for s in sales),Decimal('0'))
    total_paid=sum((Decimal(s.paid or 0) for s in sales),Decimal('0'))
    outstanding=Decimal(customer.opening_balance or 0)+sum((sale_balance(db,s) for s in sales),Decimal('0'))
    total_returns=sum((Decimal(r.total or 0) for r in returns),Decimal('0'))
    last_payment=payments[0].payment_date.isoformat()+'Z' if payments else None
    top_query=(db.query(Product.name, func.sum(SaleItem.quantity).label('qty'), func.sum(SaleItem.line_total).label('value'))
        .join(SaleItem, SaleItem.product_id==Product.id)
        .join(Sale, Sale.id==SaleItem.sale_id)
        .filter(Sale.customer_id==customer.id, Sale.status=='posted')
        .group_by(Product.id,Product.name).order_by(func.sum(SaleItem.line_total).desc()).limit(8).all())
    activity=[]
    for s in sales:
        activity.append({'date':s.sale_date.isoformat()+'Z','type':'sale','reference':s.invoice_no,'debit':str(s.total),'credit':'0.00','amount':str(s.total)})
        for sp in db.query(SalePayment).filter(SalePayment.sale_id==s.id).order_by(SalePayment.id).all():
            activity.append({'date':sp.created_at.isoformat()+'Z','type':'sale_payment','reference':sp.reference or f'{s.invoice_no} payment #{sp.id}','debit':'0.00','credit':str(sp.amount),'amount':str(sp.amount),'payment_method':sp.payment_method})
    for p in payments:
        activity.append({'date':p.payment_date.isoformat()+'Z','type':'payment','reference':p.reference or f'Payment #{p.id}','debit':'0.00','credit':str(p.amount),'amount':str(p.amount),'payment_method':p.payment_method})
    for r in returns:
        activity.append({'date':r.return_date.isoformat()+'Z','type':'return','reference':f'Return #{r.id}','debit':'0.00','credit':str(r.total),'amount':str(r.total),'payment_method':r.refund_method})
    activity.sort(key=lambda x:x['date'],reverse=True)
    return {'customer':{'id':customer.id,'name':customer.name,'phone':customer.phone,'address':customer.address,'credit_limit':str(customer.credit_limit),'payment_terms_days':customer.payment_terms_days,'opening_balance':str(customer.opening_balance),'is_active':customer.is_active},
            'summary':{'total_sales':str(total_sales),'total_paid':str(total_paid),'outstanding':str(max(outstanding,Decimal('0'))),'available_credit':str(max(Decimal(customer.credit_limit or 0)-max(outstanding,Decimal('0')),Decimal('0'))),'total_returns':str(total_returns),'last_payment':last_payment},
            'top_products':[{'name':name,'quantity':str(qty),'value':str(value)} for name,qty,value in top_query],
            'activity':activity[:40]}


@router.get('/purchase-orders')
def purchase_orders(status:str|None=None, supplier_id:int|None=None, db:Session=Depends(get_db), _:object=Depends(require_roles('owner','admin','manager','storekeeper','accountant'))):
    from app.models.models import PurchaseOrder, Supplier
    q=db.query(PurchaseOrder,Supplier.name.label('supplier_name')).outerjoin(Supplier,Supplier.id==PurchaseOrder.supplier_id)
    if status: q=q.filter(PurchaseOrder.status==status)
    if supplier_id: q=q.filter(PurchaseOrder.supplier_id==supplier_id)
    return [{'id':o.id,'po_no':o.po_no,'supplier_id':o.supplier_id,'supplier_name':name or 'Walk-in supplier','branch_id':o.branch_id,'warehouse_id':o.warehouse_id,'order_date':o.order_date.isoformat()+'Z','expected_date':o.expected_date.isoformat() if o.expected_date else None,'subtotal':str(o.subtotal),'discount':str(o.discount),'total':str(o.total),'status':o.status,'notes':o.notes} for o,name in q.order_by(PurchaseOrder.order_date.desc(),PurchaseOrder.id.desc()).limit(1000).all()]

@router.post('/purchase-orders')
def create_purchase_order(payload:PurchaseOrderCreate, db:Session=Depends(get_db), user=Depends(require_roles('owner','admin','manager','storekeeper'))):
    from datetime import date as _date
    from app.models.models import PurchaseOrder, PurchaseOrderItem, Product, Supplier
    if payload.supplier_id and not db.get(Supplier,payload.supplier_id): raise HTTPException(404,'Supplier not found.')
    po_no=payload.po_no or f'PO-{date.today().strftime("%Y%m%d")}-{__import__("uuid").uuid4().hex[:7].upper()}'
    if db.query(PurchaseOrder).filter_by(po_no=po_no).first(): raise HTTPException(409,'Purchase order number already exists.')
    subtotal=Decimal('0')
    rows=[]
    for i in payload.items:
        product=db.get(Product,i.product_id)
        if not product: raise HTTPException(404,f'Product {i.product_id} not found.')
        line=i.quantity*i.unit_price; subtotal+=line; rows.append((i,line))
    total=max(Decimal('0'),subtotal-payload.discount)
    row=PurchaseOrder(po_no=po_no,supplier_id=payload.supplier_id,branch_id=payload.branch_id,warehouse_id=payload.warehouse_id,expected_date=_date.fromisoformat(payload.expected_date) if payload.expected_date else None,subtotal=subtotal,discount=payload.discount,total=total,status='draft',notes=payload.notes,created_by=user.id,created_at=datetime.utcnow(),updated_at=datetime.utcnow())
    db.add(row); db.flush()
    for i,line in rows: db.add(PurchaseOrderItem(purchase_order_id=row.id,product_id=i.product_id,unit_id=i.unit_id,quantity=i.quantity,unit_price=i.unit_price,line_total=line,batch_no=i.batch_no,expiry_date=_date.fromisoformat(i.expiry_date) if i.expiry_date else None))
    audit(db,'purchase_order_created','purchase_order',row.id,after={'po_no':row.po_no,'total':str(row.total)},user_id=user.id); db.commit(); db.refresh(row)
    return {'id':row.id,'po_no':row.po_no,'total':str(row.total),'status':row.status}

@router.put('/purchase-orders/{order_id}/status')
def update_purchase_order_status(order_id:int,payload:PurchaseOrderStatusUpdate,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','manager','storekeeper'))):
    from app.models.models import PurchaseOrder
    row=db.get(PurchaseOrder,order_id)
    if not row: raise HTTPException(404,'Purchase order not found.')
    allowed={'draft':{'approved','cancelled'},'approved':{'received','cancelled'},'received':set(),'cancelled':set()}
    if payload.status not in allowed.get(row.status,set()): raise HTTPException(400,f'Invalid purchase order transition: {row.status} -> {payload.status}.')
    row.status=payload.status
    audit(db,'purchase_order_status_changed','purchase_order',row.id,after={'status':row.status},user_id=user.id); db.commit(); return {'id':row.id,'status':row.status}

@router.post('/purchase-orders/{order_id}/receive')
def receive_purchase_order(order_id:int,payload:PurchaseOrderReceiveIn,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','manager','storekeeper','accountant'))):
    from app.models.models import PurchaseOrder, PurchaseOrderItem
    from app.services.finance import create_purchase
    from app.schemas.finance import PurchaseItemCreate
    row=db.get(PurchaseOrder,order_id)
    if not row: raise HTTPException(404,'Purchase order not found.')
    if row.status!='approved': raise HTTPException(400,'Only an approved purchase order can be received.')
    items=db.query(PurchaseOrderItem).filter(PurchaseOrderItem.purchase_order_id==row.id).all()
    if not items: raise HTTPException(400,'Purchase order has no items.')
    payments=payload.payments
    if not payments and payload.paid>0:
        from app.schemas.finance import PurchasePaymentLine
        payments=[PurchasePaymentLine(payment_method=payload.payment_method,amount=payload.paid,bank_account_id=payload.bank_account_id,reference=payload.reference)]
    purchase_payload=PurchaseCreate(invoice_no=payload.invoice_no,supplier_id=row.supplier_id,items=[PurchaseItemCreate(product_id=i.product_id,quantity=i.quantity,unit_price=i.unit_price,unit_id=i.unit_id,batch_no=i.batch_no,expiry_date=i.expiry_date.isoformat() if i.expiry_date else None) for i in items],paid=payload.paid,payment_method=payload.payment_method,bank_account_id=payload.bank_account_id,reference=payload.reference,branch_id=row.branch_id,warehouse_id=payload.warehouse_id or row.warehouse_id,payments=payments)
    try:
        purchase=create_purchase(db,purchase_payload,user.id)
        row.status='received'
        db.commit()
        return {'purchase_order_id':row.id,'po_no':row.po_no,'purchase_id':purchase.id,'invoice_no':purchase.invoice_no,'status':row.status}
    except ValueError as exc:
        db.rollback(); raise HTTPException(400,str(exc))
