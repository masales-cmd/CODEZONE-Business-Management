import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import current_user, require_roles
from app.models.models import Branch, Warehouse, StockBalance, StockTransfer, StockTransferItem, Product, PriceList, PriceListItem, PaymentMethod, Shift, ProductBatch, ProductSerial, Recipe, RecipeItem, ProductionOrder, OnlineOrder, OnlineOrderItem, Device, SyncOutbox, IntegrationConnection, Notification, Sale, CashTransaction

router=APIRouter(prefix='/api/platform',tags=['platform'],dependencies=[Depends(current_user)])

def now(): return datetime.now(timezone.utc).replace(tzinfo=None)
class BranchIn(BaseModel): code:str=Field(min_length=1,max_length=40); name:str=Field(min_length=2,max_length=150); phone:str|None=None; address:str|None=None; is_default:bool=False
class WarehouseIn(BaseModel): code:str=Field(min_length=1,max_length=40); name:str=Field(min_length=2,max_length=150); branch_id:int|None=None; is_default:bool=False
class StockIn(BaseModel): warehouse_id:int; product_id:int; quantity:Decimal=Field(ge=0); reorder_level:Decimal=Field(default=0,ge=0)
class TransferIn(BaseModel): from_warehouse_id:int; to_warehouse_id:int; items:list[dict]; notes:str|None=None
class PriceListIn(BaseModel): name:str; code:str; currency:str='TZS'; is_default:bool=False
class PriceItemIn(BaseModel): product_id:int; price:Decimal=Field(gt=0); unit_id:int|None=None
class ShiftOpenIn(BaseModel): branch_id:int|None=None; opening_cash:Decimal=Field(default=0,ge=0)
class ShiftCloseIn(BaseModel): counted_cash:Decimal=Field(ge=0); notes:str|None=None
class BatchIn(BaseModel): product_id:int; batch_no:str; expiry_date:str|None=None; quantity:Decimal=Field(default=0,ge=0); cost_price:Decimal=Field(default=0,ge=0)
class SerialIn(BaseModel): product_id:int; serial_no:str; warranty_until:str|None=None
class OnlineOrderIn(BaseModel): customer_id:int|None=None; channel:str='web'; items:list[dict]; notes:str|None=None
class DeviceIn(BaseModel): device_code:str; name:str; device_type:str='desktop'

@router.get('/branches')
def branches(db:Session=Depends(get_db),_:object=Depends(current_user)):
    return [{'id':x.id,'code':x.code,'name':x.name,'phone':x.phone,'address':x.address,'is_default':x.is_default,'is_active':x.is_active} for x in db.query(Branch).order_by(Branch.name).all()]
@router.post('/branches')
def create_branch(payload:BranchIn,db:Session=Depends(get_db),_:object=Depends(require_roles('owner','admin'))):
    if db.query(Branch).filter(Branch.code==payload.code).first(): raise HTTPException(409,'Branch code already exists.')
    if payload.is_default: db.query(Branch).update({Branch.is_default:False})
    b=Branch(**payload.model_dump()); db.add(b); db.commit(); db.refresh(b); return {'id':b.id,'code':b.code,'name':b.name,'is_default':b.is_default}
@router.get('/warehouses')
def warehouses(db:Session=Depends(get_db),_:object=Depends(current_user)):
    return [{'id':w.id,'code':w.code,'name':w.name,'branch_id':w.branch_id,'is_default':w.is_default,'is_active':w.is_active} for w in db.query(Warehouse).order_by(Warehouse.name).all()]
@router.post('/warehouses')
def create_warehouse(payload:WarehouseIn,db:Session=Depends(get_db),_:object=Depends(require_roles('owner','admin','manager'))):
    if db.query(Warehouse).filter(Warehouse.code==payload.code).first(): raise HTTPException(409,'Warehouse code already exists.')
    if payload.is_default: db.query(Warehouse).update({Warehouse.is_default:False})
    w=Warehouse(**payload.model_dump()); db.add(w); db.commit(); db.refresh(w); return {'id':w.id,'code':w.code,'name':w.name,'branch_id':w.branch_id,'is_default':w.is_default}
