from datetime import datetime,date,timezone
from decimal import Decimal
from sqlalchemy import String,Boolean,DateTime,Date,ForeignKey,Numeric,Integer,Text,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column

def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)
from app.core.database import Base
class Role(Base):
    __tablename__='roles'; id:Mapped[int]=mapped_column(primary_key=True); name:Mapped[str]=mapped_column(String(80),unique=True)
class User(Base):
    __tablename__='users'; id:Mapped[int]=mapped_column(primary_key=True); name:Mapped[str]=mapped_column(String(150)); email:Mapped[str]=mapped_column(String(255),unique=True); password_hash:Mapped[str]=mapped_column(String(255)); role_id:Mapped[int|None]=mapped_column(ForeignKey('roles.id')); is_active:Mapped[bool]=mapped_column(Boolean,default=True); last_login_at:Mapped[datetime|None]=mapped_column(DateTime); failed_login_attempts:Mapped[int]=mapped_column(Integer,default=0); locked_until:Mapped[datetime|None]=mapped_column(DateTime)

class EmployeeProfile(Base):
    __tablename__='employee_profiles'
    id:Mapped[int]=mapped_column(primary_key=True)
    # HR employee profiles can exist without a CODEZONE login account.
    user_id:Mapped[int|None]=mapped_column(ForeignKey('users.id'),unique=True,nullable=True)
    full_name:Mapped[str|None]=mapped_column(String(150))
    company_email:Mapped[str|None]=mapped_column(String(255),unique=True)
    is_active:Mapped[bool]=mapped_column(Boolean,default=True,nullable=False)
    employee_number:Mapped[str|None]=mapped_column(String(40),unique=True)
    phone:Mapped[str|None]=mapped_column(String(50))
    department:Mapped[str|None]=mapped_column(String(100))
    job_title:Mapped[str|None]=mapped_column(String(100))
    employment_type:Mapped[str]=mapped_column(String(30),default='full_time',nullable=False)
    hire_date:Mapped[date|None]=mapped_column(Date)
    termination_date:Mapped[date|None]=mapped_column(Date)
    personal_email:Mapped[str|None]=mapped_column(String(255))
    notes:Mapped[str|None]=mapped_column(Text)
    mailbox_status:Mapped[str]=mapped_column(String(40),default='address_only',nullable=False)
    mailbox_provider:Mapped[str|None]=mapped_column(String(40))
    mailbox_last_error:Mapped[str|None]=mapped_column(String(250))
    created_at:Mapped[datetime]=mapped_column(DateTime,default=utcnow,nullable=False)
    updated_at:Mapped[datetime]=mapped_column(DateTime,default=utcnow,onupdate=utcnow,nullable=False)

class BusinessProfile(Base):
    __tablename__='business_profile'
    id:Mapped[int]=mapped_column(primary_key=True)
    business_name:Mapped[str]=mapped_column(String(180),nullable=False)
    owner_name:Mapped[str]=mapped_column(String(150),nullable=False)
    email:Mapped[str]=mapped_column(String(255),nullable=False)
    phone:Mapped[str|None]=mapped_column(String(50))
    address:Mapped[str|None]=mapped_column(String(255))
    tin:Mapped[str|None]=mapped_column(String(80))
    vat_number:Mapped[str|None]=mapped_column(String(80))
    currency:Mapped[str]=mapped_column(String(10),default='TZS',nullable=False)
    industry_profile:Mapped[str]=mapped_column(String(40),default='retail',nullable=False)
    email_domain:Mapped[str|None]=mapped_column(String(255))
    onboarding_completed:Mapped[bool]=mapped_column(Boolean,default=False,nullable=False)
    created_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow,nullable=False)
    updated_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow,onupdate=datetime.utcnow,nullable=False)

class Customer(Base):
    __tablename__='customers'; id:Mapped[int]=mapped_column(primary_key=True); name:Mapped[str]=mapped_column(String(180)); phone:Mapped[str|None]=mapped_column(String(50)); address:Mapped[str|None]=mapped_column(String(255)); credit_limit:Mapped[Decimal]=mapped_column(Numeric(14,2),default=0); payment_terms_days:Mapped[int]=mapped_column(Integer,default=0); opening_balance:Mapped[Decimal]=mapped_column(Numeric(14,2),default=0); group_id:Mapped[int|None]=mapped_column(ForeignKey('customer_groups.id')); is_active:Mapped[bool]=mapped_column(Boolean,default=True)
class Supplier(Base):
    __tablename__='suppliers'; id:Mapped[int]=mapped_column(primary_key=True); name:Mapped[str]=mapped_column(String(180)); phone:Mapped[str|None]=mapped_column(String(50)); address:Mapped[str|None]=mapped_column(String(255)); opening_balance:Mapped[Decimal]=mapped_column(Numeric(14,2),default=0); payment_terms_days:Mapped[int]=mapped_column(Integer,default=0); is_active:Mapped[bool]=mapped_column(Boolean,default=True)
class Category(Base):
    __tablename__='product_categories'; id:Mapped[int]=mapped_column(primary_key=True); name:Mapped[str]=mapped_column(String(120),unique=True)
class Unit(Base):
    __tablename__='units'; id:Mapped[int]=mapped_column(primary_key=True); name:Mapped[str]=mapped_column(String(80),unique=True); symbol:Mapped[str]=mapped_column(String(20),unique=True)
class Product(Base):
    __tablename__='products'; id:Mapped[int]=mapped_column(primary_key=True); sku:Mapped[str]=mapped_column(String(80),unique=True); name:Mapped[str]=mapped_column(String(180)); item_type:Mapped[str]=mapped_column(String(20),default='product',nullable=False); barcode:Mapped[str|None]=mapped_column(String(120),unique=True); brand:Mapped[str|None]=mapped_column(String(120)); track_inventory:Mapped[bool]=mapped_column(Boolean,default=True,nullable=False); category_id:Mapped[int|None]=mapped_column(ForeignKey('product_categories.id')); base_unit_id:Mapped[int|None]=mapped_column(ForeignKey('units.id')); buying_price:Mapped[Decimal]=mapped_column(Numeric(14,2),default=0); selling_price:Mapped[Decimal]=mapped_column(Numeric(14,2),default=0); current_stock:Mapped[Decimal]=mapped_column(Numeric(14,3),default=0); minimum_stock:Mapped[Decimal]=mapped_column(Numeric(14,3),default=0); reorder_level:Mapped[Decimal]=mapped_column(Numeric(14,3),default=0); tax_rate_id:Mapped[int|None]=mapped_column(ForeignKey('tax_rates.id')); is_active:Mapped[bool]=mapped_column(Boolean,default=True)
