"""Add multi-location, pricing, shifts, industry and cloud-ready platform features with PostgreSQL-safe seed typing."""
from alembic import op
import sqlalchemy as sa

revision = "0013_operations_industry_cloud"
down_revision = "0012_sales_pipeline"
branch_labels = None
depends_on = None

def upgrade():
    bind=op.get_bind(); tables=set(sa.inspect(bind).get_table_names())
    if "branches" not in tables:
        op.create_table("branches", sa.Column("id",sa.Integer,primary_key=True), sa.Column("code",sa.String(40),unique=True,nullable=False), sa.Column("name",sa.String(150),nullable=False), sa.Column("phone",sa.String(50)), sa.Column("address",sa.String(255)), sa.Column("is_default",sa.Boolean,nullable=False,server_default=sa.false()), sa.Column("is_active",sa.Boolean,nullable=False,server_default=sa.true()), sa.Column("created_at",sa.DateTime,nullable=False))
    if "warehouses" not in tables:
        op.create_table("warehouses", sa.Column("id",sa.Integer,primary_key=True), sa.Column("branch_id",sa.Integer,sa.ForeignKey("branches.id")), sa.Column("code",sa.String(40),unique=True,nullable=False), sa.Column("name",sa.String(150),nullable=False), sa.Column("is_default",sa.Boolean,nullable=False,server_default=sa.false()), sa.Column("is_active",sa.Boolean,nullable=False,server_default=sa.true()), sa.Column("created_at",sa.DateTime,nullable=False))
    def create(name,*cols):
        if name not in tables: op.create_table(name,*cols)
    create("stock_balances",sa.Column("id",sa.Integer,primary_key=True),sa.Column("warehouse_id",sa.Integer,sa.ForeignKey("warehouses.id"),nullable=False),sa.Column("product_id",sa.Integer,sa.ForeignKey("products.id"),nullable=False),sa.Column("quantity",sa.Numeric(14,3),nullable=False,server_default="0"),sa.Column("reserved_quantity",sa.Numeric(14,3),nullable=False,server_default="0"),sa.Column("reorder_level",sa.Numeric(14,3),nullable=False,server_default="0"),sa.Column("updated_at",sa.DateTime,nullable=False))
    create("stock_transfers",sa.Column("id",sa.Integer,primary_key=True),sa.Column("transfer_no",sa.String(80),unique=True,nullable=False),sa.Column("from_warehouse_id",sa.Integer,sa.ForeignKey("warehouses.id"),nullable=False),sa.Column("to_warehouse_id",sa.Integer,sa.ForeignKey("warehouses.id"),nullable=False),sa.Column("status",sa.String(30),nullable=False,server_default="draft"),sa.Column("requested_by",sa.Integer,sa.ForeignKey("users.id")),sa.Column("approved_by",sa.Integer,sa.ForeignKey("users.id")),sa.Column("requested_at",sa.DateTime,nullable=False),sa.Column("received_at",sa.DateTime),sa.Column("notes",sa.Text))
    create("stock_transfer_items",sa.Column("id",sa.Integer,primary_key=True),sa.Column("transfer_id",sa.Integer,sa.ForeignKey("stock_transfers.id",ondelete="CASCADE"),nullable=False),sa.Column("product_id",sa.Integer,sa.ForeignKey("products.id"),nullable=False),sa.Column("quantity",sa.Numeric(14,3),nullable=False))
    create("price_lists",sa.Column("id",sa.Integer,primary_key=True),sa.Column("name",sa.String(120),nullable=False),sa.Column("code",sa.String(40),unique=True,nullable=False),sa.Column("currency",sa.String(10),nullable=False,server_default="TZS"),sa.Column("is_default",sa.Boolean,nullable=False,server_default=sa.false()),sa.Column("is_active",sa.Boolean,nullable=False,server_default=sa.true()))
    create("price_list_items",sa.Column("id",sa.Integer,primary_key=True),sa.Column("price_list_id",sa.Integer,sa.ForeignKey("price_lists.id",ondelete="CASCADE"),nullable=False),sa.Column("product_id",sa.Integer,sa.ForeignKey("products.id"),nullable=False),sa.Column("unit_id",sa.Integer,sa.ForeignKey("units.id")),sa.Column("price",sa.Numeric(14,2),nullable=False))
    create("payment_methods",sa.Column("id",sa.Integer,primary_key=True),sa.Column("code",sa.String(40),unique=True,nullable=False),sa.Column("name",sa.String(100),nullable=False),sa.Column("method_type",sa.String(30),nullable=False,server_default="other"),sa.Column("is_active",sa.Boolean,nullable=False,server_default=sa.true()),sa.Column("sort_order",sa.Integer,nullable=False,server_default="0"))
    create("shifts",sa.Column("id",sa.Integer,primary_key=True),sa.Column("branch_id",sa.Integer,sa.ForeignKey("branches.id")),sa.Column("user_id",sa.Integer,sa.ForeignKey("users.id"),nullable=False),sa.Column("opened_at",sa.DateTime,nullable=False),sa.Column("closed_at",sa.DateTime),sa.Column("opening_cash",sa.Numeric(14,2),nullable=False,server_default="0"),sa.Column("expected_cash",sa.Numeric(14,2),nullable=False,server_default="0"),sa.Column("counted_cash",sa.Numeric(14,2)),sa.Column("difference",sa.Numeric(14,2)),sa.Column("status",sa.String(20),nullable=False,server_default="open"),sa.Column("notes",sa.Text))
    create("product_batches",sa.Column("id",sa.Integer,primary_key=True),sa.Column("product_id",sa.Integer,sa.ForeignKey("products.id"),nullable=False),sa.Column("batch_no",sa.String(100),nullable=False),sa.Column("expiry_date",sa.Date),sa.Column("quantity",sa.Numeric(14,3),nullable=False,server_default="0"),sa.Column("cost_price",sa.Numeric(14,2),nullable=False,server_default="0"),sa.Column("is_active",sa.Boolean,nullable=False,server_default=sa.true()))
    create("product_serials",sa.Column("id",sa.Integer,primary_key=True),sa.Column("product_id",sa.Integer,sa.ForeignKey("products.id"),nullable=False),sa.Column("serial_no",sa.String(120),unique=True,nullable=False),sa.Column("status",sa.String(30),nullable=False,server_default="in_stock"),sa.Column("sold_sale_id",sa.Integer,sa.ForeignKey("sales.id")),sa.Column("warranty_until",sa.Date))
    create("recipes",sa.Column("id",sa.Integer,primary_key=True),sa.Column("name",sa.String(150),nullable=False),sa.Column("output_product_id",sa.Integer,sa.ForeignKey("products.id"),nullable=False),sa.Column("output_quantity",sa.Numeric(14,3),nullable=False,server_default="1"),sa.Column("is_active",sa.Boolean,nullable=False,server_default=sa.true()))
    create("recipe_items",sa.Column("id",sa.Integer,primary_key=True),sa.Column("recipe_id",sa.Integer,sa.ForeignKey("recipes.id",ondelete="CASCADE"),nullable=False),sa.Column("product_id",sa.Integer,sa.ForeignKey("products.id"),nullable=False),sa.Column("quantity",sa.Numeric(14,3),nullable=False))
    create("production_orders",sa.Column("id",sa.Integer,primary_key=True),sa.Column("order_no",sa.String(80),unique=True,nullable=False),sa.Column("recipe_id",sa.Integer,sa.ForeignKey("recipes.id"),nullable=False),sa.Column("quantity",sa.Numeric(14,3),nullable=False),sa.Column("status",sa.String(30),nullable=False,server_default="draft"),sa.Column("created_by",sa.Integer,sa.ForeignKey("users.id")),sa.Column("created_at",sa.DateTime,nullable=False),sa.Column("completed_at",sa.DateTime))
    create("online_orders",sa.Column("id",sa.Integer,primary_key=True),sa.Column("order_no",sa.String(80),unique=True,nullable=False),sa.Column("customer_id",sa.Integer,sa.ForeignKey("customers.id")),sa.Column("channel",sa.String(40),nullable=False,server_default="web"),sa.Column("status",sa.String(30),nullable=False,server_default="pending"),sa.Column("total",sa.Numeric(14,2),nullable=False,server_default="0"),sa.Column("payload_json",sa.Text,nullable=False,server_default="{}"),sa.Column("created_at",sa.DateTime,nullable=False),sa.Column("updated_at",sa.DateTime,nullable=False))
    create("online_order_items",sa.Column("id",sa.Integer,primary_key=True),sa.Column("order_id",sa.Integer,sa.ForeignKey("online_orders.id",ondelete="CASCADE"),nullable=False),sa.Column("product_id",sa.Integer,sa.ForeignKey("products.id"),nullable=False),sa.Column("quantity",sa.Numeric(14,3),nullable=False),sa.Column("unit_price",sa.Numeric(14,2),nullable=False),sa.Column("line_total",sa.Numeric(14,2),nullable=False))
    create("devices",sa.Column("id",sa.Integer,primary_key=True),sa.Column("device_code",sa.String(80),unique=True,nullable=False),sa.Column("name",sa.String(150),nullable=False),sa.Column("device_type",sa.String(40),nullable=False,server_default="desktop"),sa.Column("last_seen_at",sa.DateTime),sa.Column("is_active",sa.Boolean,nullable=False,server_default=sa.true()))
    create("sync_outbox",sa.Column("id",sa.Integer,primary_key=True),sa.Column("event_id",sa.String(80),unique=True,nullable=False),sa.Column("entity_type",sa.String(80),nullable=False),sa.Column("entity_id",sa.Integer),sa.Column("action",sa.String(40),nullable=False),sa.Column("payload_json",sa.Text,nullable=False),sa.Column("created_at",sa.DateTime,nullable=False),sa.Column("synced_at",sa.DateTime),sa.Column("attempts",sa.Integer,nullable=False,server_default="0"))
    create("integration_connections",sa.Column("id",sa.Integer,primary_key=True),sa.Column("provider",sa.String(80),nullable=False),sa.Column("name",sa.String(120),nullable=False),sa.Column("status",sa.String(30),nullable=False,server_default="disabled"),sa.Column("settings_json",sa.Text,nullable=False,server_default="{}"),sa.Column("created_at",sa.DateTime,nullable=False))
    create("notifications",sa.Column("id",sa.Integer,primary_key=True),sa.Column("user_id",sa.Integer,sa.ForeignKey("users.id")),sa.Column("kind",sa.String(50),nullable=False),sa.Column("title",sa.String(180),nullable=False),sa.Column("message",sa.Text,nullable=False),sa.Column("is_read",sa.Boolean,nullable=False,server_default=sa.false()),sa.Column("created_at",sa.DateTime,nullable=False))
    # New fields on existing transactions keep old data valid and enable location-aware operations.
    for table in ('sales','purchases'):
        if table in tables:
            cols={c['name'] for c in sa.inspect(bind).get_columns(table)}
            if 'warehouse_id' not in cols:
                if bind.dialect.name == 'sqlite':
                    with op.batch_alter_table(table, schema=None) as batch_op:
                        batch_op.add_column(sa.Column('warehouse_id',sa.Integer,nullable=True))
                else:
                    op.add_column(table, sa.Column('warehouse_id',sa.Integer,nullable=True))
                    try:
                        op.create_foreign_key(f'fk_{table}_warehouse_id', table, 'warehouses', ['warehouse_id'], ['id'])
                    except Exception:
                        pass
    # seed a local default branch/warehouse and standard payment methods using portable SQL.
    conn=op.get_bind()
    if 'branches' in sa.inspect(conn).get_table_names():
        conn.execute(sa.text("INSERT INTO branches(code,name,is_default,is_active,created_at) SELECT 'MAIN','Main Branch',TRUE,TRUE,CURRENT_TIMESTAMP WHERE NOT EXISTS (SELECT 1 FROM branches)"))
        conn.execute(sa.text("INSERT INTO warehouses(branch_id,code,name,is_default,is_active,created_at) SELECT id,'MAIN','Main Warehouse',TRUE,TRUE,CURRENT_TIMESTAMP FROM branches b WHERE b.is_default=TRUE AND NOT EXISTS (SELECT 1 FROM warehouses)"))
    if 'payment_methods' in sa.inspect(conn).get_table_names():
        methods=[('cash','Cash','cash',1),('bank','Bank','bank',2),('mobile_money','Mobile Money','mobile_money',3),('card','Card','card',4),('credit','Credit','credit',5),('cheque','Cheque','other',6)]
        for code,name,kind,order in methods:
            conn.execute(sa.text("INSERT INTO payment_methods(code,name,method_type,is_active,sort_order) SELECT CAST(:c AS VARCHAR(40)),CAST(:n AS VARCHAR(100)),CAST(:k AS VARCHAR(30)),TRUE,:o WHERE NOT EXISTS (SELECT 1 FROM payment_methods WHERE code=CAST(:c AS VARCHAR(40)))"),dict(c=code,n=name,k=kind,o=order))
    if 'price_lists' in sa.inspect(conn).get_table_names():
        conn.execute(sa.text("INSERT INTO price_lists(name,code,currency,is_default,is_active) SELECT 'Retail','RETAIL','TZS',TRUE,TRUE WHERE NOT EXISTS (SELECT 1 FROM price_lists)"))

def downgrade():
    # Additive platform release; preserve operational data on downgrade.
    pass
