"""Reconcile production schema gaps: units on line items and expense bank account."""
from alembic import op
import sqlalchemy as sa
revision="0008_schema_reconciliation"
down_revision="0007_sales_return_cost"
branch_labels=None
depends_on=None

def _add_column_and_fk(table,column,target):
    bind=op.get_bind(); inspector=sa.inspect(bind)
    cols={c["name"] for c in inspector.get_columns(table)}
    if column not in cols:
        op.add_column(table,sa.Column(column,sa.Integer(),nullable=True))
    fks=sa.inspect(op.get_bind()).get_foreign_keys(table)
    if bind.dialect.name != "sqlite" and not any(f.get("referred_table")==target and f.get("constrained_columns")==[column] for f in fks):
        op.create_foreign_key(f"fk_{table}_{column}",table,target,[column],["id"])

def upgrade():
    _add_column_and_fk("sale_items","unit_id","units")
    _add_column_and_fk("purchase_items","unit_id","units")
    _add_column_and_fk("expenses","bank_account_id","bank_accounts")

def downgrade():
    bind=op.get_bind()
    for table,column,fk in (("expenses","bank_account_id","fk_expenses_bank_account"),("purchase_items","unit_id","fk_purchase_items_unit_id"),("sale_items","unit_id","fk_sale_items_unit_id")):
        fks={f.get("name") for f in sa.inspect(bind).get_foreign_keys(table)}
        if fk in fks:
            op.drop_constraint(fk,table,type_="foreignkey")
        if column in {c["name"] for c in sa.inspect(bind).get_columns(table)}:
            op.drop_column(table,column)
