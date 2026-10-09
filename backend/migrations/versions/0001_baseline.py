"""Baseline CODEZONE schema."""
from alembic import op
from app.core.database import Base
from app.models import models
revision='0001_baseline'; down_revision=None; branch_labels=None; depends_on=None
def upgrade():
    # Baseline migration for installations that start from an empty database.
    Base.metadata.create_all(bind=op.get_bind())
def downgrade():
    Base.metadata.drop_all(bind=op.get_bind())
