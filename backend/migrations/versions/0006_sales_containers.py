"""Add empty-container tracking fields to sales."""
from alembic import op
import sqlalchemy as sa
revision="0006_sales_containers"
down_revision="0005_purchase_return_cost"
branch_labels=None
depends_on=None

def upgrade():
    bind=op.get_bind(); cols={c["name"] for c in sa.inspect(bind).get_columns("sales")}
    if "container_type_id" not in cols: op.add_column("sales",sa.Column("container_type_id",sa.Integer(),nullable=True))
    if "containers_issued" not in cols: op.add_column("sales",sa.Column("containers_issued",sa.Numeric(14,3),nullable=False,server_default="0"))
    if "containers_returned" not in cols: op.add_column("sales",sa.Column("containers_returned",sa.Numeric(14,3),nullable=False,server_default="0"))
    if "container_deposit" not in cols: op.add_column("sales",sa.Column("container_deposit",sa.Numeric(14,2),nullable=False,server_default="0"))
    op.execute("UPDATE sales SET containers_issued=COALESCE(containers_issued,0), containers_returned=COALESCE(containers_returned,0), container_deposit=COALESCE(container_deposit,0)")
    fks={f.get("name") for f in sa.inspect(bind).get_foreign_keys("sales")}
    if bind.dialect.name != "sqlite" and "fk_sales_container_type" not in fks:
        op.create_foreign_key("fk_sales_container_type","sales","empty_container_types",["container_type_id"],["id"])
    if bind.dialect.name != "sqlite":
        for c in ("containers_issued","containers_returned","container_deposit"): op.alter_column("sales",c,server_default=None)

def downgrade():
    bind=op.get_bind(); fks={f.get("name") for f in sa.inspect(bind).get_foreign_keys("sales")}
    if "fk_sales_container_type" in fks: op.drop_constraint("fk_sales_container_type","sales",type_="foreignkey")
    for c in ("container_deposit","containers_returned","containers_issued","container_type_id"):
        if c in {x["name"] for x in sa.inspect(bind).get_columns("sales")}: op.drop_column("sales",c)
