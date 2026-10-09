"""Add customer-owned business profile for first-time setup."""
from alembic import op
import sqlalchemy as sa
revision="0009_business_profile"
down_revision="0008_schema_reconciliation"
branch_labels=None
depends_on=None

def upgrade():
    bind=op.get_bind()
    if "business_profile" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "business_profile",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("business_name", sa.String(180), nullable=False),
            sa.Column("owner_name", sa.String(150), nullable=False),
            sa.Column("email", sa.String(255), nullable=False),
            sa.Column("phone", sa.String(50), nullable=True),
            sa.Column("address", sa.String(255), nullable=True),
            sa.Column("tin", sa.String(80), nullable=True),
            sa.Column("vat_number", sa.String(80), nullable=True),
            sa.Column("currency", sa.String(10), nullable=False, server_default="TZS"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

def downgrade():
    bind=op.get_bind()
    if "business_profile" in sa.inspect(bind).get_table_names():
        op.drop_table("business_profile")