class ProductUnitConversion(Base):
    __tablename__='product_unit_conversions'; id:Mapped[int]=mapped_column(primary_key=True); product_id:Mapped[int]=mapped_column(ForeignKey('products.id')); from_unit_id:Mapped[int]=mapped_column(ForeignKey('units.id')); to_unit_id:Mapped[int]=mapped_column(ForeignKey('units.id')); factor:Mapped[Decimal]=mapped_column(Numeric(14,4))
class Sale(Base):
    __tablename__='sales'; id:Mapped[int]=mapped_column(primary_key=True); invoice_no:Mapped[str]=mapped_column(String(80),unique=True); customer_id:Mapped[int|None]=mapped_column(ForeignKey('customers.id')); container_type_id:Mapped[int|None]=mapped_column(ForeignKey('empty_container_types.id'),nullable=True); containers_issued:Mapped[Decimal]=mapped_column(Numeric(14,3),default=0); containers_returned:Mapped[Decimal]=mapped_column(Numeric(14,3),default=0); container_deposit:Mapped[Decimal]=mapped_column(Numeric(14,2),default=0); sale_date:Mapped[datetime]=mapped_column(DateTime,default=utcnow); subtotal:Mapped[Decimal]=mapped_column(Numeric(14,2),default=0); discount:Mapped[Decimal]=mapped_column(Numeric(14,2),default=0); tax_amount:Mapped[Decimal]=mapped_column(Numeric(14,2),default=0); total:Mapped[Decimal]=mapped_column(Numeric(14,2),default=0); paid:Mapped[Decimal]=mapped_column(Numeric(14,2),default=0); payment_method:Mapped[str]=mapped_column(String(30),default='cash'); bank_account_id:Mapped[int|None]=mapped_column(ForeignKey('bank_accounts.id')); branch_id:Mapped[int|None]=mapped_column(ForeignKey('branches.id')); warehouse_id:Mapped[int|None]=mapped_column(ForeignKey('warehouses.id')); price_list_id:Mapped[int|None]=mapped_column(ForeignKey('price_lists.id')); shift_id:Mapped[int|None]=mapped_column(ForeignKey('shifts.id')); tax_rate_id:Mapped[int|None]=mapped_column(ForeignKey('tax_rates.id')); status:Mapped[str]=mapped_column(String(30),default='posted')
class SaleItem(Base):
    __tablename__='sale_items'; id:Mapped[int]=mapped_column(primary_key=True); sale_id:Mapped[int]=mapped_column(ForeignKey('sales.id')); product_id:Mapped[int]=mapped_column(ForeignKey('products.id')); unit_id:Mapped[int|None]=mapped_column(ForeignKey('units.id')); quantity:Mapped[Decimal]=mapped_column(Numeric(14,3)); unit_price:Mapped[Decimal]=mapped_column(Numeric(14,2)); cost_price:Mapped[Decimal]=mapped_column(Numeric(14,2)); line_total:Mapped[Decimal]=mapped_column(Numeric(14,2)); batch_id:Mapped[int|None]=mapped_column(ForeignKey('product_batches.id')); serial_id:Mapped[int|None]=mapped_column(ForeignKey('product_serials.id'))
class Purchase(Base):
    __tablename__='purchases'; id:Mapped[int]=mapped_column(primary_key=True); invoice_no:Mapped[str]=mapped_column(String(80)); supplier_id:Mapped[int|None]=mapped_column(ForeignKey('suppliers.id')); purchase_date:Mapped[datetime]=mapped_column(DateTime,default=utcnow); total:Mapped[Decimal]=mapped_column(Numeric(14,2),default=0); paid:Mapped[Decimal]=mapped_column(Numeric(14,2),default=0); payment_method:Mapped[str]=mapped_column(String(30),default='cash'); bank_account_id:Mapped[int|None]=mapped_column(ForeignKey('bank_accounts.id')); branch_id:Mapped[int|None]=mapped_column(ForeignKey('branches.id')); warehouse_id:Mapped[int|None]=mapped_column(ForeignKey('warehouses.id')); status:Mapped[str]=mapped_column(String(30),default='posted')
class PurchaseItem(Base):
    __tablename__='purchase_items'; id:Mapped[int]=mapped_column(primary_key=True); purchase_id:Mapped[int]=mapped_column(ForeignKey('purchases.id')); product_id:Mapped[int]=mapped_column(ForeignKey('products.id')); unit_id:Mapped[int|None]=mapped_column(ForeignKey('units.id')); quantity:Mapped[Decimal]=mapped_column(Numeric(14,3)); unit_price:Mapped[Decimal]=mapped_column(Numeric(14,2)); line_total:Mapped[Decimal]=mapped_column(Numeric(14,2))
class StockMovement(Base):
    __tablename__='stock_movements'; id:Mapped[int]=mapped_column(primary_key=True); product_id:Mapped[int]=mapped_column(ForeignKey('products.id')); movement_type:Mapped[str]=mapped_column(String(40)); quantity:Mapped[Decimal]=mapped_column(Numeric(14,3)); reference_type:Mapped[str|None]=mapped_column(String(60)); reference_id:Mapped[int|None]=mapped_column(Integer); created_at:Mapped[datetime]=mapped_column(DateTime,default=utcnow)
class Expense(Base):
    __tablename__='expenses'; id:Mapped[int]=mapped_column(primary_key=True); category:Mapped[str]=mapped_column(String(100)); amount:Mapped[Decimal]=mapped_column(Numeric(14,2)); payment_method:Mapped[str]=mapped_column(String(30)); bank_account_id:Mapped[int|None]=mapped_column(ForeignKey('bank_accounts.id')); description:Mapped[str|None]=mapped_column(Text); expense_date:Mapped[date]=mapped_column(Date)
