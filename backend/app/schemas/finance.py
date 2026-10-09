from decimal import Decimal
from pydantic import BaseModel, Field
class PurchasePaymentLine(BaseModel):
    payment_method:str=Field(pattern='^(cash|bank|mobile_money|card|cheque|credit)$')
    amount:Decimal=Field(gt=0)
    bank_account_id:int|None=None
    reference:str|None=Field(default=None,max_length=160)
    external_reference:str|None=Field(default=None,max_length=160)

class PurchaseItemCreate(BaseModel):
    product_id:int
    quantity:Decimal=Field(gt=0)
    unit_price:Decimal=Field(ge=0)
    unit_id:int|None=None
    batch_no:str|None=None
    expiry_date:str|None=None
class PurchaseCreate(BaseModel):
    invoice_no:str=Field(min_length=1,max_length=80)
    supplier_id:int|None=None
    items:list[PurchaseItemCreate]
    paid:Decimal=Field(default=0,ge=0)
    payment_method:str=Field(default='cash',pattern='^(cash|bank|mobile_money|card|cheque|credit|split)$')
    bank_account_id:int|None=None
    reference:str|None=Field(default=None,max_length=120)
    branch_id:int|None=None
    warehouse_id:int|None=None
    payments:list[PurchasePaymentLine]=[]
class ExpenseCreate(BaseModel):
    category:str=Field(min_length=1,max_length=100)
    amount:Decimal=Field(gt=0)
    payment_method:str=Field(default='cash',pattern='^(cash|bank|mobile_money|card|cheque)$')
    bank_account_id:int|None=None
    description:str|None=None
    reference:str|None=Field(default=None,max_length=120)
    expense_date:str|None=None
class CustomerPaymentCreate(BaseModel):
    customer_id:int
    sale_id:int|None=None
    amount:Decimal=Field(gt=0)
    payment_method:str=Field(default='cash',pattern='^(cash|bank|mobile_money|card|cheque)$')
    bank_account_id:int|None=None
    reference:str|None=Field(default=None,max_length=120)
    notes:str|None=Field(default=None,max_length=500)
class SupplierPaymentCreate(BaseModel):
    supplier_id:int
    purchase_id:int|None=None
    amount:Decimal=Field(gt=0)
    payment_method:str=Field(default='cash',pattern='^(cash|bank|mobile_money|card|cheque)$')
    bank_account_id:int|None=None
    reference:str|None=Field(default=None,max_length=120)
    notes:str|None=Field(default=None,max_length=500)
class ReturnItemCreate(BaseModel):
    product_id:int
    quantity:Decimal=Field(gt=0)
    unit_id:int|None=None
class SalesReturnCreate(BaseModel):
    sale_id:int
    items:list[ReturnItemCreate]
    reason:str
    refund_method:str=Field(default='credit',pattern='^(cash|bank|credit)$')
    bank_account_id:int|None=None
class PurchaseReturnCreate(BaseModel):
    purchase_id:int
    items:list[ReturnItemCreate]
    reason:str
    settlement_method:str=Field(default='credit',pattern='^(cash|bank|credit)$')
    bank_account_id:int|None=None


class PurchaseOrderItemCreate(BaseModel):
    product_id:int
    quantity:Decimal=Field(gt=0)
    unit_price:Decimal=Field(ge=0)
    unit_id:int|None=None
    batch_no:str|None=None
    expiry_date:str|None=None

class PurchaseOrderCreate(BaseModel):
    po_no:str|None=Field(default=None,max_length=80)
    supplier_id:int|None=None
    branch_id:int|None=None
    warehouse_id:int|None=None
    expected_date:str|None=None
    discount:Decimal=Field(default=0,ge=0)
    items:list[PurchaseOrderItemCreate]=Field(min_length=1)
    notes:str|None=None

class PurchaseOrderStatusUpdate(BaseModel):
    status:str=Field(pattern='^(draft|approved|received|cancelled)$')

class PurchaseOrderReceiveIn(BaseModel):
    invoice_no:str=Field(min_length=1,max_length=80)
    warehouse_id:int|None=None
    paid:Decimal=Field(default=0,ge=0)
    payment_method:str=Field(default='credit',pattern='^(cash|bank|mobile_money|card|cheque|credit)$')
    bank_account_id:int|None=None
    reference:str|None=Field(default=None,max_length=120)
    payments:list[PurchasePaymentLine]=[]