@router.get('/stock')
def stock(warehouse_id:int|None=None,q:str|None=None,db:Session=Depends(get_db),_:object=Depends(current_user)):
    query=db.query(StockBalance,Product).join(Product,Product.id==StockBalance.product_id).filter(Product.is_active==True)
    if warehouse_id: query=query.filter(StockBalance.warehouse_id==warehouse_id)
    if q:
        like=f'%{q.strip()}%'; query=query.filter(Product.name.ilike(like)|Product.sku.ilike(like))
    return [{'id':s.id,'warehouse_id':s.warehouse_id,'product_id':p.id,'sku':p.sku,'product_name':p.name,'quantity':str(s.quantity),'reserved':str(s.reserved_quantity),'reorder_level':str(s.reorder_level)} for s,p in query.order_by(Product.name).limit(2000).all()]
@router.post('/stock/seed')
def seed_stock(payload:StockIn,db:Session=Depends(get_db),_:object=Depends(require_roles('owner','admin','manager','storekeeper'))):
    product=db.get(Product,payload.product_id); warehouse=db.get(Warehouse,payload.warehouse_id)
    if not product or not warehouse: raise HTTPException(404,'Product or warehouse not found.')
    row=db.query(StockBalance).filter_by(product_id=product.id,warehouse_id=warehouse.id).first()
    if not row: row=StockBalance(product_id=product.id,warehouse_id=warehouse.id,quantity=payload.quantity,reorder_level=payload.reorder_level); db.add(row)
    else: row.quantity=payload.quantity; row.reorder_level=payload.reorder_level
    total=db.query(func.coalesce(func.sum(StockBalance.quantity),0)).filter(StockBalance.product_id==product.id).scalar() or 0
    product.current_stock=total
    db.commit(); return {'product_id':product.id,'warehouse_id':warehouse.id,'quantity':str(row.quantity),'total_stock':str(total)}
