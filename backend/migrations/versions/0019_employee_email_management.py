"""Add employee profiles and company email domain settings."""
from alembic import op
import sqlalchemy as sa

revision = '0019_employee_email_management'
down_revision = '0018_team_communication'
branch_labels = None
depends_on = None

def upgrade():
    bind=op.get_bind()
    inspector=sa.inspect(bind)
    tables=set(inspector.get_table_names())
    if 'business_profile' in tables and 'email_domain' not in {c['name'] for c in inspector.get_columns('business_profile')}:
        op.add_column('business_profile',sa.Column('email_domain',sa.String(255),nullable=True))
    if 'employee_profiles' not in tables:
        op.create_table(
            'employee_profiles',
            sa.Column('id',sa.Integer(),primary_key=True),
            sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False,unique=True),
            sa.Column('employee_number',sa.String(40),nullable=True,unique=True),
            sa.Column('phone',sa.String(50),nullable=True),
            sa.Column('department',sa.String(100),nullable=True),
            sa.Column('job_title',sa.String(100),nullable=True),
            sa.Column('employment_type',sa.String(30),nullable=False,server_default='full_time'),
            sa.Column('hire_date',sa.Date(),nullable=True),
            sa.Column('termination_date',sa.Date(),nullable=True),
            sa.Column('personal_email',sa.String(255),nullable=True),
            sa.Column('notes',sa.Text(),nullable=True),
            sa.Column('mailbox_status',sa.String(40),nullable=False,server_default='address_only'),
            sa.Column('mailbox_provider',sa.String(40),nullable=True),
            sa.Column('mailbox_last_error',sa.String(250),nullable=True),
            sa.Column('created_at',sa.DateTime(),nullable=False,server_default=sa.func.current_timestamp()),
            sa.Column('updated_at',sa.DateTime(),nullable=False,server_default=sa.func.current_timestamp()),
        )
        op.create_index('ix_employee_profiles_department','employee_profiles',['department'])
        op.create_index('ix_employee_profiles_mailbox_status','employee_profiles',['mailbox_status'])

def downgrade():
    # Preserve employee and company-email history on downgrade, like other CODEZONE operational migrations.
    pass
