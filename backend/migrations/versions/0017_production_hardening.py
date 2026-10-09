"""Production hardening: shift-bound cash and audit-friendly return units."""
from alembic import op
import sqlalchemy as sa

revision = '0017_production_hardening'
down_revision = '0016_business_workflows'
branch_labels = None
depends_on = None


def _cols(bind, table):
    return {c['name'] for c in sa.inspect(bind).get_columns(table)}


def _add_column(bind, table, column):
    if column.name in _cols(bind, table):
        return
    if bind.dialect.name == 'sqlite':
        with op.batch_alter_table(table, schema=None) as batch:
            batch.add_column(column)
    else:
        op.add_column(table, column)


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    if 'cash_transactions' in tables:
        _add_column(bind, 'cash_transactions', sa.Column('shift_id', sa.Integer(), sa.ForeignKey('shifts.id')))

    if 'sales_return_items' in tables:
        _add_column(bind, 'sales_return_items', sa.Column('unit_id', sa.Integer(), sa.ForeignKey('units.id')))
        _add_column(bind, 'sales_return_items', sa.Column('requested_quantity', sa.Numeric(14, 3), nullable=False, server_default='0'))

    if 'purchase_return_items' in tables:
        _add_column(bind, 'purchase_return_items', sa.Column('unit_id', sa.Integer(), sa.ForeignKey('units.id')))
        _add_column(bind, 'purchase_return_items', sa.Column('requested_quantity', sa.Numeric(14, 3), nullable=False, server_default='0'))

    # Preserve historical base quantities as the requested quantity for legacy rows.
    if 'sales_return_items' in tables:
        bind.execute(sa.text("UPDATE sales_return_items SET requested_quantity=quantity WHERE requested_quantity=0"))
    if 'purchase_return_items' in tables:
        bind.execute(sa.text("UPDATE purchase_return_items SET requested_quantity=quantity WHERE requested_quantity=0"))


def downgrade():
    # Additive migration; keep transactional history intact on downgrade.
    pass
