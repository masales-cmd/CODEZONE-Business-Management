"""Production hardening: payment bank references and return settlement accounts."""
from alembic import op
import sqlalchemy as sa
revision = "0002_production_hardening"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None
def upgrade():
    bind=op.get_bind(); inspector=sa.inspect(bind)
    def cols(table): return {c["name"] for c in inspector.get_columns(table)}
    for table, fk in (("expenses","fk_expenses_bank_account"),("sales_returns","fk_sales_returns_bank_account"),("purchase_returns","fk_purchase_returns_bank_account")):
        if "bank_account_id" not in cols(table):
            op.add_column(table,sa.Column("bank_account_id",sa.Integer(),nullable=True))
            op.create_foreign_key(fk,table,"bank_accounts",["bank_account_id"],["id"])
def downgrade():
    for table,fk in (("purchase_returns","fk_purchase_returns_bank_account"),("sales_returns","fk_sales_returns_bank_account"),("expenses","fk_expenses_bank_account")):
        op.drop_constraint(fk,table,type_="foreignkey"); op.drop_column(table,"bank_account_id")
