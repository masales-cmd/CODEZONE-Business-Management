from datetime import datetime, timezone
import json
from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import current_user, require_roles
from app.models.models import Device, SyncOutbox, OnlineOrder, OnlineOrderItem, Customer, Product
from app.services.transactions import audit
from app.services.sales import create_sale
from app.schemas.common import SaleCreate, SaleItemCreate, PaymentLineCreate

router = APIRouter(prefix='/api/cloud', tags=['cloud'], dependencies=[Depends(current_user)])

class DeviceHeartbeatIn(BaseModel):
    device_code: str = Field(min_length=2, max_length=80)

class SyncAckIn(BaseModel):
    event_ids: list[str] = Field(min_length=1, max_length=500)

class OnlineOrderItemIn(BaseModel):
    product_id: int
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)

class OnlineOrderIn(BaseModel):
    order_no: str = Field(min_length=2, max_length=80)
    customer_id: int | None = None
    channel: str = Field(default='web', max_length=40)
    items: list[OnlineOrderItemIn] = Field(min_length=1)
    payload: dict = {}

@router.get('/health')
def cloud_health(_: object = Depends(current_user)):
    return {'status': 'ok', 'mode': 'sync-ready', 'timestamp': datetime.now(timezone.utc).isoformat()}

@router.post('/devices/heartbeat')
def device_heartbeat(payload: DeviceHeartbeatIn, db: Session = Depends(get_db), _: object = Depends(current_user)):
    d = db.query(Device).filter(Device.device_code == payload.device_code).first()
    if not d:
        raise HTTPException(404, 'Device not registered.')
    d.last_seen_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return {'device_code': d.device_code, 'last_seen_at': d.last_seen_at.isoformat()}

@router.get('/sync/pull')
def sync_pull(since: str | None = None, limit: int = 250, db: Session = Depends(get_db), _: object = Depends(current_user)):
    limit = max(1, min(limit, 500))
    q = db.query(SyncOutbox).filter(SyncOutbox.synced_at.is_(None)).order_by(SyncOutbox.id.asc())
    if since:
        try:
            dt = datetime.fromisoformat(since.replace('Z','+00:00')).replace(tzinfo=None)
            q = q.filter(SyncOutbox.created_at > dt)
        except ValueError:
            raise HTTPException(400, 'Invalid since timestamp.')
    rows = q.limit(limit).all()
    return {'events': [{'event_id':x.event_id,'entity_type':x.entity_type,'entity_id':x.entity_id,'action':x.action,'payload':json.loads(x.payload_json or '{}'),'created_at':x.created_at.isoformat(),'attempts':x.attempts} for x in rows], 'count': len(rows)}

@router.post('/sync/ack')
def sync_ack(payload: SyncAckIn, db: Session = Depends(get_db), _: object = Depends(require_roles('owner','admin','manager'))):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = db.query(SyncOutbox).filter(SyncOutbox.event_id.in_(payload.event_ids)).all()
    for x in rows:
        x.synced_at = now
        x.attempts = int(x.attempts or 0) + 1
    db.commit()
    return {'acknowledged': len(rows)}

@router.post('/online-orders')
def ingest_online_order(payload: OnlineOrderIn, db: Session = Depends(get_db), user = Depends(require_roles('owner','admin','manager','salesperson'))):
    if db.query(OnlineOrder).filter(OnlineOrder.order_no == payload.order_no).first():
        raise HTTPException(409, 'Online order number already exists.')
    if payload.customer_id and not db.get(Customer, payload.customer_id):
        raise HTTPException(404, 'Customer not found.')
    total = Decimal('0')
    for i in payload.items:
        if not db.get(Product, i.product_id):
            raise HTTPException(404, f'Product {i.product_id} not found.')
        total += i.quantity * i.unit_price
    row = OnlineOrder(order_no=payload.order_no, customer_id=payload.customer_id, channel=payload.channel, status='pending', total=total, payload_json=json.dumps(payload.payload or {}))
    db.add(row); db.flush()
    for i in payload.items:
        db.add(OnlineOrderItem(order_id=row.id, product_id=i.product_id, quantity=i.quantity, unit_price=i.unit_price, line_total=i.quantity*i.unit_price))
    db.add(SyncOutbox(event_id=str(uuid4()), entity_type='online_order', entity_id=row.id, action='created', payload_json=json.dumps({'order_no':row.order_no,'total':str(total)})))
    audit(db,'online_order_ingested','online_order',row.id,after={'order_no':row.order_no,'channel':row.channel},user_id=user.id)
    db.commit()
    return {'id':row.id,'order_no':row.order_no,'total':str(total),'status':row.status}


class OnlineOrderConvertIn(BaseModel):
    warehouse_id: int | None = None
    branch_id: int | None = None
    shift_id: int | None = None
    paid: Decimal = Field(default=0, ge=0)
    payment_method: str = Field(default='credit', pattern='^(cash|bank|mobile_money|card|cheque|credit)$')
    bank_account_id: int | None = None
    reference: str | None = Field(default=None, max_length=160)

@router.post('/online-orders/{order_id}/convert-to-sale')
def convert_online_order(order_id:int, payload:OnlineOrderConvertIn, db:Session=Depends(get_db), user=Depends(require_roles('owner','admin','manager','salesperson','cashier'))):
    order=db.get(OnlineOrder,order_id)
    if not order: raise HTTPException(404,'Online order not found.')
    if order.status in ('completed','cancelled'): raise HTTPException(400,'Online order is already finalized.')
    if order.customer_id is None: raise HTTPException(400,'Online order must have a customer before conversion.')
    items=db.query(OnlineOrderItem).filter(OnlineOrderItem.order_id==order.id).all()
    if not items: raise HTTPException(400,'Online order has no items.')
    payment_lines=[]
    if payload.paid>0:
        payment_lines=[PaymentLineCreate(payment_method=payload.payment_method,amount=payload.paid,bank_account_id=payload.bank_account_id,reference=payload.reference)]
    sale_payload=SaleCreate(
        invoice_no=f'WEB-{order.order_no}', customer_id=order.customer_id,
        items=[SaleItemCreate(product_id=i.product_id,quantity=i.quantity,unit_price=i.unit_price) for i in items],
        paid=payload.paid, payment_method=payload.payment_method, bank_account_id=payload.bank_account_id,
        reference=payload.reference, branch_id=payload.branch_id, warehouse_id=payload.warehouse_id, shift_id=payload.shift_id, payments=payment_lines
    )
    try:
        sale=create_sale(db,sale_payload,user.id)
        order.status='completed'
        order.payload_json=json.dumps({'converted_sale_id':sale.id,'converted_invoice_no':sale.invoice_no})
        db.add(SyncOutbox(event_id=str(uuid4()),entity_type='online_order',entity_id=order.id,action='converted',payload_json=json.dumps({'sale_id':sale.id,'invoice_no':sale.invoice_no})))
        audit(db,'online_order_converted','online_order',order.id,after={'sale_id':sale.id,'invoice_no':sale.invoice_no},user_id=user.id)
        db.commit()
        return {'id':sale.id,'invoice_no':sale.invoice_no,'order_no':order.order_no,'status':'completed'}
    except ValueError as exc:
        db.rollback(); raise HTTPException(400,str(exc))