class CashTransaction(Base):
    __tablename__='cash_transactions'; id:Mapped[int]=mapped_column(primary_key=True); transaction_type:Mapped[str]=mapped_column(String(50)); amount:Mapped[Decimal]=mapped_column(Numeric(14,2)); reference:Mapped[str|None]=mapped_column(String(120)); description:Mapped[str|None]=mapped_column(Text); shift_id:Mapped[int|None]=mapped_column(ForeignKey('shifts.id')); created_at:Mapped[datetime]=mapped_column(DateTime,default=utcnow)
class BankAccount(Base):
    __tablename__='bank_accounts'; id:Mapped[int]=mapped_column(primary_key=True); bank_name:Mapped[str]=mapped_column(String(120)); account_name:Mapped[str]=mapped_column(String(180)); account_number:Mapped[str]=mapped_column(String(100),unique=True); opening_balance:Mapped[Decimal]=mapped_column(Numeric(14,2),default=0)
class BankTransaction(Base):
    __tablename__='bank_transactions'; id:Mapped[int]=mapped_column(primary_key=True); bank_account_id:Mapped[int]=mapped_column(ForeignKey('bank_accounts.id')); transaction_type:Mapped[str]=mapped_column(String(50)); amount:Mapped[Decimal]=mapped_column(Numeric(14,2)); reference:Mapped[str|None]=mapped_column(String(120)); description:Mapped[str|None]=mapped_column(Text); created_at:Mapped[datetime]=mapped_column(DateTime,default=utcnow)
class AuditLog(Base):
    __tablename__='audit_logs'; id:Mapped[int]=mapped_column(primary_key=True); user_id:Mapped[int|None]=mapped_column(ForeignKey('users.id')); action:Mapped[str]=mapped_column(String(100)); entity_type:Mapped[str]=mapped_column(String(100)); entity_id:Mapped[int|None]=mapped_column(Integer); before_json:Mapped[str|None]=mapped_column(Text); after_json:Mapped[str|None]=mapped_column(Text); created_at:Mapped[datetime]=mapped_column(DateTime,default=utcnow)

class CustomerPayment(Base):
    __tablename__='customer_payments'
    id: Mapped[int]=mapped_column(primary_key=True)
    customer_id: Mapped[int]=mapped_column(ForeignKey('customers.id'))
    sale_id: Mapped[int|None]=mapped_column(ForeignKey('sales.id'))
    amount: Mapped[Decimal]=mapped_column(Numeric(14,2))
    payment_method: Mapped[str]=mapped_column(String(30),default='cash')
    bank_account_id: Mapped[int|None]=mapped_column(ForeignKey('bank_accounts.id'))
    reference: Mapped[str|None]=mapped_column(String(120))
    notes: Mapped[str|None]=mapped_column(Text)
    payment_date: Mapped[datetime]=mapped_column(DateTime,default=utcnow)

class SupplierPayment(Base):
    __tablename__='supplier_payments'
    id: Mapped[int]=mapped_column(primary_key=True)
    supplier_id: Mapped[int]=mapped_column(ForeignKey('suppliers.id'))
    purchase_id: Mapped[int|None]=mapped_column(ForeignKey('purchases.id'))
    amount: Mapped[Decimal]=mapped_column(Numeric(14,2))
    payment_method: Mapped[str]=mapped_column(String(30),default='cash')
    bank_account_id: Mapped[int|None]=mapped_column(ForeignKey('bank_accounts.id'))
    reference: Mapped[str|None]=mapped_column(String(120))
    notes: Mapped[str|None]=mapped_column(Text)
    payment_date: Mapped[datetime]=mapped_column(DateTime,default=utcnow)

class SalesReturn(Base):
    __tablename__='sales_returns'
    id: Mapped[int]=mapped_column(primary_key=True)
    sale_id: Mapped[int]=mapped_column(ForeignKey('sales.id'))
    customer_id: Mapped[int|None]=mapped_column(ForeignKey('customers.id'))
    reason: Mapped[str]=mapped_column(String(255))
    total: Mapped[Decimal]=mapped_column(Numeric(14,2),default=0)
    refund_method: Mapped[str]=mapped_column(String(30),default='credit')
    bank_account_id: Mapped[int|None]=mapped_column(ForeignKey('bank_accounts.id'))
    status: Mapped[str]=mapped_column(String(30),default='posted')
    return_date: Mapped[datetime]=mapped_column(DateTime,default=utcnow)

class SalesReturnItem(Base):
    __tablename__='sales_return_items'
    id: Mapped[int]=mapped_column(primary_key=True)
    sales_return_id: Mapped[int]=mapped_column(ForeignKey('sales_returns.id'))
    product_id: Mapped[int]=mapped_column(ForeignKey('products.id'))
    unit_id: Mapped[int|None]=mapped_column(ForeignKey('units.id'))
    requested_quantity: Mapped[Decimal]=mapped_column(Numeric(14,3),default=0,nullable=False)
    quantity: Mapped[Decimal]=mapped_column(Numeric(14,3))
    unit_price: Mapped[Decimal]=mapped_column(Numeric(14,2))
    cost_price: Mapped[Decimal]=mapped_column(Numeric(14,2),default=0,nullable=False)
    line_total: Mapped[Decimal]=mapped_column(Numeric(14,2))

class PurchaseReturn(Base):
    __tablename__='purchase_returns'
    id: Mapped[int]=mapped_column(primary_key=True)
    purchase_id: Mapped[int]=mapped_column(ForeignKey('purchases.id'))
    supplier_id: Mapped[int|None]=mapped_column(ForeignKey('suppliers.id'))
    reason: Mapped[str]=mapped_column(String(255))
    total: Mapped[Decimal]=mapped_column(Numeric(14,2),default=0)
    settlement_method: Mapped[str]=mapped_column(String(30),default='credit')
    bank_account_id: Mapped[int|None]=mapped_column(ForeignKey('bank_accounts.id'))
    status: Mapped[str]=mapped_column(String(30),default='posted')
    return_date: Mapped[datetime]=mapped_column(DateTime,default=utcnow)

