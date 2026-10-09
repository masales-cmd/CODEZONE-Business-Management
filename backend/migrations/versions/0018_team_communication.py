"""Add internal team messages and assignable tasks."""
from alembic import op
import sqlalchemy as sa

revision = '0018_team_communication'
down_revision = '0017_production_hardening'
branch_labels = None
depends_on = None

def _tables(bind):
    return set(sa.inspect(bind).get_table_names())

def upgrade():
    bind = op.get_bind()
    existing = _tables(bind)
    if 'conversations' not in existing:
        op.create_table(
            'conversations',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('subject', sa.String(180)),
            sa.Column('kind', sa.String(20), nullable=False, server_default='direct'),
            sa.Column('branch_id', sa.Integer(), sa.ForeignKey('branches.id')) ,
            sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )
    existing = _tables(bind)
    if 'conversation_participants' not in existing:
        op.create_table(
            'conversation_participants',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('joined_at', sa.DateTime(), nullable=False),
            sa.Column('last_read_at', sa.DateTime()),
            sa.UniqueConstraint('conversation_id', 'user_id', name='uq_conversation_participant'),
        )
        op.create_index('ix_conversation_participants_user_id', 'conversation_participants', ['user_id'])
        op.create_index('ix_conversation_participants_conversation_id', 'conversation_participants', ['conversation_id'])
    existing = _tables(bind)
    if 'team_messages' not in existing:
        op.create_table(
            'team_messages',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
            sa.Column('sender_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('body', sa.Text(), nullable=False),
            sa.Column('linked_entity_type', sa.String(60)),
            sa.Column('linked_entity_id', sa.Integer()),
            sa.Column('created_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_team_messages_conversation_id', 'team_messages', ['conversation_id'])
    existing = _tables(bind)
    if 'team_tasks' not in existing:
        op.create_table(
            'team_tasks',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('title', sa.String(180), nullable=False),
            sa.Column('description', sa.Text()),
            sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('assigned_to', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('branch_id', sa.Integer(), sa.ForeignKey('branches.id')),
            sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id', ondelete='SET NULL')),
            sa.Column('priority', sa.String(20), nullable=False, server_default='normal'),
            sa.Column('status', sa.String(30), nullable=False, server_default='open'),
            sa.Column('due_at', sa.DateTime()),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.Column('completed_at', sa.DateTime()),
        )
        op.create_index('ix_team_tasks_assigned_to', 'team_tasks', ['assigned_to'])
        op.create_index('ix_team_tasks_created_by', 'team_tasks', ['created_by'])

def downgrade():
    # Keep operational communication history during downgrade; removal is deliberately non-destructive.
    pass
