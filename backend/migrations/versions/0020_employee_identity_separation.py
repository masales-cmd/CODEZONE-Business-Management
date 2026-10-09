"""Separate HR employee profiles, CODEZONE login accounts, and company mailboxes.

Existing user-linked employee records are preserved and backfilled. Employee profiles
without a login are now supported; no existing user, password, or mailbox is deleted.
"""
from alembic import op
import sqlalchemy as sa

revision = '0020_employee_identity'
down_revision = '0019_employee_email_management'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if 'employee_profiles' not in tables:
        return
    columns = {c['name'] for c in inspector.get_columns('employee_profiles')}
    if 'full_name' not in columns:
        op.add_column('employee_profiles', sa.Column('full_name', sa.String(150), nullable=True))
    if 'company_email' not in columns:
        op.add_column('employee_profiles', sa.Column('company_email', sa.String(255), nullable=True))
    if 'is_active' not in columns:
        op.add_column('employee_profiles', sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()))
    # Make user_id nullable on both PostgreSQL and SQLite (using batch mode for SQLite).
    user_col = next((c for c in inspector.get_columns('employee_profiles') if c['name'] == 'user_id'), None)
    if user_col and not user_col.get('nullable', True):
        if bind.dialect.name == 'sqlite':
            with op.batch_alter_table('employee_profiles') as batch:
                batch.alter_column('user_id', existing_type=sa.Integer(), nullable=True)
        else:
            op.alter_column('employee_profiles', 'user_id', existing_type=sa.Integer(), nullable=True)
    # Preserve existing HR details while separating the mailbox address from the login.
    rows = bind.execute(sa.text(
        "SELECT ep.id, ep.user_id, ep.full_name, ep.company_email, u.name, u.email, u.is_active "
        "FROM employee_profiles ep LEFT JOIN users u ON u.id = ep.user_id"
    )).mappings().all()
    for row in rows:
        updates = {}
        if not row['full_name']:
            updates['full_name'] = row['name']
        if not row['company_email']:
            updates['company_email'] = row['email']
        if row['user_id'] is not None:
            updates['is_active'] = bool(row['is_active'])
        if updates:
            bind.execute(sa.text(
                "UPDATE employee_profiles SET " + ", ".join(f"{k} = :{k}" for k in updates) + " WHERE id = :id"
            ), {**updates, 'id': row['id']})
    # Every existing login must remain represented in the employee directory.
    # Avoid duplicate profile links if a partial prior run created some rows.
    users = bind.execute(sa.text(
        "SELECT u.id, u.name, u.email, u.is_active FROM users u "
        "LEFT JOIN employee_profiles ep ON ep.user_id = u.id WHERE ep.id IS NULL"
    )).mappings().all()
    for user in users:
        bind.execute(sa.text(
            "INSERT INTO employee_profiles (user_id, full_name, company_email, is_active, employment_type, "
            "mailbox_status, created_at, updated_at) "
            "VALUES (:user_id, :full_name, :company_email, :is_active, 'full_time', 'address_only', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ), {'user_id': user['id'], 'full_name': user['name'], 'company_email': user['email'], 'is_active': bool(user['is_active'])})
    # Old employee records often had no employee number. Backfill those records
    # with stable IDs without changing any manually assigned employee number.
    numbered = bind.execute(sa.text(
        "SELECT id, employee_number FROM employee_profiles ORDER BY id"
    )).mappings().all()
    used = {str(row['employee_number']).strip().lower() for row in numbered if row['employee_number'] and str(row['employee_number']).strip()}
    for row in numbered:
        if row['employee_number'] and str(row['employee_number']).strip():
            continue
        candidate_id = max(int(row['id'] or 1), 1)
        while True:
            candidate = f"EMP-{candidate_id:05d}"
            if candidate.lower() not in used:
                break
            candidate_id += 1
        bind.execute(sa.text("UPDATE employee_profiles SET employee_number=:number WHERE id=:id"),
                     {'number': candidate, 'id': row['id']})
        used.add(candidate.lower())

    indexes = {i['name'] for i in sa.inspect(bind).get_indexes('employee_profiles')}
    if 'uq_employee_profiles_company_email' not in indexes:
        if bind.dialect.name == 'sqlite':
            op.create_index('uq_employee_profiles_company_email', 'employee_profiles', ['company_email'], unique=True,
                            sqlite_where=sa.text('company_email IS NOT NULL'))
        elif bind.dialect.name == 'postgresql':
            op.create_index('uq_employee_profiles_company_email', 'employee_profiles', ['company_email'], unique=True,
                            postgresql_where=sa.text('company_email IS NOT NULL'))
        else:
            op.create_index('uq_employee_profiles_company_email', 'employee_profiles', ['company_email'], unique=True)


def downgrade():
    # Intentionally non-destructive: employee data and login links must survive rollback.
    pass
