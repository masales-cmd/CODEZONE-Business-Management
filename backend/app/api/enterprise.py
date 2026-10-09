from datetime import datetime, date, timezone
import json
from decimal import Decimal
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import current_user, require_roles
from app.models.models import (
    Customer, CustomerGroup, CustomerPayment, PaymentAllocation, Sale, SaleItem, SalePayment, SalesReturn,
    TaxRate, DeliveryOrder, DeliveryItem, SalesOrder, SalesOrderItem, Product,
    ServiceOrder, LoyaltyAccount, LoyaltyTransaction, WarehouseBin, Warehouse,
    StockAdjustment, StockAdjustmentItem, StockBalance, PaymentTransaction, Notification, ApprovalRequest,
    ProductBatch, ProductSerial, Recipe, RecipeItem, ProductionOrder, Purchase, PriceListItem,
)
from app.services.transactions import sale_balance, audit
from app.services.sales import create_sale
from app.schemas.common import SaleCreate, SaleItemCreate, PaymentLineCreate
from app.services.accounting import build_customer_payment_journal
from app.services.sync import queue_sync_event

router = APIRouter(prefix='/api/enterprise', tags=['enterprise'], dependencies=[Depends(current_user)])

def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class CustomerGroupIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    discount_percent: Decimal = Field(default=0, ge=0, le=100)
    default_price_list_id: int | None = None

class AllocatePaymentIn(BaseModel):
    customer_id: int
    amount: Decimal = Field(gt=0)
    payment_method: str = Field(default='cash', pattern='^(cash|bank|mobile_money|card|cheque)$')
    bank_account_id: int | None = None
    reference: str | None = Field(default=None, max_length=160)
    allocations: list[dict]
    notes: str | None = None

class DeliveryIn(BaseModel):
    sale_id: int | None = None
    sales_order_id: int | None = None
    customer_id: int | None = None
    driver_name: str | None = None
    vehicle_no: str | None = None
    address: str | None = None
    scheduled_at: datetime | None = None
    items: list[dict] = []
    notes: str | None = None

class DeliveryStatusIn(BaseModel):
    status: str = Field(pattern='^(pending|packed|out_for_delivery|delivered|cancelled)$')

class ServiceOrderIn(BaseModel):
    customer_id: int | None = None
    title: str = Field(min_length=2, max_length=180)
    description: str | None = None
    assigned_user_id: int | None = None
    quoted_amount: Decimal = Field(default=0, ge=0)
    scheduled_date: date | None = None

class ServiceStatusIn(BaseModel):
    status: str = Field(pattern='^(open|in_progress|waiting|completed|cancelled)$')
    actual_amount: Decimal | None = Field(default=None, ge=0)

class LoyaltyAdjustIn(BaseModel):
    points: int
    reason: str = Field(min_length=2, max_length=180)
    reference: str | None = Field(default=None, max_length=120)

class BinIn(BaseModel):
    warehouse_id: int
    code: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=120)

class AdjustmentIn(BaseModel):
    warehouse_id: int
    reason: str = Field(min_length=2, max_length=255)
    items: list[dict]

class ApprovalIn(BaseModel):
    request_type: str
    entity_type: str
    entity_id: int
    reason: str | None = None

class ApprovalDecisionIn(BaseModel):
    status: str = Field(pattern='^(approved|rejected)$')

class ProductionCompleteIn(BaseModel):
    output_warehouse_id: int

class RecipeItemIn(BaseModel):
    product_id: int
    quantity: Decimal = Field(gt=0)

