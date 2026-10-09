"""Reconcile customer-owned business profile for upgrades and legacy databases."""
from alembic import op
import sqlalchemy as sa
revision = '0010_profile_reconcile'
down_revision = '0009_business_profile'
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if 'business_profile' not in tables or 'users' not in tables:
        return
    # Backfill one profile from the existing owner/admin user so upgrades do not land in an unconfigured state.
    row = bind.execute(sa.text("SELECT id,name,email FROM users ORDER BY CASE WHEN role_id=(SELECT id FROM roles WHERE name='owner' LIMIT 1) THEN 0 ELSE 1 END,id LIMIT 1")).mappings().first()
    if not row:
        return
    exists = bind.execute(sa.text("SELECT id FROM business_profile ORDER BY id LIMIT 1")).first()
    if not exists:
        bind.execute(sa.text("INSERT INTO business_profile (id,business_name,owner_name,email,currency,created_at,updated_at) VALUES (1,:bn,:on,:em,'TZS',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"), {'bn':'My Business','on':row['name'],'em':row['email']})

def downgrade():
    pass
