"""Add login tracking and account lockout fields."""
from alembic import op
import sqlalchemy as sa
revision="0003_production_security"
down_revision="0002_production_hardening"
branch_labels=None
depends_on=None

def upgrade():
    bind=op.get_bind(); inspector=sa.inspect(bind)
    cols={c["name"] for c in inspector.get_columns("users")}
    if "last_login_at" not in cols:
        op.add_column("users",sa.Column("last_login_at",sa.DateTime(),nullable=True))
    if "failed_login_attempts" not in cols:
        op.add_column("users",sa.Column("failed_login_attempts",sa.Integer(),nullable=False,server_default="0"))
        op.alter_column("users","failed_login_attempts",server_default=None)
    if "locked_until" not in cols:
        op.add_column("users",sa.Column("locked_until",sa.DateTime(),nullable=True))

def downgrade():
    bind=op.get_bind(); cols={c["name"] for c in sa.inspect(bind).get_columns("users")}
    for c in ("locked_until","failed_login_attempts","last_login_at"):
        if c in cols: op.drop_column("users",c)