class PurchaseReturnItem(Base):
    __tablename__='purchase_return_items'
    id: Mapped[int]=mapped_column(primary_key=True)
    purchase_return_id: Mapped[int]=mapped_column(ForeignKey('purchase_returns.id'))
    product_id: Mapped[int]=mapped_column(ForeignKey('products.id'))
    unit_id: Mapped[int|None]=mapped_column(ForeignKey('units.id'))
    requested_quantity: Mapped[Decimal]=mapped_column(Numeric(14,3),default=0,nullable=False)
    quantity: Mapped[Decimal]=mapped_column(Numeric(14,3))
    unit_price: Mapped[Decimal]=mapped_column(Numeric(14,2))
    cost_price: Mapped[Decimal]=mapped_column(Numeric(14,2),default=0,nullable=False)
    line_total: Mapped[Decimal]=mapped_column(Numeric(14,2))



class EmptyContainerType(Base):
    __tablename__='empty_container_types'
    id: Mapped[int]=mapped_column(primary_key=True)
    name: Mapped[str]=mapped_column(String(100),unique=True)
    symbol: Mapped[str]=mapped_column(String(20),unique=True)
    deposit_value: Mapped[Decimal]=mapped_column(Numeric(14,2),default=0)
    current_stock: Mapped[Decimal]=mapped_column(Numeric(14,3),default=0)
    is_active: Mapped[bool]=mapped_column(Boolean,default=True)

class EmptyContainerMovement(Base):
    __tablename__='empty_container_movements'
    id: Mapped[int]=mapped_column(primary_key=True)
    container_type_id: Mapped[int]=mapped_column(ForeignKey('empty_container_types.id'))
    movement_type: Mapped[str]=mapped_column(String(40))
    quantity: Mapped[Decimal]=mapped_column(Numeric(14,3))
    reference_type: Mapped[str|None]=mapped_column(String(60))
    reference_id: Mapped[int|None]=mapped_column(Integer)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow)

class Account(Base):
    __tablename__='accounts'
    id: Mapped[int]=mapped_column(primary_key=True)
    code: Mapped[str]=mapped_column(String(30),unique=True)
    name: Mapped[str]=mapped_column(String(150))
    account_type: Mapped[str]=mapped_column(String(30))
    is_active: Mapped[bool]=mapped_column(Boolean,default=True)

class JournalEntry(Base):
    __tablename__='journal_entries'
    id: Mapped[int]=mapped_column(primary_key=True)
    entry_date: Mapped[datetime]=mapped_column(DateTime,default=utcnow)
    reference_type: Mapped[str|None]=mapped_column(String(60))
    reference_id: Mapped[int|None]=mapped_column(Integer)
    description: Mapped[str|None]=mapped_column(Text)
    status: Mapped[str]=mapped_column(String(30),default='posted')

class JournalLine(Base):
    __tablename__='journal_lines'
    id: Mapped[int]=mapped_column(primary_key=True)
    journal_entry_id: Mapped[int]=mapped_column(ForeignKey('journal_entries.id'))
    account_id: Mapped[int]=mapped_column(ForeignKey('accounts.id'))
    debit: Mapped[Decimal]=mapped_column(Numeric(14,2),default=0)
    credit: Mapped[Decimal]=mapped_column(Numeric(14,2),default=0)
    description: Mapped[str|None]=mapped_column(Text)

class SalesQuote(Base):
    __tablename__='sales_quotes'
    id: Mapped[int]=mapped_column(primary_key=True)
    quote_no: Mapped[str]=mapped_column(String(80),unique=True)
    customer_id: Mapped[int|None]=mapped_column(ForeignKey('customers.id'))
    issue_date: Mapped[datetime]=mapped_column(DateTime,default=utcnow)
    valid_until: Mapped[date|None]=mapped_column(Date)
    subtotal: Mapped[Decimal]=mapped_column(Numeric(14,2),default=0)
    discount: Mapped[Decimal]=mapped_column(Numeric(14,2),default=0)
    total: Mapped[Decimal]=mapped_column(Numeric(14,2),default=0)
    status: Mapped[str]=mapped_column(String(30),default='draft')
    notes: Mapped[str|None]=mapped_column(Text)
    created_by: Mapped[int|None]=mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow)
    updated_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,onupdate=utcnow)

class SalesQuoteItem(Base):
    __tablename__='sales_quote_items'
    id: Mapped[int]=mapped_column(primary_key=True)
    quote_id: Mapped[int]=mapped_column(ForeignKey('sales_quotes.id',ondelete='CASCADE'))
    product_id: Mapped[int]=mapped_column(ForeignKey('products.id'))
    unit_id: Mapped[int|None]=mapped_column(ForeignKey('units.id'))
    quantity: Mapped[Decimal]=mapped_column(Numeric(14,3))
    unit_price: Mapped[Decimal]=mapped_column(Numeric(14,2))
    line_total: Mapped[Decimal]=mapped_column(Numeric(14,2))

class SalesOrder(Base):
    __tablename__='sales_orders'
    id: Mapped[int]=mapped_column(primary_key=True)
    order_no: Mapped[str]=mapped_column(String(80),unique=True)
    quote_id: Mapped[int|None]=mapped_column(ForeignKey('sales_quotes.id'))
    customer_id: Mapped[int|None]=mapped_column(ForeignKey('customers.id'))
    order_date: Mapped[datetime]=mapped_column(DateTime,default=utcnow)
    subtotal: Mapped[Decimal]=mapped_column(Numeric(14,2),default=0)
    discount: Mapped[Decimal]=mapped_column(Numeric(14,2),default=0)
    total: Mapped[Decimal]=mapped_column(Numeric(14,2),default=0)
    status: Mapped[str]=mapped_column(String(30),default='draft')
    notes: Mapped[str|None]=mapped_column(Text)
    created_by: Mapped[int|None]=mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow)
    updated_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,onupdate=utcnow)

class SalesOrderItem(Base):
    __tablename__='sales_order_items'
    id: Mapped[int]=mapped_column(primary_key=True)
    order_id: Mapped[int]=mapped_column(ForeignKey('sales_orders.id',ondelete='CASCADE'))
    product_id: Mapped[int]=mapped_column(ForeignKey('products.id'))
    unit_id: Mapped[int|None]=mapped_column(ForeignKey('units.id'))
    quantity: Mapped[Decimal]=mapped_column(Numeric(14,3))
    unit_price: Mapped[Decimal]=mapped_column(Numeric(14,2))
    line_total: Mapped[Decimal]=mapped_column(Numeric(14,2))


