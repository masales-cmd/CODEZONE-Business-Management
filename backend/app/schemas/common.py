from decimal import Decimal
from datetime import date
from pydantic import BaseModel,Field,model_validator
class ProductUpdate(BaseModel):
    sku:str|None=Field(default=None,min_length=1,max_length=80)
    name:str|None=Field(default=None,min_length=1,max_length=180)
    buying_price:Decimal|None=Field(default=None,ge=0)
    selling_price:Decimal|None=Field(default=None,gt=0)
    minimum_stock:Decimal|None=Field(default=None,ge=0)
    reorder_level:Decimal|None=Field(default=None,ge=0)
    item_type:str|None=Field(default=None,pattern='^(product|service)$')
    barcode:str|None=Field(default=None,max_length=120)
    brand:str|None=Field(default=None,max_length=120)
    track_inventory:bool|None=None

class ProductCreate(BaseModel):
    sku:str=Field(min_length=1,max_length=80); name:str=Field(min_length=1,max_length=180); item_type:str=Field(default='product',pattern='^(product|service)$'); barcode:str|None=Field(default=None,max_length=120); brand:str|None=Field(default=None,max_length=120); track_inventory:bool=True; buying_price:Decimal=Field(default=Decimal('0'),ge=0); selling_price:Decimal=Field(gt=0); minimum_stock:Decimal=Field(default=Decimal('0'),ge=0); reorder_level:Decimal=Field(default=Decimal('0'),ge=0); current_stock:Decimal=Field(default=Decimal('0'),ge=0)
    @model_validator(mode='after')
    def selling_price_covers_cost(self):
        if self.selling_price < self.buying_price:
            raise ValueError('Selling price cannot be below buying price.')
        return self
class PaymentLineCreate(BaseModel):
    payment_method:str=Field(pattern='^(cash|bank|mobile_money|card|cheque|credit)$')
    amount:Decimal=Field(gt=0)
    bank_account_id:int|None=None
    reference:str|None=Field(default=None,max_length=160)
    external_reference:str|None=Field(default=None,max_length=160)

class SaleItemCreate(BaseModel):
    product_id:int; quantity:Decimal=Field(gt=0); unit_price:Decimal=Field(default=0,ge=0); unit_id:int|None=None; batch_id:int|None=None; serial_id:int|None=None
class SaleCreate(BaseModel):
    invoice_no:str|None=Field(default=None,max_length=80)
    customer_id:int|None=None
    items:list[SaleItemCreate]=Field(min_length=1)
    discount:Decimal=Field(default=0,ge=0)
    paid:Decimal=Field(default=0,ge=0)
    payment_method:str=Field(default='cash',pattern='^(cash|bank|mobile_money|card|cheque|credit|split)$')
    bank_account_id:int|None=None
    reference:str|None=Field(default=None,max_length=120)
    allow_below_cost:bool=False
    container_type_id:int|None=None
    containers_issued:Decimal=Field(default=0,ge=0)
    containers_returned:Decimal=Field(default=0,ge=0)
    branch_id:int|None=None
    warehouse_id:int|None=None
    price_list_id:int|None=None
    shift_id:int|None=None
    tax_rate_id:int|None=None
    payments:list[PaymentLineCreate]=Field(default_factory=list)



class UnitCreate(BaseModel):
    name:str=Field(min_length=1,max_length=80); symbol:str=Field(min_length=1,max_length=20)
class ConversionCreate(BaseModel):
    product_id:int; from_unit_id:int; to_unit_id:int; factor:Decimal=Field(gt=0)


class BusinessProfileCreate(BaseModel):
    industry_profile:str=Field(default='retail',pattern='^(retail|wholesale|restaurant|pharmacy|electronics|hardware|services|manufacturing|beverage|other)$')

class CustomerCreate(BaseModel):
    name:str=Field(min_length=2,max_length=180)
    phone:str|None=Field(default=None,max_length=50)
    address:str|None=Field(default=None,max_length=255)
    credit_limit:Decimal=Field(default=0,ge=0)
    payment_terms_days:int=Field(default=0,ge=0,le=3650)

class SupplierCreate(BaseModel):
    name:str=Field(min_length=2,max_length=180)
    phone:str|None=Field(default=None,max_length=50)
    address:str|None=Field(default=None,max_length=255)
    payment_terms_days:int=Field(default=0,ge=0,le=3650)

class BankCreate(BaseModel):
    bank_name:str=Field(min_length=2,max_length=120)
    account_name:str=Field(min_length=2,max_length=180)
    account_number:str=Field(min_length=3,max_length=100)
    opening_balance:Decimal=Field(default=0,ge=0)

class UnitCreatePayload(BaseModel):
    name:str=Field(min_length=1,max_length=80)
    symbol:str=Field(min_length=1,max_length=20)

class ContainerCreate(BaseModel):
    name:str=Field(min_length=1,max_length=100)
    symbol:str=Field(min_length=1,max_length=20)
    deposit_value:Decimal=Field(default=0,ge=0)
    current_stock:Decimal=Field(default=Decimal('0'),ge=0)

class OpeningBalancesApply(BaseModel):
    cash_opening_balance: Decimal = Field(default=0, ge=0)

class OpeningBalanceSet(BaseModel):
    entity_type:str=Field(pattern='^(customer|supplier|bank)$')
    entity_id:int
    amount:Decimal=Field(ge=0)

class QuoteItemCreate(BaseModel):
    product_id:int
    quantity:Decimal=Field(gt=0)
    unit_price:Decimal=Field(gt=0)
    unit_id:int|None=None

class QuoteCreate(BaseModel):
    quote_no:str|None=Field(default=None,max_length=80)
    customer_id:int|None=None
    valid_until:date|None=None
    items:list[QuoteItemCreate]=Field(min_length=1)
    discount:Decimal=Field(default=0,ge=0)
    notes:str|None=Field(default=None,max_length=1000)

class QuoteStatusUpdate(BaseModel):
    status:str=Field(pattern='^(draft|sent|accepted|rejected|expired)$')

class OrderStatusUpdate(BaseModel):
    status:str=Field(pattern='^(draft|confirmed|cancelled|fulfilled)$')

class OrderConvertToSale(BaseModel):
    paid:Decimal=Field(default=0,ge=0)
    payment_method:str=Field(default='cash',pattern='^(cash|bank|mobile_money|card|cheque)$')
    bank_account_id:int|None=None
    reference:str|None=Field(default=None,max_length=120)
    payments:list[PaymentLineCreate]=Field(default_factory=list)

