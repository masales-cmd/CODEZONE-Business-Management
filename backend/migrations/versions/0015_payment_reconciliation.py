"""Payment reconciliation controls."""
from alembic import op
import sqlalchemy as sa

revision = "0015_payment_reconciliation"
down_revision = "0014_enterprise_core"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    cols = {c['name'] for c in sa.inspect(bind).get_columns('payment_transactions')}
    if 'reconciled_at' not in cols:
        if bind.dialect.name == 'sqlite':
            with op.batch_alter_table('payment_transactions', schema=None) as batch:
                batch.add_column(sa.Column('reconciled_at', sa.DateTime))
                batch.add_column(sa.Column('reconciled_by', sa.Integer, sa.ForeignKey('users.id')))
        else:
            op.add_column('payment_transactions', sa.Column('reconciled_at', sa.DateTime))
            op.add_column('payment_transactions', sa.Column('reconciled_by', sa.Integer, sa.ForeignKey('users.id')))


def downgrade():
    pass