class PurchaseOrder(Base):
    __tablename__='purchase_orders'
    id: Mapped[int]=mapped_column(primary_key=True)
    po_no: Mapped[str]=mapped_column(String(80),unique=True,nullable=False)
    supplier_id: Mapped[int|None]=mapped_column(ForeignKey('suppliers.id'))
    branch_id: Mapped[int|None]=mapped_column(ForeignKey('branches.id'))
    warehouse_id: Mapped[int|None]=mapped_column(ForeignKey('warehouses.id'))
    order_date: Mapped[datetime]=mapped_column(DateTime,default=utcnow,nullable=False)
    expected_date: Mapped[date|None]=mapped_column(Date)
    subtotal: Mapped[Decimal]=mapped_column(Numeric(14,2),default=0,nullable=False)
    discount: Mapped[Decimal]=mapped_column(Numeric(14,2),default=0,nullable=False)
    total: Mapped[Decimal]=mapped_column(Numeric(14,2),default=0,nullable=False)
    status: Mapped[str]=mapped_column(String(30),default='draft',nullable=False)
    notes: Mapped[str|None]=mapped_column(Text)
    created_by: Mapped[int|None]=mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,nullable=False)
    updated_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,onupdate=utcnow,nullable=False)

class PurchaseOrderItem(Base):
    __tablename__='purchase_order_items'
    id: Mapped[int]=mapped_column(primary_key=True)
    purchase_order_id: Mapped[int]=mapped_column(ForeignKey('purchase_orders.id',ondelete='CASCADE'),nullable=False)
    product_id: Mapped[int]=mapped_column(ForeignKey('products.id'),nullable=False)
    unit_id: Mapped[int|None]=mapped_column(ForeignKey('units.id'))
    quantity: Mapped[Decimal]=mapped_column(Numeric(14,3),nullable=False)
    unit_price: Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)
    line_total: Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)
    batch_no: Mapped[str|None]=mapped_column(String(120))
    expiry_date: Mapped[date|None]=mapped_column(Date)

# --- Platform v1.4: multi-location, pricing, shifts, industry and cloud-ready foundations ---
class Branch(Base):
    __tablename__='branches'
    id: Mapped[int]=mapped_column(primary_key=True)
    code: Mapped[str]=mapped_column(String(40),unique=True)
    name: Mapped[str]=mapped_column(String(150),nullable=False)
    phone: Mapped[str|None]=mapped_column(String(50))
    address: Mapped[str|None]=mapped_column(String(255))
    is_default: Mapped[bool]=mapped_column(Boolean,default=False,nullable=False)
    is_active: Mapped[bool]=mapped_column(Boolean,default=True,nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,nullable=False)

class Warehouse(Base):
    __tablename__='warehouses'
    id: Mapped[int]=mapped_column(primary_key=True)
    branch_id: Mapped[int|None]=mapped_column(ForeignKey('branches.id'))
    code: Mapped[str]=mapped_column(String(40),unique=True)
    name: Mapped[str]=mapped_column(String(150),nullable=False)
    is_default: Mapped[bool]=mapped_column(Boolean,default=False,nullable=False)
    is_active: Mapped[bool]=mapped_column(Boolean,default=True,nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,nullable=False)

class StockBalance(Base):
    __tablename__='stock_balances'
    id: Mapped[int]=mapped_column(primary_key=True)
    warehouse_id: Mapped[int]=mapped_column(ForeignKey('warehouses.id'),nullable=False)
    product_id: Mapped[int]=mapped_column(ForeignKey('products.id'),nullable=False)
    quantity: Mapped[Decimal]=mapped_column(Numeric(14,3),default=0,nullable=False)
    reserved_quantity: Mapped[Decimal]=mapped_column(Numeric(14,3),default=0,nullable=False)
    reorder_level: Mapped[Decimal]=mapped_column(Numeric(14,3),default=0,nullable=False)
    updated_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,onupdate=utcnow,nullable=False)

class StockTransfer(Base):
    __tablename__='stock_transfers'
    id: Mapped[int]=mapped_column(primary_key=True)
    transfer_no: Mapped[str]=mapped_column(String(80),unique=True)
    from_warehouse_id: Mapped[int]=mapped_column(ForeignKey('warehouses.id'),nullable=False)
    to_warehouse_id: Mapped[int]=mapped_column(ForeignKey('warehouses.id'),nullable=False)
    status: Mapped[str]=mapped_column(String(30),default='draft',nullable=False)
    requested_by: Mapped[int|None]=mapped_column(ForeignKey('users.id'))
    approved_by: Mapped[int|None]=mapped_column(ForeignKey('users.id'))
    requested_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,nullable=False)
    received_at: Mapped[datetime|None]=mapped_column(DateTime)
    notes: Mapped[str|None]=mapped_column(Text)

class StockTransferItem(Base):
    __tablename__='stock_transfer_items'
    id: Mapped[int]=mapped_column(primary_key=True)
    transfer_id: Mapped[int]=mapped_column(ForeignKey('stock_transfers.id',ondelete='CASCADE'),nullable=False)
    product_id: Mapped[int]=mapped_column(ForeignKey('products.id'),nullable=False)
    quantity: Mapped[Decimal]=mapped_column(Numeric(14,3),nullable=False)

class PriceList(Base):
    __tablename__='price_lists'
    id: Mapped[int]=mapped_column(primary_key=True)
    name: Mapped[str]=mapped_column(String(120),nullable=False)
    code: Mapped[str]=mapped_column(String(40),unique=True)
    currency: Mapped[str]=mapped_column(String(10),default='TZS',nullable=False)
    is_default: Mapped[bool]=mapped_column(Boolean,default=False,nullable=False)
    is_active: Mapped[bool]=mapped_column(Boolean,default=True,nullable=False)

class PriceListItem(Base):
    __tablename__='price_list_items'
    id: Mapped[int]=mapped_column(primary_key=True)
    price_list_id: Mapped[int]=mapped_column(ForeignKey('price_lists.id',ondelete='CASCADE'),nullable=False)
    product_id: Mapped[int]=mapped_column(ForeignKey('products.id'),nullable=False)
    unit_id: Mapped[int|None]=mapped_column(ForeignKey('units.id'))
    price: Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)

