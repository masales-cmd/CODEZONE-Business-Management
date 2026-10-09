"""Compatibility bridge for production databases created by v1.2.8 customer hardening."""
from alembic import op
import sqlalchemy as sa

revision='0007_customer_release_hardening'
down_revision='0006_sales_containers'
branch_labels=None
depends_on=None

def upgrade():
    bind=op.get_bind(); cols={c['name'] for c in sa.inspect(bind).get_columns('sales')}
    additions=[
        ('container_type_id',sa.Integer(), 'empty_container_types'),
        ('containers_issued',sa.Numeric(14,3),'default'),
        ('containers_returned',sa.Numeric(14,3),'default'),
        ('container_deposit',sa.Numeric(14,2),'default'),
    ]
    for name,typ,target in additions:
        if name not in cols:
            op.add_column('sales',sa.Column(name,typ,nullable=False if target=='default' else True,server_default='0' if target=='default' else None))
    if bind.dialect.name!='sqlite':
        fks=sa.inspect(bind).get_foreign_keys('sales')
        if not any(f.get('name')=='fk_sales_container_type' for f in fks):
            op.create_foreign_key('fk_sales_container_type','sales','empty_container_types',['container_type_id'],['id'])
        for col in ('containers_issued','containers_returned','container_deposit'):
            try: op.alter_column('sales',col,server_default=None)
            except Exception: pass

def downgrade():
    bind=op.get_bind(); fks={f.get('name') for f in sa.inspect(bind).get_foreign_keys('sales')}
    if 'fk_sales_container_type' in fks and bind.dialect.name!='sqlite': op.drop_constraint('fk_sales_container_type','sales',type_='foreignkey')
    cols={c['name'] for c in sa.inspect(bind).get_columns('sales')}
    for col in ('container_deposit','containers_returned','containers_issued','container_type_id'):
        if col in cols: op.drop_column('sales',col)