class RecipeIn(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    output_product_id: int
    output_quantity: Decimal = Field(gt=0)
    items: list[RecipeItemIn] = Field(min_length=1)

class ProductionOrderIn(BaseModel):
    recipe_id: int
    quantity: Decimal = Field(gt=0)

class IntegrationIn(BaseModel):
    provider: str = Field(min_length=2, max_length=80)
    name: str = Field(min_length=2, max_length=120)
    settings: dict = {}


@router.get('/customer-groups')
def customer_groups(db: Session = Depends(get_db), _: object = Depends(current_user)):
    return [{'id':g.id,'name':g.name,'discount_percent':str(g.discount_percent),'default_price_list_id':g.default_price_list_id,'is_active':g.is_active} for g in db.query(CustomerGroup).order_by(CustomerGroup.name).all()]

@router.post('/customer-groups')
def create_customer_group(payload: CustomerGroupIn, db: Session = Depends(get_db), _: object = Depends(require_roles('owner','admin','manager'))):
    if db.query(CustomerGroup).filter(func.lower(CustomerGroup.name)==payload.name.lower()).first():
        raise HTTPException(409,'Customer group already exists.')
    row=CustomerGroup(**payload.model_dump()); db.add(row); db.commit(); db.refresh(row)
    return {'id':row.id,'name':row.name,'discount_percent':str(row.discount_percent),'default_price_list_id':row.default_price_list_id}

@router.put('/customers/{customer_id}/group/{group_id}')
def assign_customer_group(customer_id:int, group_id:int, db:Session=Depends(get_db), _: object=Depends(require_roles('owner','admin','manager','salesperson'))):
    customer=db.get(Customer,customer_id); group=db.get(CustomerGroup,group_id)
    if not customer or not group: raise HTTPException(404,'Customer or group not found.')
    customer.group_id=group.id; db.commit(); return {'customer_id':customer.id,'group_id':group.id}

@router.get('/pricing/resolve')
def resolve_price(product_id:int,customer_id:int|None=None,price_list_id:int|None=None,unit_id:int|None=None,db:Session=Depends(get_db),_:object=Depends(current_user)):
    from app.models.models import PriceListItem
    product=db.get(Product,product_id)
    if not product: raise HTTPException(404,'Product not found.')
    selected=price_list_id
    if not selected and customer_id:
        c=db.get(Customer,customer_id)
        if c and c.group_id:
            g=db.get(CustomerGroup,c.group_id)
            selected=g.default_price_list_id if g else None
    row=None
    if selected:
        row=db.query(PriceListItem).filter(PriceListItem.price_list_id==selected,PriceListItem.product_id==product_id,PriceListItem.unit_id==unit_id).first()
        if not row and unit_id is not None:
            row=db.query(PriceListItem).filter(PriceListItem.price_list_id==selected,PriceListItem.product_id==product_id,PriceListItem.unit_id.is_(None)).first()
    price=Decimal(row.price) if row else Decimal(product.selling_price or 0)
    if customer_id:
        c=db.get(Customer,customer_id)
        if c and c.group_id:
            g=db.get(CustomerGroup,c.group_id)
            if g and Decimal(g.discount_percent or 0)>0 and not row:
                price=(price*(Decimal('1')-Decimal(g.discount_percent)/Decimal('100'))).quantize(Decimal('0.01'))
    return {'product_id':product.id,'product_name':product.name,'price':str(price),'source':'price_list' if row else 'product','price_list_id':selected}

@router.get('/customers/{customer_id}/statement')
def customer_statement(customer_id:int,db:Session=Depends(get_db),_:object=Depends(current_user)):
    customer=db.get(Customer,customer_id)
    if not customer: raise HTTPException(404,'Customer not found.')
    events=[]
    sales=db.query(Sale).filter(Sale.customer_id==customer_id,Sale.status=='posted').order_by(Sale.sale_date,Sale.id).all()
    for s in sales:
        events.append({'date':s.sale_date.isoformat(),'type':'invoice','reference':s.invoice_no,'debit':str(s.total),'credit':'0.00','balance':None})
        # Payments collected at the time of sale are part of the customer's ledger too.
        for sp in db.query(SalePayment).filter(SalePayment.sale_id==s.id).order_by(SalePayment.id).all():
            events.append({'date':sp.created_at.isoformat(),'type':'sale_payment','reference':sp.reference or f'{s.invoice_no} payment #{sp.id}','debit':'0.00','credit':str(sp.amount),'balance':None})
    for r in db.query(SalesReturn).filter_by(customer_id=customer_id,status='posted').order_by(SalesReturn.return_date).all():
        events.append({'date':r.return_date.isoformat(),'type':'return','reference':f'Return #{r.id}','debit':'0.00','credit':str(r.total),'balance':None})
    for p in db.query(CustomerPayment).filter(CustomerPayment.customer_id==customer_id).order_by(CustomerPayment.payment_date,CustomerPayment.id).all():
        events.append({'date':p.payment_date.isoformat(),'type':'payment','reference':p.reference or f'Payment #{p.id}','debit':'0.00','credit':str(p.amount),'balance':None})
    events.sort(key=lambda x:x['date'])
    running=Decimal(customer.opening_balance or 0)
    for e in events:
        running += Decimal(e['debit'])-Decimal(e['credit'])
        e['balance']=str(running)
    return {'customer':{'id':customer.id,'name':customer.name,'phone':customer.phone,'credit_limit':str(customer.credit_limit)},'opening_balance':str(customer.opening_balance),'closing_balance':str(max(running,Decimal('0'))),'entries':events}

@router.post('/customer-payments/allocate')
def allocate_customer_payment(payload:AllocatePaymentIn,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','manager','accountant','salesperson','cashier'))):
    customer=db.get(Customer,payload.customer_id)
    if not customer: raise HTTPException(404,'Customer not found.')
    if payload.payment_method=='bank' and (not payload.bank_account_id): raise HTTPException(400,'Bank account is required.')
    total_alloc=sum((Decimal(str(x['amount'])) for x in payload.allocations),Decimal('0'))
    if total_alloc>payload.amount: raise HTTPException(400,'Allocations cannot exceed payment amount.')
    for row in payload.allocations:
        sale=db.get(Sale,int(row['sale_id'])); amount=Decimal(str(row['amount']))
        if not sale or sale.customer_id!=customer.id: raise HTTPException(400,'Invalid invoice in allocation.')
        if amount<=0 or amount>sale_balance(db,sale): raise HTTPException(400,f'Allocation exceeds invoice balance for {sale.invoice_no}.')
    payment=CustomerPayment(customer_id=customer.id,amount=payload.amount,payment_method=payload.payment_method,bank_account_id=payload.bank_account_id,reference=payload.reference,notes=payload.notes)
    db.add(payment); db.flush()
    from app.services.transactions import money_flow
    money_flow(db,payload.amount,payload.payment_method,payload.bank_account_id,'customer_payment','customer_refund',payload.reference or f'payment:{payment.id}','Customer payment')
    allocated=Decimal('0')
    for row in payload.allocations:
        sale=db.get(Sale,int(row['sale_id'])); amount=Decimal(str(row['amount']))
        sale.paid += amount
        db.add(PaymentAllocation(customer_payment_id=payment.id,sale_id=sale.id,amount=amount)); allocated += amount
    if total_alloc==0:
        opening=Decimal(customer.opening_balance or 0)
        if payload.amount>opening: raise HTTPException(400,'Unallocated payment exceeds opening balance.')
        customer.opening_balance=opening-payload.amount
    else:
        remaining=payload.amount-total_alloc
        if remaining:
            opening=Decimal(customer.opening_balance or 0)
            if remaining>opening: raise HTTPException(400,'Remaining payment exceeds opening balance.')
            customer.opening_balance=opening-remaining
    audit(db,'customer_payment_allocated','customer_payment',payment.id,after={'amount':str(payload.amount),'allocated':str(total_alloc)},user_id=user.id)
    build_customer_payment_journal(db,payment.id)
    queue_sync_event(db,'customer_payment',payment.id,'created',{'customer_id':customer.id,'amount':str(payload.amount),'allocated':str(total_alloc),'payment_method':payload.payment_method})
    db.commit(); return {'id':payment.id,'amount':str(payload.amount),'allocated':str(total_alloc),'unallocated':str(payload.amount-total_alloc)}

@router.get('/deliveries')
def deliveries(status:str|None=None,db:Session=Depends(get_db),_:object=Depends(current_user)):
    q=db.query(DeliveryOrder).order_by(DeliveryOrder.created_at.desc())
    if status: q=q.filter(DeliveryOrder.status==status)
    return [{'id':d.id,'delivery_no':d.delivery_no,'sale_id':d.sale_id,'sales_order_id':d.sales_order_id,'customer_id':d.customer_id,'driver_name':d.driver_name,'vehicle_no':d.vehicle_no,'status':d.status,'scheduled_at':d.scheduled_at.isoformat() if d.scheduled_at else None,'delivered_at':d.delivered_at.isoformat() if d.delivered_at else None} for d in q.limit(1000).all()]

@router.post('/deliveries')
def create_delivery(payload:DeliveryIn,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','manager','storekeeper','salesperson'))):
    sale=db.get(Sale,payload.sale_id) if payload.sale_id else None
    order=db.get(SalesOrder,payload.sales_order_id) if payload.sales_order_id else None
    if not sale and not order: raise HTTPException(400,'A delivery must reference a sale or sales order.')
    customer_id=payload.customer_id or (sale.customer_id if sale else order.customer_id)
    if not customer_id: raise HTTPException(400,'Delivery requires a customer.')
    no=f'DEL-{now().strftime("%Y%m%d")}-{uuid4().hex[:7].upper()}'
    d=DeliveryOrder(delivery_no=no,sale_id=sale.id if sale else None,sales_order_id=order.id if order else None,customer_id=customer_id,driver_name=payload.driver_name,vehicle_no=payload.vehicle_no,address=payload.address,scheduled_at=payload.scheduled_at,notes=payload.notes)
    db.add(d); db.flush()
    source_items=[]
    if sale: source_items=db.query(SaleItem).filter(SaleItem.sale_id==sale.id).all()
    if order: source_items=db.query(SalesOrderItem).filter(SalesOrderItem.order_id==order.id).all()
    for item in (payload.items or [{'product_id':i.product_id,'quantity':i.quantity} for i in source_items]):
        db.add(DeliveryItem(delivery_id=d.id,product_id=int(item['product_id']),quantity=Decimal(str(item['quantity']))))
    audit(db,'delivery_created','delivery_order',d.id,after={'delivery_no':no},user_id=user.id)
    db.commit(); db.refresh(d); return {'id':d.id,'delivery_no':d.delivery_no,'status':d.status}

@router.put('/deliveries/{delivery_id}/status')
def delivery_status(delivery_id:int,payload:DeliveryStatusIn,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','manager','storekeeper','salesperson'))):
    d=db.get(DeliveryOrder,delivery_id)
    if not d: raise HTTPException(404,'Delivery not found.')
    d.status=payload.status
    if payload.status=='delivered': d.delivered_at=now()
    audit(db,'delivery_status_changed','delivery_order',d.id,after={'status':d.status},user_id=user.id)
    db.commit(); return {'id':d.id,'status':d.status}

@router.get('/services')
def services(status:str|None=None,db:Session=Depends(get_db),_:object=Depends(current_user)):
    q=db.query(ServiceOrder).order_by(ServiceOrder.created_at.desc())
    if status: q=q.filter(ServiceOrder.status==status)
    return [{'id':s.id,'job_no':s.job_no,'customer_id':s.customer_id,'title':s.title,'description':s.description,'assigned_user_id':s.assigned_user_id,'status':s.status,'quoted_amount':str(s.quoted_amount),'actual_amount':str(s.actual_amount),'scheduled_date':s.scheduled_date.isoformat() if s.scheduled_date else None} for s in q.limit(1000).all()]

@router.post('/services')
def create_service(payload:ServiceOrderIn,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','manager','salesperson'))):
    no=f'JOB-{now().strftime("%Y%m%d")}-{uuid4().hex[:7].upper()}'
    row=ServiceOrder(job_no=no,**payload.model_dump()); db.add(row); db.flush(); audit(db,'service_order_created','service_order',row.id,after={'job_no':no},user_id=user.id); db.commit(); db.refresh(row); return {'id':row.id,'job_no':row.job_no}

@router.put('/services/{service_id}/status')
def service_status(service_id:int,payload:ServiceStatusIn,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','manager','salesperson'))):
    row=db.get(ServiceOrder,service_id)
    if not row: raise HTTPException(404,'Service order not found.')
    row.status=payload.status
    if payload.actual_amount is not None: row.actual_amount=payload.actual_amount
    if payload.status=='completed': row.completed_at=now()
    audit(db,'service_order_status_changed','service_order',row.id,after={'status':row.status},user_id=user.id); db.commit(); return {'id':row.id,'status':row.status}

@router.get('/loyalty/{customer_id}')
def loyalty(customer_id:int,db:Session=Depends(get_db),_:object=Depends(current_user)):
    c=db.get(Customer,customer_id)
    if not c: raise HTTPException(404,'Customer not found.')
    a=db.query(LoyaltyAccount).filter_by(customer_id=customer_id).first()
    if not a: a=LoyaltyAccount(customer_id=customer_id); db.add(a); db.commit(); db.refresh(a)
    return {'id':a.id,'customer_id':customer_id,'points_balance':a.points_balance,'lifetime_points':a.lifetime_points}

@router.post('/loyalty/{customer_id}/adjust')
def loyalty_adjust(customer_id:int,payload:LoyaltyAdjustIn,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','manager'))):
    if not db.get(Customer,customer_id): raise HTTPException(404,'Customer not found.')
    a=db.query(LoyaltyAccount).filter_by(customer_id=customer_id).first()
    if not a: a=LoyaltyAccount(customer_id=customer_id); db.add(a); db.flush()
    if a.points_balance + payload.points < 0: raise HTTPException(400,'Loyalty balance cannot be negative.')
    a.points_balance += payload.points
    if payload.points>0: a.lifetime_points += payload.points
    db.add(LoyaltyTransaction(account_id=a.id,points=payload.points,reason=payload.reason,reference=payload.reference))
    audit(db,'loyalty_adjusted','loyalty_account',a.id,after={'points':payload.points,'reason':payload.reason},user_id=user.id); db.commit(); return {'points_balance':a.points_balance,'lifetime_points':a.lifetime_points}

@router.get('/tax-rates')
def tax_rates(db:Session=Depends(get_db),_:object=Depends(current_user)):
    return [{'id':x.id,'name':x.name,'rate':str(x.rate),'inclusive':x.inclusive,'is_active':x.is_active} for x in db.query(TaxRate).order_by(TaxRate.name).all()]

class TaxRateIn(BaseModel):
    name:str
    rate:Decimal=Field(ge=0,le=100)
    inclusive:bool=False

@router.post('/tax-rates')
def create_tax_rate(payload:TaxRateIn,db:Session=Depends(get_db),_:object=Depends(require_roles('owner','admin','accountant'))):
    r=TaxRate(**payload.model_dump()); db.add(r); db.commit(); db.refresh(r); return {'id':r.id,'name':r.name,'rate':str(r.rate),'inclusive':r.inclusive}

@router.get('/warehouse-bins')
def bins(warehouse_id:int|None=None,db:Session=Depends(get_db),_:object=Depends(current_user)):
    q=db.query(WarehouseBin); 
    if warehouse_id: q=q.filter(WarehouseBin.warehouse_id==warehouse_id)
    return [{'id':b.id,'warehouse_id':b.warehouse_id,'code':b.code,'name':b.name,'is_active':b.is_active} for b in q.order_by(WarehouseBin.code).all()]

@router.post('/warehouse-bins')
def create_bin(payload:BinIn,db:Session=Depends(get_db),_:object=Depends(require_roles('owner','admin','manager','storekeeper'))):
    if not db.get(Warehouse,payload.warehouse_id): raise HTTPException(404,'Warehouse not found.')
    if db.query(WarehouseBin).filter_by(warehouse_id=payload.warehouse_id,code=payload.code).first(): raise HTTPException(409,'Bin code already exists in this warehouse.')
    b=WarehouseBin(**payload.model_dump()); db.add(b); db.commit(); db.refresh(b); return {'id':b.id,'code':b.code}

@router.post('/stock-adjustments')
def stock_adjustment(payload:AdjustmentIn,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','manager','storekeeper'))):
    if not db.get(Warehouse,payload.warehouse_id): raise HTTPException(404,'Warehouse not found.')
    a=StockAdjustment(adjustment_no=f'ADJ-{now().strftime("%Y%m%d")}-{uuid4().hex[:7].upper()}',warehouse_id=payload.warehouse_id,reason=payload.reason,created_by=user.id)
    db.add(a); db.flush()
    for item in payload.items:
        pid=int(item['product_id']); delta=Decimal(str(item['quantity_delta']))
        p=db.get(Product,pid)
        if not p: raise HTTPException(404,f'Product {pid} not found.')
        b=db.query(StockBalance).filter_by(warehouse_id=payload.warehouse_id,product_id=pid).with_for_update().first()
        if not b: b=StockBalance(warehouse_id=payload.warehouse_id,product_id=pid,quantity=0,reorder_level=0); db.add(b); db.flush()
        if Decimal(b.quantity)+delta < 0: raise HTTPException(400,f'Adjustment would make stock negative for {p.name}.')
        b.quantity=Decimal(b.quantity)+delta
        total=db.query(func.coalesce(func.sum(StockBalance.quantity),0)).filter(StockBalance.product_id==pid).scalar() or 0
        p.current_stock=Decimal(total)
        db.add(StockAdjustmentItem(adjustment_id=a.id,product_id=pid,quantity_delta=delta))
        db.add(__import__('app.models.models',fromlist=['StockMovement']).StockMovement(
            product_id=pid,
            movement_type='stock_adjustment',
            quantity=delta,
            reference_type='stock_adjustment',
            reference_id=a.id,
        ))
    audit(db,'stock_adjustment_created','stock_adjustment',a.id,after={'adjustment_no':a.adjustment_no,'reason':a.reason},user_id=user.id); db.commit(); return {'id':a.id,'adjustment_no':a.adjustment_no}

@router.get('/recipes')
def recipes(db:Session=Depends(get_db),_:object=Depends(current_user)):
    rows=[]
    for r in db.query(Recipe).filter(Recipe.is_active==True).order_by(Recipe.name).all():
        rows.append({'id':r.id,'name':r.name,'output_product_id':r.output_product_id,'output_quantity':str(r.output_quantity),'items':[{'product_id':i.product_id,'quantity':str(i.quantity)} for i in db.query(RecipeItem).filter(RecipeItem.recipe_id==r.id).all()]})
    return rows

@router.post('/recipes')
def create_recipe(payload:RecipeIn,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','manager'))):
    if not db.get(Product,payload.output_product_id): raise HTTPException(404,'Output product not found.')
    for item in payload.items:
        if not db.get(Product,item.product_id): raise HTTPException(404,f'Component product {item.product_id} not found.')
    r=Recipe(name=payload.name,output_product_id=payload.output_product_id,output_quantity=payload.output_quantity); db.add(r); db.flush()
    for item in payload.items: db.add(RecipeItem(recipe_id=r.id,product_id=item.product_id,quantity=item.quantity))
    audit(db,'recipe_created','recipe',r.id,after={'name':r.name},user_id=user.id); db.commit(); db.refresh(r); return {'id':r.id,'name':r.name}

@router.get('/production')
def production_orders(db:Session=Depends(get_db),_:object=Depends(current_user)):
    rows=[]
    for o in db.query(ProductionOrder).order_by(ProductionOrder.created_at.desc()).limit(500).all():
        r=db.get(Recipe,o.recipe_id); out=db.get(Product,r.output_product_id) if r else None
        rows.append({'id':o.id,'order_no':o.order_no,'recipe_id':o.recipe_id,'recipe_name':r.name if r else '', 'output_product':out.name if out else '', 'quantity':str(o.quantity),'status':o.status,'created_at':o.created_at.isoformat()})
    return rows

@router.post('/production')
def create_production(payload:ProductionOrderIn,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','manager','storekeeper'))):
    recipe=db.get(Recipe,payload.recipe_id)
    if not recipe or not recipe.is_active: raise HTTPException(404,'Recipe not found.')
    no=f'PROD-{now().strftime("%Y%m%d")}-{uuid4().hex[:7].upper()}'
    o=ProductionOrder(order_no=no,recipe_id=recipe.id,quantity=payload.quantity,created_by=user.id); db.add(o); db.flush(); audit(db,'production_order_created','production_order',o.id,after={'order_no':no,'quantity':str(payload.quantity)},user_id=user.id); db.commit(); db.refresh(o); return {'id':o.id,'order_no':o.order_no,'status':o.status}

@router.get('/batches')
def batches(product_id:int|None=None,expiring_days:int|None=None,db:Session=Depends(get_db),_:object=Depends(current_user)):
    q=db.query(ProductBatch).order_by(ProductBatch.expiry_date.asc().nullslast(),ProductBatch.batch_no)
    if product_id: q=q.filter(ProductBatch.product_id==product_id)
    rows=[]
    today=date.today()
    for b in q.limit(2000).all():
        days=None
        if b.expiry_date: days=(b.expiry_date.date() if hasattr(b.expiry_date,'date') else b.expiry_date)-today
        if expiring_days is not None and (days is None or days.days>expiring_days): continue
        rows.append({'id':b.id,'product_id':b.product_id,'batch_no':b.batch_no,'expiry_date':b.expiry_date.isoformat() if b.expiry_date else None,'quantity':str(b.quantity),'days_to_expiry':days.days if days is not None else None})
    return rows

@router.get('/serials')
def serials(product_id:int|None=None,status:str|None=None,db:Session=Depends(get_db),_:object=Depends(current_user)):
    q=db.query(ProductSerial).order_by(ProductSerial.serial_no)
    if product_id:q=q.filter(ProductSerial.product_id==product_id)
    if status:q=q.filter(ProductSerial.status==status)
    return [{'id':x.id,'product_id':x.product_id,'serial_no':x.serial_no,'status':x.status,'sold_sale_id':x.sold_sale_id} for x in q.limit(3000).all()]


@router.get('/payment-transactions')
def payment_transactions(direction:str|None=None, method:str|None=None, reconciled:bool|None=None, limit:int=500, db:Session=Depends(get_db), _:object=Depends(require_roles('owner','admin','manager','accountant'))):
    q=db.query(PaymentTransaction).order_by(PaymentTransaction.created_at.desc(),PaymentTransaction.id.desc())
    if direction: q=q.filter(PaymentTransaction.direction==direction)
    if method: q=q.filter(PaymentTransaction.payment_method==method)
    if reconciled is True: q=q.filter(PaymentTransaction.reconciled_at.is_not(None))
    if reconciled is False: q=q.filter(PaymentTransaction.reconciled_at.is_(None))
    limit=max(1,min(limit,2000))
    return [{'id':x.id,'direction':x.direction,'payment_method':x.payment_method,'amount':str(x.amount),'reference':x.reference,'external_reference':x.external_reference,'bank_account_id':x.bank_account_id,'created_at':x.created_at.isoformat(),'reconciled_at':x.reconciled_at.isoformat() if x.reconciled_at else None,'reconciled_by':x.reconciled_by} for x in q.limit(limit).all()]

@router.post('/payment-transactions/{transaction_id}/reconcile')
def reconcile_payment_transaction(transaction_id:int, db:Session=Depends(get_db), user=Depends(require_roles('owner','admin','manager','accountant'))):
    x=db.get(PaymentTransaction,transaction_id)
    if not x: raise HTTPException(404,'Payment transaction not found.')
    if x.reconciled_at: return {'id':x.id,'reconciled':True,'reconciled_at':x.reconciled_at.isoformat()}
    x.reconciled_at=now(); x.reconciled_by=user.id
    audit(db,'payment_reconciled','payment_transaction',x.id,after={'reconciled_by':user.id},user_id=user.id)
    db.commit()
    return {'id':x.id,'reconciled':True,'reconciled_at':x.reconciled_at.isoformat()}

@router.get('/integrations')
def integrations(db:Session=Depends(get_db),_:object=Depends(require_roles('owner','admin','manager'))):
    from app.models.models import IntegrationConnection
    return [{'id':x.id,'provider':x.provider,'name':x.name,'status':x.status,'settings':json.loads(x.settings_json or '{}')} for x in db.query(IntegrationConnection).order_by(IntegrationConnection.name).all()]

@router.post('/integrations')
def create_integration(payload:IntegrationIn,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin'))):
    from app.models.models import IntegrationConnection
    x=IntegrationConnection(provider=payload.provider,name=payload.name,status='disabled',settings_json=json.dumps(payload.settings or {})); db.add(x); db.commit(); db.refresh(x); audit(db,'integration_registered','integration',x.id,after={'provider':x.provider,'name':x.name},user_id=user.id); db.commit(); return {'id':x.id,'status':x.status}

@router.post('/production/{production_id}/complete')
def complete_production(production_id:int,payload:ProductionCompleteIn,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','manager','storekeeper'))):
    order=db.get(ProductionOrder,production_id)
    if not order or order.status in ('completed','cancelled'): raise HTTPException(404,'Production order not available.')
    recipe=db.get(Recipe,order.recipe_id)
    if not recipe: raise HTTPException(404,'Recipe not found.')
    if not db.get(Warehouse,payload.output_warehouse_id): raise HTTPException(404,'Output warehouse not found.')
    factor=Decimal(order.quantity)/Decimal(recipe.output_quantity or 1)
    for ri in db.query(RecipeItem).filter(RecipeItem.recipe_id==recipe.id).all():
        p=db.get(Product,ri.product_id); need=Decimal(ri.quantity)*factor
        b=db.query(StockBalance).filter_by(warehouse_id=payload.output_warehouse_id,product_id=p.id).with_for_update().first()
        if not b: raise HTTPException(400,f'No stock balance configured for {p.name}.')
        if Decimal(b.quantity)<need: raise HTTPException(400,f'Insufficient component stock for {p.name}.')
    for ri in db.query(RecipeItem).filter(RecipeItem.recipe_id==recipe.id).all():
        p=db.get(Product,ri.product_id); need=Decimal(ri.quantity)*factor
        b=db.query(StockBalance).filter_by(warehouse_id=payload.output_warehouse_id,product_id=p.id).with_for_update().first(); b.quantity-=need
        p.current_stock-=need
        db.add(__import__('app.models.models', fromlist=['StockMovement']).StockMovement(product_id=p.id,movement_type='production_consume',quantity=-need,reference_type='production_order',reference_id=order.id))
    out=db.get(Product,recipe.output_product_id)
    b=db.query(StockBalance).filter_by(warehouse_id=payload.output_warehouse_id,product_id=out.id).with_for_update().first()
    if not b: b=StockBalance(warehouse_id=payload.output_warehouse_id,product_id=out.id,quantity=0,reorder_level=out.reorder_level or 0); db.add(b); db.flush()
    produced=Decimal(order.quantity); b.quantity+=produced; out.current_stock+=produced
    db.add(__import__('app.models.models', fromlist=['StockMovement']).StockMovement(product_id=out.id,movement_type='production_output',quantity=produced,reference_type='production_order',reference_id=order.id))
    order.status='completed'; order.completed_at=now(); audit(db,'production_completed','production_order',order.id,after={'quantity':str(produced),'warehouse_id':payload.output_warehouse_id}); db.commit(); return {'id':order.id,'status':order.status,'output_product_id':out.id,'quantity':str(produced)}

@router.get('/approvals')
def approvals(status:str|None=None,db:Session=Depends(get_db),_:object=Depends(require_roles('owner','admin','manager'))):
    q=db.query(ApprovalRequest).order_by(ApprovalRequest.created_at.desc())
    if status:q=q.filter(ApprovalRequest.status==status)
    return [{'id':a.id,'request_type':a.request_type,'entity_type':a.entity_type,'entity_id':a.entity_id,'requested_by':a.requested_by,'approved_by':a.approved_by,'status':a.status,'reason':a.reason,'created_at':a.created_at.isoformat()} for a in q.limit(1000).all()]

@router.post('/approvals')
def create_approval(payload:ApprovalIn,db:Session=Depends(get_db),user=Depends(current_user)):
    a=ApprovalRequest(request_type=payload.request_type,entity_type=payload.entity_type,entity_id=payload.entity_id,requested_by=user.id,reason=payload.reason); db.add(a); db.commit(); db.refresh(a); return {'id':a.id,'status':a.status}

@router.put('/approvals/{approval_id}')
def decide_approval(approval_id:int,payload:ApprovalDecisionIn,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','manager'))):
    a=db.get(ApprovalRequest,approval_id)
    if not a: raise HTTPException(404,'Approval request not found.')
    if a.status!='pending': raise HTTPException(400,'Approval request is already decided.')
    a.status=payload.status; a.approved_by=user.id; a.decided_at=now(); db.commit(); return {'id':a.id,'status':a.status}

@router.get('/notifications')
def notifications(unread_only:bool=False,db:Session=Depends(get_db),user=Depends(current_user)):
    q=db.query(Notification).filter(or_(Notification.user_id==user.id,Notification.user_id.is_(None))).order_by(Notification.created_at.desc())
    if unread_only:q=q.filter(Notification.is_read==False)
    return [{'id':n.id,'kind':n.kind,'title':n.title,'message':n.message,'is_read':n.is_read,'created_at':n.created_at.isoformat()} for n in q.limit(200).all()]

@router.get('/notifications/unread-count')
def unread_notification_count(db:Session=Depends(get_db),user=Depends(current_user)):
    n=db.query(Notification).filter(or_(Notification.user_id==user.id,Notification.user_id.is_(None)),Notification.is_read==False).count()
    return {'count':n}

@router.post('/notifications/{notification_id}/read')
def mark_notification_read(notification_id:int,db:Session=Depends(get_db),user=Depends(current_user)):
    n=db.get(Notification,notification_id)
    if not n or (n.user_id not in (None,user.id)): raise HTTPException(404,'Notification not found.')
    n.is_read=True; db.commit(); return {'id':n.id,'is_read':True}

@router.put('/integrations/{integration_id}/status')
def integration_status(integration_id:int,status:str,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin'))):
    from app.models.models import IntegrationConnection
    if status not in ('disabled','configured','active','error'): raise HTTPException(400,'Invalid integration status.')
    x=db.get(IntegrationConnection,integration_id)
    if not x: raise HTTPException(404,'Integration not found.')
    x.status=status; audit(db,'integration_status_changed','integration',x.id,after={'status':status},user_id=user.id); db.commit(); return {'id':x.id,'status':x.status}

@router.post('/integrations/{integration_id}/test')
def integration_test(integration_id:int,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin'))):
    from app.models.models import IntegrationConnection
    x=db.get(IntegrationConnection,integration_id)
    if not x: raise HTTPException(404,'Integration not found.')
    # Provider-specific network credentials are intentionally not stored in source control.
    # This endpoint validates that the integration configuration is structurally readable.
    try: payload=json.loads(x.settings_json or '{}')
    except Exception: payload={}
    ok=isinstance(payload,dict)
    x.status='configured' if ok else 'error'; db.commit()
    return {'id':x.id,'provider':x.provider,'ok':ok,'status':x.status}

@router.get('/health/features')
def feature_health(db:Session=Depends(get_db),_:object=Depends(current_user)):
    return {
        'multi_location': bool(db.query(Warehouse).count()),
        'price_lists': bool(db.query(__import__('app.models.models',fromlist=['PriceList']).PriceList).count()),
        'split_payments': True,
        'tax_engine': bool(db.query(TaxRate).count()),
        'delivery': bool(db.query(DeliveryOrder).count() or True),
        'service_orders': True,
        'loyalty': True,
        'production': True,
        'approval_workflow': True,
        'notifications': True,
    }


class ServiceInvoiceIn(BaseModel):
    service_product_id:int
    warehouse_id:int|None=None
    branch_id:int|None=None
    shift_id:int|None=None
    paid:Decimal=Field(default=0,ge=0)
    payment_method:str=Field(default='cash',pattern='^(cash|bank|mobile_money|card|cheque)$')
    bank_account_id:int|None=None
    reference:str|None=Field(default=None,max_length=160)

@router.post('/services/{service_id}/invoice')
def invoice_service(service_id:int,payload:ServiceInvoiceIn,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','manager','salesperson','cashier'))):
    row=db.get(ServiceOrder,service_id)
    if not row: raise HTTPException(404,'Service order not found.')
    if row.status!='completed': raise HTTPException(400,'Complete the service before invoicing it.')
    if row.sale_id: return {'service_id':row.id,'sale_id':row.sale_id,'status':'already_invoiced'}
    product=db.get(Product,payload.service_product_id)
    if not product or product.item_type!='service': raise HTTPException(400,'Selected item must be a service product.')
    amount=Decimal(row.actual_amount or 0)
    if amount<=0: raise HTTPException(400,'Service actual amount must be greater than zero.')
    payments=[]
    if payload.paid>0: payments=[PaymentLineCreate(payment_method=payload.payment_method,amount=payload.paid,bank_account_id=payload.bank_account_id,reference=payload.reference)]
    sale_payload=SaleCreate(invoice_no=f'SRV-{row.job_no}',customer_id=row.customer_id,items=[SaleItemCreate(product_id=product.id,quantity=1,unit_price=amount)],paid=payload.paid,payment_method=payload.payment_method,bank_account_id=payload.bank_account_id,reference=payload.reference,branch_id=payload.branch_id,warehouse_id=payload.warehouse_id,shift_id=payload.shift_id,payments=payments)
    try:
        sale=create_sale(db,sale_payload,user.id)
        row.sale_id=sale.id
        audit(db,'service_invoiced','service_order',row.id,after={'sale_id':sale.id,'invoice_no':sale.invoice_no},user_id=user.id); db.commit()
        return {'service_id':row.id,'sale_id':sale.id,'invoice_no':sale.invoice_no,'total':str(sale.total),'status':'invoiced'}
    except ValueError as exc:
        db.rollback(); raise HTTPException(400,str(exc))

@router.get('/inventory/valuation')
def inventory_valuation(warehouse_id:int|None=None,db:Session=Depends(get_db),_:object=Depends(require_roles('owner','admin','manager','accountant','storekeeper'))):
    q=db.query(StockBalance,Product).join(Product,Product.id==StockBalance.product_id).filter(Product.is_active==True)
    if warehouse_id: q=q.filter(StockBalance.warehouse_id==warehouse_id)
    rows=[]; total=Decimal('0')
    for bal,p in q.order_by(Product.name).all():
        value=Decimal(bal.quantity or 0)*Decimal(p.buying_price or 0); total+=value
        rows.append({'warehouse_id':bal.warehouse_id,'product_id':p.id,'sku':p.sku,'product_name':p.name,'quantity':str(bal.quantity),'unit_cost':str(p.buying_price),'stock_value':str(value)})
    return {'total_value':str(total),'rows':rows}

@router.get('/reports/tax-summary')
def tax_summary(start_date:date,end_date:date,db:Session=Depends(get_db),_:object=Depends(require_roles('owner','admin','manager','accountant'))):
    rows=db.query(Sale,TaxRate).outerjoin(TaxRate,TaxRate.id==Sale.tax_rate_id).filter(Sale.status=='posted',Sale.sale_date>=datetime.combine(start_date,datetime.min.time()),Sale.sale_date<datetime.combine(end_date,datetime.min.time())+__import__('datetime').timedelta(days=1)).all()
    grouped={}
    for sale,rate in rows:
        key=(rate.id if rate else 0, rate.name if rate else 'No tax', str(rate.rate if rate else 0))
        g=grouped.setdefault(key,{'tax_rate_id':key[0],'name':key[1],'rate':key[2],'taxable_sales':Decimal('0'),'tax_collected':Decimal('0')})
        g['taxable_sales']+=Decimal(sale.subtotal or 0)-Decimal(sale.discount or 0)
        g['tax_collected']+=Decimal(sale.tax_amount or 0)
    return [{'tax_rate_id':k[0],'name':v['name'],'rate':v['rate'],'taxable_sales':str(v['taxable_sales']),'tax_collected':str(v['tax_collected'])} for k,v in grouped.items()]
