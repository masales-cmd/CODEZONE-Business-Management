"""Generalize CODEZONE into a configurable multi-industry business platform."""
from alembic import op
import sqlalchemy as sa

revision = '0011_platform_core'
down_revision = '0010_profile_reconcile'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if 'business_profile' in tables:
        existing = {c['name'] for c in sa.inspect(bind).get_columns('business_profile')}
        if 'industry_profile' not in existing:
            op.add_column('business_profile', sa.Column('industry_profile', sa.String(length=40), nullable=False, server_default='retail'))
        if 'onboarding_completed' not in existing:
            op.add_column('business_profile', sa.Column('onboarding_completed', sa.Boolean(), nullable=False, server_default=sa.false()))
    if 'products' in tables:
        existing = {c['name'] for c in sa.inspect(bind).get_columns('products')}
        if 'item_type' not in existing:
            op.add_column('products', sa.Column('item_type', sa.String(length=20), nullable=False, server_default='product'))
        if 'barcode' not in existing:
            op.add_column('products', sa.Column('barcode', sa.String(length=120), nullable=True))
            op.create_unique_constraint('uq_products_barcode', 'products', ['barcode'])
        if 'brand' not in existing:
            op.add_column('products', sa.Column('brand', sa.String(length=120), nullable=True))
        if 'track_inventory' not in existing:
            op.add_column('products', sa.Column('track_inventory', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    # Platform columns are additive and safe to leave during rollback of an upgrade chain.
    pass
