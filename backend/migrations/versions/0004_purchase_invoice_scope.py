"""Scope purchase invoice uniqueness by supplier."""
from alembic import op
import sqlalchemy as sa
revision="0004_purchase_invoice_scope"
down_revision="0003_production_security"
branch_labels=None
depends_on=None

def upgrade():
    bind=op.get_bind(); inspector=sa.inspect(bind); dialect=bind.dialect.name
    if dialect=="postgresql":
        # Remove the old global UNIQUE constraint/index when present.
        for c in inspector.get_unique_constraints("purchases"):
            if c.get("name") and c.get("column_names")==["invoice_no"]:
                try: op.drop_constraint(c["name"],"purchases",type_="unique")
                except Exception: pass
        for i in inspector.get_indexes("purchases"):
            if i.get("unique") and i.get("column_names")==["invoice_no"]:
                try: op.drop_index(i["name"],table_name="purchases")
                except Exception: pass
        op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_purchases_supplier_invoice ON purchases (COALESCE(supplier_id, 0), invoice_no)")
    else:
        names={i.get("name") for i in inspector.get_indexes("purchases")}
        if "uq_purchases_supplier_invoice" not in names:
            op.create_index("uq_purchases_supplier_invoice","purchases",["supplier_id","invoice_no"],unique=True)

def downgrade():
    bind=op.get_bind()
    if bind.dialect.name=="postgresql":
        op.execute("DROP INDEX IF EXISTS uq_purchases_supplier_invoice")
    else:
        try: op.drop_index("uq_purchases_supplier_invoice",table_name="purchases")
        except Exception: pass
    # Downgrade intentionally does not recreate the former global unique constraint.