class PaymentMethod(Base):
    __tablename__='payment_methods'
    id: Mapped[int]=mapped_column(primary_key=True)
    code: Mapped[str]=mapped_column(String(40),unique=True)
    name: Mapped[str]=mapped_column(String(100),nullable=False)
    method_type: Mapped[str]=mapped_column(String(30),default='other',nullable=False)
    is_active: Mapped[bool]=mapped_column(Boolean,default=True,nullable=False)
    sort_order: Mapped[int]=mapped_column(Integer,default=0,nullable=False)

class Shift(Base):
    __tablename__='shifts'
    id: Mapped[int]=mapped_column(primary_key=True)
    branch_id: Mapped[int|None]=mapped_column(ForeignKey('branches.id'))
    user_id: Mapped[int]=mapped_column(ForeignKey('users.id'),nullable=False)
    opened_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,nullable=False)
    closed_at: Mapped[datetime|None]=mapped_column(DateTime)
    opening_cash: Mapped[Decimal]=mapped_column(Numeric(14,2),default=0,nullable=False)
    expected_cash: Mapped[Decimal]=mapped_column(Numeric(14,2),default=0,nullable=False)
    counted_cash: Mapped[Decimal|None]=mapped_column(Numeric(14,2))
    difference: Mapped[Decimal|None]=mapped_column(Numeric(14,2))
    status: Mapped[str]=mapped_column(String(20),default='open',nullable=False)
    notes: Mapped[str|None]=mapped_column(Text)

class ProductBatch(Base):
    __tablename__='product_batches'
    id: Mapped[int]=mapped_column(primary_key=True)
    product_id: Mapped[int]=mapped_column(ForeignKey('products.id'),nullable=False)
    batch_no: Mapped[str]=mapped_column(String(100),nullable=False)
    expiry_date: Mapped[date|None]=mapped_column(Date)
    quantity: Mapped[Decimal]=mapped_column(Numeric(14,3),default=0,nullable=False)
    cost_price: Mapped[Decimal]=mapped_column(Numeric(14,2),default=0,nullable=False)
    is_active: Mapped[bool]=mapped_column(Boolean,default=True,nullable=False)

class ProductSerial(Base):
    __tablename__='product_serials'
    id: Mapped[int]=mapped_column(primary_key=True)
    product_id: Mapped[int]=mapped_column(ForeignKey('products.id'),nullable=False)
    serial_no: Mapped[str]=mapped_column(String(120),unique=True)
    status: Mapped[str]=mapped_column(String(30),default='in_stock',nullable=False)
    sold_sale_id: Mapped[int|None]=mapped_column(ForeignKey('sales.id'))
    warranty_until: Mapped[date|None]=mapped_column(Date)

class Recipe(Base):
    __tablename__='recipes'
    id: Mapped[int]=mapped_column(primary_key=True)
    name: Mapped[str]=mapped_column(String(150),nullable=False)
    output_product_id: Mapped[int]=mapped_column(ForeignKey('products.id'),nullable=False)
    output_quantity: Mapped[Decimal]=mapped_column(Numeric(14,3),default=1,nullable=False)
    is_active: Mapped[bool]=mapped_column(Boolean,default=True,nullable=False)

class RecipeItem(Base):
    __tablename__='recipe_items'
    id: Mapped[int]=mapped_column(primary_key=True)
    recipe_id: Mapped[int]=mapped_column(ForeignKey('recipes.id',ondelete='CASCADE'),nullable=False)
    product_id: Mapped[int]=mapped_column(ForeignKey('products.id'),nullable=False)
    quantity: Mapped[Decimal]=mapped_column(Numeric(14,3),nullable=False)

class ProductionOrder(Base):
    __tablename__='production_orders'
    id: Mapped[int]=mapped_column(primary_key=True)
    order_no: Mapped[str]=mapped_column(String(80),unique=True)
    recipe_id: Mapped[int]=mapped_column(ForeignKey('recipes.id'),nullable=False)
    quantity: Mapped[Decimal]=mapped_column(Numeric(14,3),nullable=False)
    status: Mapped[str]=mapped_column(String(30),default='draft',nullable=False)
    created_by: Mapped[int|None]=mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,nullable=False)
    completed_at: Mapped[datetime|None]=mapped_column(DateTime)

class OnlineOrder(Base):
    __tablename__='online_orders'
    id: Mapped[int]=mapped_column(primary_key=True)
    order_no: Mapped[str]=mapped_column(String(80),unique=True)
    customer_id: Mapped[int|None]=mapped_column(ForeignKey('customers.id'))
    channel: Mapped[str]=mapped_column(String(40),default='web',nullable=False)
    status: Mapped[str]=mapped_column(String(30),default='pending',nullable=False)
    total: Mapped[Decimal]=mapped_column(Numeric(14,2),default=0,nullable=False)
    payload_json: Mapped[str]=mapped_column(Text,default='{}',nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,nullable=False)
    updated_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,onupdate=utcnow,nullable=False)

class OnlineOrderItem(Base):
    __tablename__='online_order_items'
    id: Mapped[int]=mapped_column(primary_key=True)
    order_id: Mapped[int]=mapped_column(ForeignKey('online_orders.id',ondelete='CASCADE'),nullable=False)
    product_id: Mapped[int]=mapped_column(ForeignKey('products.id'),nullable=False)
    quantity: Mapped[Decimal]=mapped_column(Numeric(14,3),nullable=False)
    unit_price: Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)
    line_total: Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)

class Device(Base):
    __tablename__='devices'
    id: Mapped[int]=mapped_column(primary_key=True)
    device_code: Mapped[str]=mapped_column(String(80),unique=True)
    name: Mapped[str]=mapped_column(String(150),nullable=False)
    device_type: Mapped[str]=mapped_column(String(40),default='desktop',nullable=False)
    last_seen_at: Mapped[datetime|None]=mapped_column(DateTime)
    is_active: Mapped[bool]=mapped_column(Boolean,default=True,nullable=False)

class SyncOutbox(Base):
    __tablename__='sync_outbox'
    id: Mapped[int]=mapped_column(primary_key=True)
    event_id: Mapped[str]=mapped_column(String(80),unique=True)
    entity_type: Mapped[str]=mapped_column(String(80),nullable=False)
    entity_id: Mapped[int|None]=mapped_column(Integer)
    action: Mapped[str]=mapped_column(String(40),nullable=False)
    payload_json: Mapped[str]=mapped_column(Text,nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,nullable=False)
    synced_at: Mapped[datetime|None]=mapped_column(DateTime)
    attempts: Mapped[int]=mapped_column(Integer,default=0,nullable=False)