@router.post('/transfers')
def create_transfer(payload:TransferIn,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','manager','storekeeper'))):
    if payload.from_warehouse_id==payload.to_warehouse_id: raise HTTPException(400,'Source and destination warehouses must differ.')
    if not db.get(Warehouse,payload.from_warehouse_id) or not db.get(Warehouse,payload.to_warehouse_id): raise HTTPException(404,'Warehouse not found.')
    t=StockTransfer(transfer_no=f"TR-{now().strftime('%Y%m%d')}-{uuid4().hex[:6].upper()}",from_warehouse_id=payload.from_warehouse_id,to_warehouse_id=payload.to_warehouse_id,requested_by=user.id,notes=payload.notes)
    db.add(t); db.flush()
    for item in payload.items:
        pid=int(item['product_id']); qty=Decimal(str(item['quantity']))
        if qty<=0: raise HTTPException(400,'Transfer quantity must be greater than zero.')
        src=db.query(StockBalance).filter_by(warehouse_id=t.from_warehouse_id,product_id=pid).first()
        if not src or Decimal(src.quantity)<qty: raise HTTPException(400,f'Insufficient stock for product {pid} at source warehouse.')
        db.add(StockTransferItem(transfer_id=t.id,product_id=pid,quantity=qty))
    db.commit(); db.refresh(t); return {'id':t.id,'transfer_no':t.transfer_no,'status':t.status}
@router.put('/transfers/{transfer_id}/status')
def transfer_status(transfer_id:int,status:str,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','manager','storekeeper'))):
    t=db.get(StockTransfer,transfer_id)
    if not t: raise HTTPException(404,'Transfer not found.')
    valid={'draft':{'approved','cancelled'},'approved':{'dispatched','cancelled'},'dispatched':{'received'},'received':set(),'cancelled':set()}
    if status not in valid.get(t.status,set()):
        raise HTTPException(400,f'Invalid transfer transition: {t.status} -> {status}.')
    items=db.query(StockTransferItem).filter_by(transfer_id=t.id).all()
    if status=='approved':
        t.approved_by=user.id
    elif status=='dispatched':
        for i in items:
            src=db.query(StockBalance).filter_by(warehouse_id=t.from_warehouse_id,product_id=i.product_id).with_for_update().first()
            if not src or Decimal(src.quantity)<Decimal(i.quantity):
                raise HTTPException(400,f'Insufficient source stock for product {i.product_id}.')
            src.quantity-=Decimal(i.quantity)
            p=db.get(Product,i.product_id)
            p.current_stock=max(Decimal('0'),Decimal(p.current_stock or 0)-Decimal(i.quantity))
            db.add(__import__('app.models.models',fromlist=['StockMovement']).StockMovement(product_id=i.product_id,movement_type='transfer_out',quantity=-Decimal(i.quantity),reference_type='stock_transfer',reference_id=t.id))
    elif status=='received':
        for i in items:
            dst=db.query(StockBalance).filter_by(warehouse_id=t.to_warehouse_id,product_id=i.product_id).with_for_update().first()
            if not dst:
                dst=StockBalance(warehouse_id=t.to_warehouse_id,product_id=i.product_id,quantity=0,reorder_level=0); db.add(dst); db.flush()
            dst.quantity+=Decimal(i.quantity)
            p=db.get(Product,i.product_id)
            p.current_stock=Decimal(p.current_stock or 0)+Decimal(i.quantity)
            db.add(__import__('app.models.models',fromlist=['StockMovement']).StockMovement(product_id=i.product_id,movement_type='transfer_in',quantity=Decimal(i.quantity),reference_type='stock_transfer',reference_id=t.id))
        t.received_at=now()
    t.status=status
    db.commit(); return {'id':t.id,'status':t.status}

@router.get('/price-lists')
def price_lists(db:Session=Depends(get_db),_:object=Depends(current_user)):
    return [{'id':x.id,'name':x.name,'code':x.code,'currency':x.currency,'is_default':x.is_default,'is_active':x.is_active} for x in db.query(PriceList).order_by(PriceList.name).all()]
@router.post('/price-lists')
def create_price_list(payload:PriceListIn,db:Session=Depends(get_db),_:object=Depends(require_roles('owner','admin','manager'))):
    if db.query(PriceList).filter(PriceList.code==payload.code).first(): raise HTTPException(409,'Price list code already exists.')
    if payload.is_default: db.query(PriceList).update({PriceList.is_default:False})
    row=PriceList(**payload.model_dump()); db.add(row); db.commit(); db.refresh(row); return {'id':row.id,'name':row.name,'code':row.code}
@router.post('/price-lists/{price_list_id}/items')
def upsert_price_item(price_list_id:int,payload:PriceItemIn,db:Session=Depends(get_db),_:object=Depends(require_roles('owner','admin','manager'))):
    if not db.get(PriceList,price_list_id) or not db.get(Product,payload.product_id): raise HTTPException(404,'Price list or product not found.')
    row=db.query(PriceListItem).filter_by(price_list_id=price_list_id,product_id=payload.product_id,unit_id=payload.unit_id).first()
    if not row: row=PriceListItem(price_list_id=price_list_id,product_id=payload.product_id,unit_id=payload.unit_id,price=payload.price); db.add(row)
    else: row.price=payload.price
    db.commit(); return {'id':row.id,'price':str(row.price)}
@router.get('/payment-methods')
def payment_methods(db:Session=Depends(get_db),_:object=Depends(current_user)):
    return [{'id':x.id,'code':x.code,'name':x.name,'method_type':x.method_type,'is_active':x.is_active,'sort_order':x.sort_order} for x in db.query(PaymentMethod).filter(PaymentMethod.is_active==True).order_by(PaymentMethod.sort_order,PaymentMethod.name).all()]
@router.get('/shift/current')
def current_shift(db:Session=Depends(get_db),user=Depends(current_user)):
    s=db.query(Shift).filter(Shift.user_id==user.id,Shift.status=='open').order_by(Shift.opened_at.desc()).first()
    if not s:
        return None
    branch=db.get(Branch,s.branch_id) if s.branch_id else None
    sales_rows=db.query(Sale).filter(Sale.shift_id==s.id,Sale.status=='posted').all()
    cash_rows=db.query(CashTransaction).filter(CashTransaction.shift_id==s.id).all()
    cash_after=sum((Decimal(str(x.amount)) for x in cash_rows),Decimal('0'))
    expected=Decimal(s.opening_cash or 0)+cash_after
    by_method={}
    for sale in sales_rows:
        by_method[sale.payment_method]=(by_method.get(sale.payment_method,Decimal('0'))+Decimal(sale.paid or 0))
    return {'id':s.id,'branch_id':s.branch_id,'branch_name':branch.name if branch else None,'user_id':user.id,'user_name':getattr(user,'name',None),'opened_at':s.opened_at.isoformat()+'Z','opening_cash':str(s.opening_cash),'expected_cash':str(expected),'sales_count':len(sales_rows),'sales_total':str(sum((Decimal(x.total or 0) for x in sales_rows),Decimal('0'))),'paid_total':str(sum((Decimal(x.paid or 0) for x in sales_rows),Decimal('0'))),'payments_by_method':[{'method':k.replace('_',' ').title(),'amount':str(v)} for k,v in sorted(by_method.items())],'running_difference':None,'status':s.status}

@router.post('/shift/open')
def open_shift(payload:ShiftOpenIn,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','manager','cashier','salesperson'))):
    if db.query(Shift).filter(Shift.user_id==user.id,Shift.status=='open').first():
        raise HTTPException(409,'You already have an open shift. Close it before opening another one.')
    branch_id=payload.branch_id
    if branch_id is None:
        default_branch=db.query(Branch).filter(Branch.is_active==True,Branch.is_default==True).first() or db.query(Branch).filter(Branch.is_active==True).order_by(Branch.id).first()
        branch_id=default_branch.id if default_branch else None
    if branch_id is not None and not db.query(Branch).filter(Branch.id==branch_id,Branch.is_active==True).first():
        raise HTTPException(404,'Branch not found or inactive.')
    s=Shift(branch_id=branch_id,user_id=user.id,opening_cash=payload.opening_cash); db.add(s); db.commit(); db.refresh(s)
    return {'id':s.id,'branch_id':s.branch_id,'status':s.status,'opened_at':s.opened_at.isoformat()+'Z','opening_cash':str(s.opening_cash)}

@router.post('/shift/{shift_id}/close')
def close_shift(shift_id:int,payload:ShiftCloseIn,db:Session=Depends(get_db),user=Depends(require_roles('owner','admin','manager','cashier','salesperson'))):
    s=db.get(Shift,shift_id)
    if not s or s.user_id!=user.id or s.status!='open': raise HTTPException(404,'Open shift not found.')
    cash_rows=db.query(CashTransaction).filter(CashTransaction.shift_id==s.id).all()
    cash_after=sum((Decimal(str(x.amount)) for x in cash_rows),Decimal('0'))
    s.expected_cash=Decimal(s.opening_cash or 0)+cash_after
    s.counted_cash=payload.counted_cash
    s.difference=payload.counted_cash-s.expected_cash
    s.closed_at=now(); s.notes=payload.notes; s.status='closed'
    db.commit()
    return {'id':s.id,'expected_cash':str(s.expected_cash),'counted_cash':str(s.counted_cash),'difference':str(s.difference),'status':s.status,'closed_at':s.closed_at.isoformat()+'Z'}

@router.get('/batches')
def batches(product_id:int|None=None,db:Session=Depends(get_db),_:object=Depends(current_user)):
    q=db.query(ProductBatch,Product.name).join(Product,Product.id==ProductBatch.product_id)
    if product_id:q=q.filter(ProductBatch.product_id==product_id)
    return [{'id':b.id,'product_id':b.product_id,'product_name':name,'batch_no':b.batch_no,'expiry_date':b.expiry_date.isoformat() if b.expiry_date else None,'quantity':str(b.quantity),'cost_price':str(b.cost_price),'is_active':b.is_active} for b,name in q.order_by(ProductBatch.expiry_date.nulls_last(),Product.name).all()]
@router.post('/batches')
def create_batch(payload:BatchIn,db:Session=Depends(get_db),_:object=Depends(require_roles('owner','admin','manager','storekeeper'))):
    if not db.get(Product,payload.product_id): raise HTTPException(404,'Product not found.')
    exp=datetime.fromisoformat(payload.expiry_date).date() if payload.expiry_date else None
    b=ProductBatch(product_id=payload.product_id,batch_no=payload.batch_no,expiry_date=exp,quantity=payload.quantity,cost_price=payload.cost_price); db.add(b); db.commit(); db.refresh(b); return {'id':b.id,'batch_no':b.batch_no}
@router.post('/serials')
def create_serial(payload:SerialIn,db:Session=Depends(get_db),_:object=Depends(require_roles('owner','admin','manager','storekeeper'))):
    if not db.get(Product,payload.product_id): raise HTTPException(404,'Product not found.')
    try: warranty=datetime.fromisoformat(payload.warranty_until).date() if payload.warranty_until else None
    except ValueError: raise HTTPException(400,'Invalid warranty date.')
    if db.query(ProductSerial).filter(ProductSerial.serial_no==payload.serial_no).first(): raise HTTPException(409,'Serial number already exists.')
    s=ProductSerial(product_id=payload.product_id,serial_no=payload.serial_no,warranty_until=warranty); db.add(s); db.commit(); return {'serial_no':s.serial_no,'status':s.status}
@router.get('/devices')
def devices(db:Session=Depends(get_db),_:object=Depends(require_roles('owner','admin'))):
    return [{'id':d.id,'device_code':d.device_code,'name':d.name,'device_type':d.device_type,'last_seen_at':d.last_seen_at.isoformat()+'Z' if d.last_seen_at else None,'is_active':d.is_active} for d in db.query(Device).order_by(Device.name).all()]
@router.post('/devices')
def register_device(payload:DeviceIn,db:Session=Depends(get_db),_:object=Depends(require_roles('owner','admin'))):
    row=db.query(Device).filter(Device.device_code==payload.device_code).first()
    if not row: row=Device(**payload.model_dump()); db.add(row)
    else: row.name=payload.name; row.device_type=payload.device_type; row.is_active=True
    row.last_seen_at=now(); db.commit(); db.refresh(row); return {'id':row.id,'device_code':row.device_code}
@router.get('/sync/outbox')
def sync_outbox(limit:int=100,db:Session=Depends(get_db),_:object=Depends(require_roles('owner','admin'))):
    rows=db.query(SyncOutbox).filter(SyncOutbox.synced_at.is_(None)).order_by(SyncOutbox.id).limit(min(limit,500)).all()
    return [{'id':r.id,'event_id':r.event_id,'entity_type':r.entity_type,'entity_id':r.entity_id,'action':r.action,'payload':json.loads(r.payload_json),'created_at':r.created_at.isoformat()+'Z','attempts':r.attempts} for r in rows]
@router.post('/sync/ack')
def sync_ack(ids:list[int],db:Session=Depends(get_db),_:object=Depends(require_roles('owner','admin'))):
    rows=db.query(SyncOutbox).filter(SyncOutbox.id.in_(ids)).all(); ts=now()
    for r in rows:r.synced_at=ts
    db.commit(); return {'acknowledged':len(rows)}
@router.get('/online-orders')
def online_orders(db:Session=Depends(get_db),_:object=Depends(require_roles('owner','admin','manager','salesperson'))):
    return [{'id':o.id,'order_no':o.order_no,'customer_id':o.customer_id,'channel':o.channel,'status':o.status,'total':str(o.total),'created_at':o.created_at.isoformat()+'Z'} for o in db.query(OnlineOrder).order_by(OnlineOrder.created_at.desc()).limit(500).all()]
@router.post('/online-orders')
def create_online_order(payload:OnlineOrderIn,db:Session=Depends(get_db),_:object=Depends(current_user)):
    total=Decimal('0'); order=OnlineOrder(order_no=f"WEB-{now().strftime('%Y%m%d')}-{uuid4().hex[:7].upper()}",customer_id=payload.customer_id,channel=payload.channel,payload_json=json.dumps({'notes':payload.notes})); db.add(order); db.flush()
    for x in payload.items:
        product=db.get(Product,int(x['product_id'])); qty=Decimal(str(x['quantity'])); price=Decimal(str(x.get('unit_price',product.selling_price if product else 0)))
        if not product or qty<=0 or price<=0: raise HTTPException(400,'Invalid online order item.')
        line=qty*price; total+=line; db.add(OnlineOrderItem(order_id=order.id,product_id=product.id,quantity=qty,unit_price=price,line_total=line))
    order.total=total; db.commit(); db.refresh(order); return {'id':order.id,'order_no':order.order_no,'total':str(order.total),'status':order.status}
@router.get('/notifications')
def notifications(db:Session=Depends(get_db),user=Depends(current_user)):
    return [{'id':n.id,'kind':n.kind,'title':n.title,'message':n.message,'is_read':n.is_read,'created_at':n.created_at.isoformat()+'Z'} for n in db.query(Notification).filter((Notification.user_id==user.id)|(Notification.user_id.is_(None))).order_by(Notification.created_at.desc()).limit(100).all()]
@router.get('/capabilities')
def capabilities(db:Session=Depends(get_db)):
    from app.core.industry_profiles import INDUSTRY_PROFILES
    return {'version':'1.10.2','phases':['platform foundation','core business operations','industry modules','cloud & intelligence'],'industries':INDUSTRY_PROFILES,'capabilities':['multi-location','price-lists','payment-methods','shifts','batch-tracking','serial-tracking','recipes','production','delivery','service-orders','loyalty','tax-engine','approvals','warehouse-bins','online-orders','sync-outbox','devices','notifications','integrations','team-messaging','task-assignment']}
