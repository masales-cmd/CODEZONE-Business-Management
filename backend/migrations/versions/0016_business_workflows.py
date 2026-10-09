"""Business workflows: purchasing orders and service invoicing."""
from alembic import op
import sqlalchemy as sa

revision = "0016_business_workflows"
down_revision = "0015_payment_reconciliation"
branch_labels = None
depends_on = None

def _add_if_missing(table, column):
    bind=op.get_bind()
    cols={c["name"] for c in sa.inspect(bind).get_columns(table)}
    if column.name in cols: return
    op.add_column(table,column)

def upgrade():
    bind=op.get_bind()
    # Service billing link
    _add_if_missing("service_orders", sa.Column("sale_id", sa.Integer(), sa.ForeignKey("sales.id")))
    insp=sa.inspect(bind)
    existing=set(insp.get_table_names())
    if "purchase_orders" not in existing:
        op.create_table(
            "purchase_orders",
            sa.Column("id",sa.Integer(),primary_key=True),
            sa.Column("po_no",sa.String(80),nullable=False,unique=True),
            sa.Column("supplier_id",sa.Integer(),sa.ForeignKey("suppliers.id")),
            sa.Column("branch_id",sa.Integer(),sa.ForeignKey("branches.id")),
            sa.Column("warehouse_id",sa.Integer(),sa.ForeignKey("warehouses.id")),
            sa.Column("order_date",sa.DateTime(),nullable=False),
            sa.Column("expected_date",sa.Date()),
            sa.Column("subtotal",sa.Numeric(14,2),nullable=False,server_default="0"),
            sa.Column("discount",sa.Numeric(14,2),nullable=False,server_default="0"),
            sa.Column("total",sa.Numeric(14,2),nullable=False,server_default="0"),
            sa.Column("status",sa.String(30),nullable=False,server_default="draft"),
            sa.Column("notes",sa.Text()),
            sa.Column("created_by",sa.Integer(),sa.ForeignKey("users.id")),
            sa.Column("created_at",sa.DateTime(),nullable=False),
            sa.Column("updated_at",sa.DateTime(),nullable=False),
        )
    if "purchase_order_items" not in existing:
        op.create_table(
            "purchase_order_items",
            sa.Column("id",sa.Integer(),primary_key=True),
            sa.Column("purchase_order_id",sa.Integer(),sa.ForeignKey("purchase_orders.id",ondelete="CASCADE"),nullable=False),
            sa.Column("product_id",sa.Integer(),sa.ForeignKey("products.id"),nullable=False),
            sa.Column("unit_id",sa.Integer(),sa.ForeignKey("units.id")),
            sa.Column("quantity",sa.Numeric(14,3),nullable=False),
            sa.Column("unit_price",sa.Numeric(14,2),nullable=False),
            sa.Column("line_total",sa.Numeric(14,2),nullable=False),
            sa.Column("batch_no",sa.String(120)),
            sa.Column("expiry_date",sa.Date()),
        )

def downgrade():
    pass
