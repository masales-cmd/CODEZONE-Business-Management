"""Store historical cost on sales return lines."""
from alembic import op
import sqlalchemy as sa
revision="0007_sales_return_cost"
down_revision="0007_customer_release_hardening"
branch_labels=None
depends_on=None

def upgrade():
    cols={c["name"] for c in sa.inspect(op.get_bind()).get_columns("sales_return_items")}
    if "cost_price" not in cols:
        op.add_column("sales_return_items",sa.Column("cost_price",sa.Numeric(14,2),nullable=False,server_default="0"))
        if op.get_bind().dialect.name != "sqlite": op.alter_column("sales_return_items","cost_price",server_default=None)

def downgrade():
    if "cost_price" in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("sales_return_items")}: op.drop_column("sales_return_items","cost_price")