class IntegrationConnection(Base):
    __tablename__='integration_connections'
    id: Mapped[int]=mapped_column(primary_key=True)
    provider: Mapped[str]=mapped_column(String(80),nullable=False)
    name: Mapped[str]=mapped_column(String(120),nullable=False)
    status: Mapped[str]=mapped_column(String(30),default='disabled',nullable=False)
    settings_json: Mapped[str]=mapped_column(Text,default='{}',nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,nullable=False)

class Notification(Base):
    __tablename__='notifications'
    id: Mapped[int]=mapped_column(primary_key=True)
    user_id: Mapped[int|None]=mapped_column(ForeignKey('users.id'))
    kind: Mapped[str]=mapped_column(String(50),nullable=False)
    title: Mapped[str]=mapped_column(String(180),nullable=False)
    message: Mapped[str]=mapped_column(Text,nullable=False)
    is_read: Mapped[bool]=mapped_column(Boolean,default=False,nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,nullable=False)


class CustomerGroup(Base):
    __tablename__='customer_groups'
    id: Mapped[int]=mapped_column(primary_key=True)
    name: Mapped[str]=mapped_column(String(120),unique=True,nullable=False)
    discount_percent: Mapped[Decimal]=mapped_column(Numeric(8,2),default=0,nullable=False)
    default_price_list_id: Mapped[int|None]=mapped_column(ForeignKey('price_lists.id'))
    is_active: Mapped[bool]=mapped_column(Boolean,default=True,nullable=False)

class TaxRate(Base):
    __tablename__='tax_rates'
    id: Mapped[int]=mapped_column(primary_key=True)
    name: Mapped[str]=mapped_column(String(80),nullable=False)
    rate: Mapped[Decimal]=mapped_column(Numeric(8,4),default=0,nullable=False)
    inclusive: Mapped[bool]=mapped_column(Boolean,default=False,nullable=False)
    is_active: Mapped[bool]=mapped_column(Boolean,default=True,nullable=False)

class SalePayment(Base):
    __tablename__='sale_payments'
    id: Mapped[int]=mapped_column(primary_key=True)
    sale_id: Mapped[int]=mapped_column(ForeignKey('sales.id',ondelete='CASCADE'),nullable=False)
    payment_method: Mapped[str]=mapped_column(String(30),nullable=False)
    amount: Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)
    bank_account_id: Mapped[int|None]=mapped_column(ForeignKey('bank_accounts.id'))
    reference: Mapped[str|None]=mapped_column(String(120))
    created_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,nullable=False)

class PaymentTransaction(Base):
    __tablename__='payment_transactions'
    id: Mapped[int]=mapped_column(primary_key=True)
    direction: Mapped[str]=mapped_column(String(20),nullable=False)
    payment_method: Mapped[str]=mapped_column(String(30),nullable=False)
    amount: Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)
    reference: Mapped[str|None]=mapped_column(String(120))
    external_reference: Mapped[str|None]=mapped_column(String(160))
    bank_account_id: Mapped[int|None]=mapped_column(ForeignKey('bank_accounts.id'))
    reconciled_at: Mapped[datetime|None]=mapped_column(DateTime)
    reconciled_by: Mapped[int|None]=mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,nullable=False)

class PaymentAllocation(Base):
    __tablename__='payment_allocations'
    id: Mapped[int]=mapped_column(primary_key=True)
    customer_payment_id: Mapped[int]=mapped_column(ForeignKey('customer_payments.id',ondelete='CASCADE'),nullable=False)
    sale_id: Mapped[int]=mapped_column(ForeignKey('sales.id'),nullable=False)
    amount: Mapped[Decimal]=mapped_column(Numeric(14,2),nullable=False)

class DeliveryOrder(Base):
    __tablename__='delivery_orders'
    id: Mapped[int]=mapped_column(primary_key=True)
    delivery_no: Mapped[str]=mapped_column(String(80),unique=True,nullable=False)
    sale_id: Mapped[int|None]=mapped_column(ForeignKey('sales.id'))
    sales_order_id: Mapped[int|None]=mapped_column(ForeignKey('sales_orders.id'))
    customer_id: Mapped[int|None]=mapped_column(ForeignKey('customers.id'))
    driver_name: Mapped[str|None]=mapped_column(String(150))
    vehicle_no: Mapped[str|None]=mapped_column(String(80))
    address: Mapped[str|None]=mapped_column(String(255))
    status: Mapped[str]=mapped_column(String(30),default='pending',nullable=False)
    scheduled_at: Mapped[datetime|None]=mapped_column(DateTime)
    delivered_at: Mapped[datetime|None]=mapped_column(DateTime)
    notes: Mapped[str|None]=mapped_column(Text)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,nullable=False)

class DeliveryItem(Base):
    __tablename__='delivery_items'
    id: Mapped[int]=mapped_column(primary_key=True)
    delivery_id: Mapped[int]=mapped_column(ForeignKey('delivery_orders.id',ondelete='CASCADE'),nullable=False)
    product_id: Mapped[int]=mapped_column(ForeignKey('products.id'),nullable=False)
    quantity: Mapped[Decimal]=mapped_column(Numeric(14,3),nullable=False)

class ServiceOrder(Base):
    __tablename__='service_orders'
    id: Mapped[int]=mapped_column(primary_key=True)
    job_no: Mapped[str]=mapped_column(String(80),unique=True,nullable=False)
    customer_id: Mapped[int|None]=mapped_column(ForeignKey('customers.id'))
    sale_id: Mapped[int|None]=mapped_column(ForeignKey('sales.id'))
    title: Mapped[str]=mapped_column(String(180),nullable=False)
    description: Mapped[str|None]=mapped_column(Text)
    assigned_user_id: Mapped[int|None]=mapped_column(ForeignKey('users.id'))
    status: Mapped[str]=mapped_column(String(30),default='open',nullable=False)
    quoted_amount: Mapped[Decimal]=mapped_column(Numeric(14,2),default=0,nullable=False)
    actual_amount: Mapped[Decimal]=mapped_column(Numeric(14,2),default=0,nullable=False)
    scheduled_date: Mapped[date|None]=mapped_column(Date)
    completed_at: Mapped[datetime|None]=mapped_column(DateTime)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,nullable=False)

class LoyaltyAccount(Base):
    __tablename__='loyalty_accounts'
    id: Mapped[int]=mapped_column(primary_key=True)
    customer_id: Mapped[int]=mapped_column(ForeignKey('customers.id',ondelete='CASCADE'),unique=True,nullable=False)
    points_balance: Mapped[int]=mapped_column(Integer,default=0,nullable=False)
    lifetime_points: Mapped[int]=mapped_column(Integer,default=0,nullable=False)

class LoyaltyTransaction(Base):
    __tablename__='loyalty_transactions'
    id: Mapped[int]=mapped_column(primary_key=True)
    account_id: Mapped[int]=mapped_column(ForeignKey('loyalty_accounts.id',ondelete='CASCADE'),nullable=False)
    points: Mapped[int]=mapped_column(Integer,nullable=False)
    reason: Mapped[str]=mapped_column(String(180),nullable=False)
    reference: Mapped[str|None]=mapped_column(String(120))
    created_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,nullable=False)

class WarehouseBin(Base):
    __tablename__='warehouse_bins'
    id: Mapped[int]=mapped_column(primary_key=True)
    warehouse_id: Mapped[int]=mapped_column(ForeignKey('warehouses.id',ondelete='CASCADE'),nullable=False)
    code: Mapped[str]=mapped_column(String(60),nullable=False)
    name: Mapped[str]=mapped_column(String(120),nullable=False)
    is_active: Mapped[bool]=mapped_column(Boolean,default=True,nullable=False)

class StockAdjustment(Base):
    __tablename__='stock_adjustments'
    id: Mapped[int]=mapped_column(primary_key=True)
    adjustment_no: Mapped[str]=mapped_column(String(80),unique=True,nullable=False)
    warehouse_id: Mapped[int]=mapped_column(ForeignKey('warehouses.id'),nullable=False)
    reason: Mapped[str]=mapped_column(String(255),nullable=False)
    status: Mapped[str]=mapped_column(String(30),default='posted',nullable=False)
    created_by: Mapped[int|None]=mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,nullable=False)

class StockAdjustmentItem(Base):
    __tablename__='stock_adjustment_items'
    id: Mapped[int]=mapped_column(primary_key=True)
    adjustment_id: Mapped[int]=mapped_column(ForeignKey('stock_adjustments.id',ondelete='CASCADE'),nullable=False)
    product_id: Mapped[int]=mapped_column(ForeignKey('products.id'),nullable=False)
    quantity_delta: Mapped[Decimal]=mapped_column(Numeric(14,3),nullable=False)

class ApprovalRequest(Base):
    __tablename__='approval_requests'
    id: Mapped[int]=mapped_column(primary_key=True)
    request_type: Mapped[str]=mapped_column(String(60),nullable=False)
    entity_type: Mapped[str]=mapped_column(String(60),nullable=False)
    entity_id: Mapped[int]=mapped_column(Integer,nullable=False)
    requested_by: Mapped[int|None]=mapped_column(ForeignKey('users.id'))
    approved_by: Mapped[int|None]=mapped_column(ForeignKey('users.id'))
    status: Mapped[str]=mapped_column(String(30),default='pending',nullable=False)
    reason: Mapped[str|None]=mapped_column(Text)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,nullable=False)
    decided_at: Mapped[datetime|None]=mapped_column(DateTime)


# Internal team communication and task tracking. Participants determine visibility;
# branch_id is operational context (the legacy User model has no branch assignment).
class Conversation(Base):
    __tablename__='conversations'
    id: Mapped[int]=mapped_column(primary_key=True)
    subject: Mapped[str|None]=mapped_column(String(180))
    kind: Mapped[str]=mapped_column(String(20),default='direct',nullable=False)
    branch_id: Mapped[int|None]=mapped_column(ForeignKey('branches.id'))
    created_by: Mapped[int]=mapped_column(ForeignKey('users.id'),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,nullable=False)
    updated_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,onupdate=utcnow,nullable=False)

class ConversationParticipant(Base):
    __tablename__='conversation_participants'
    __table_args__=(UniqueConstraint('conversation_id','user_id',name='uq_conversation_participant'),)
    id: Mapped[int]=mapped_column(primary_key=True)
    conversation_id: Mapped[int]=mapped_column(ForeignKey('conversations.id',ondelete='CASCADE'),nullable=False)
    user_id: Mapped[int]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),nullable=False)
    joined_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,nullable=False)
    last_read_at: Mapped[datetime|None]=mapped_column(DateTime)

class TeamMessage(Base):
    __tablename__='team_messages'
    id: Mapped[int]=mapped_column(primary_key=True)
    conversation_id: Mapped[int]=mapped_column(ForeignKey('conversations.id',ondelete='CASCADE'),nullable=False)
    sender_id: Mapped[int]=mapped_column(ForeignKey('users.id'),nullable=False)
    body: Mapped[str]=mapped_column(Text,nullable=False)
    linked_entity_type: Mapped[str|None]=mapped_column(String(60))
    linked_entity_id: Mapped[int|None]=mapped_column(Integer)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,nullable=False)

class TeamTask(Base):
    __tablename__='team_tasks'
    id: Mapped[int]=mapped_column(primary_key=True)
    title: Mapped[str]=mapped_column(String(180),nullable=False)
    description: Mapped[str|None]=mapped_column(Text)
    created_by: Mapped[int]=mapped_column(ForeignKey('users.id'),nullable=False)
    assigned_to: Mapped[int]=mapped_column(ForeignKey('users.id'),nullable=False)
    branch_id: Mapped[int|None]=mapped_column(ForeignKey('branches.id'))
    conversation_id: Mapped[int|None]=mapped_column(ForeignKey('conversations.id',ondelete='SET NULL'))
    priority: Mapped[str]=mapped_column(String(20),default='normal',nullable=False)
    status: Mapped[str]=mapped_column(String(30),default='open',nullable=False)
    due_at: Mapped[datetime|None]=mapped_column(DateTime)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,nullable=False)
    updated_at: Mapped[datetime]=mapped_column(DateTime,default=utcnow,onupdate=utcnow,nullable=False)
    completed_at: Mapped[datetime|None]=mapped_column(DateTime)
