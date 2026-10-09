"""Enterprise business core: tax, split payments, delivery, services, loyalty and stock controls."""
from alembic import op
import sqlalchemy as sa

revision = "0014_enterprise_core"
down_revision = "0013_operations_industry_cloud"
branch_labels = None
depends_on = None


def _tables(bind):
    return set(sa.inspect(bind).get_table_names())


def _cols(bind, table):
    return {c['name'] for c in sa.inspect(bind).get_columns(table)}


def _add_column(bind, table, column):
    if table not in _tables(bind) or column.name in _cols(bind, table):
        return
    if bind.dialect.name == 'sqlite':
        with op.batch_alter_table(table, schema=None) as batch:
            batch.add_column(column)
    else:
        op.add_column(table, column)


def _create(bind, name, *cols, indexes=()):
    if name in _tables(bind):
        return
    op.create_table(name, *cols)
    for idx in indexes:
        op.create_index(idx[0], name, idx[1], unique=idx[2] if len(idx) > 2 else False)


def upgrade():
    bind = op.get_bind()

    _create(bind, 'customer_groups',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('name', sa.String(120), unique=True, nullable=False),
        sa.Column('discount_percent', sa.Numeric(8,2), nullable=False, server_default='0'),
        sa.Column('default_price_list_id', sa.Integer, sa.ForeignKey('price_lists.id')),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default=sa.true()),
    )
    _create(bind, 'tax_rates',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('name', sa.String(80), nullable=False),
        sa.Column('rate', sa.Numeric(8,4), nullable=False, server_default='0'),
        sa.Column('inclusive', sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default=sa.true()),
    )
    _create(bind, 'sale_payments',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('sale_id', sa.Integer, sa.ForeignKey('sales.id', ondelete='CASCADE'), nullable=False),
        sa.Column('payment_method', sa.String(30), nullable=False),
        sa.Column('amount', sa.Numeric(14,2), nullable=False),
        sa.Column('bank_account_id', sa.Integer, sa.ForeignKey('bank_accounts.id')),
        sa.Column('reference', sa.String(120)),
        sa.Column('created_at', sa.DateTime, nullable=False),
    )
    _create(bind, 'payment_transactions',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('direction', sa.String(20), nullable=False),
        sa.Column('payment_method', sa.String(30), nullable=False),
        sa.Column('amount', sa.Numeric(14,2), nullable=False),
        sa.Column('reference', sa.String(120)),
        sa.Column('external_reference', sa.String(160)),
        sa.Column('bank_account_id', sa.Integer, sa.ForeignKey('bank_accounts.id')),
        sa.Column('created_at', sa.DateTime, nullable=False),
    )
    _create(bind, 'payment_allocations',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('customer_payment_id', sa.Integer, sa.ForeignKey('customer_payments.id', ondelete='CASCADE'), nullable=False),
        sa.Column('sale_id', sa.Integer, sa.ForeignKey('sales.id'), nullable=False),
        sa.Column('amount', sa.Numeric(14,2), nullable=False),
    )
    _create(bind, 'delivery_orders',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('delivery_no', sa.String(80), unique=True, nullable=False),
        sa.Column('sale_id', sa.Integer, sa.ForeignKey('sales.id')),
        sa.Column('sales_order_id', sa.Integer, sa.ForeignKey('sales_orders.id')),
        sa.Column('customer_id', sa.Integer, sa.ForeignKey('customers.id')),
        sa.Column('driver_name', sa.String(150)),
        sa.Column('vehicle_no', sa.String(80)),
        sa.Column('address', sa.String(255)),
        sa.Column('status', sa.String(30), nullable=False, server_default='pending'),
        sa.Column('scheduled_at', sa.DateTime),
        sa.Column('delivered_at', sa.DateTime),
        sa.Column('notes', sa.Text),
        sa.Column('created_at', sa.DateTime, nullable=False),
    )
    _create(bind, 'delivery_items',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('delivery_id', sa.Integer, sa.ForeignKey('delivery_orders.id', ondelete='CASCADE'), nullable=False),
        sa.Column('product_id', sa.Integer, sa.ForeignKey('products.id'), nullable=False),
        sa.Column('quantity', sa.Numeric(14,3), nullable=False),
    )
    _create(bind, 'service_orders',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('job_no', sa.String(80), unique=True, nullable=False),
        sa.Column('customer_id', sa.Integer, sa.ForeignKey('customers.id')),
        sa.Column('title', sa.String(180), nullable=False),
        sa.Column('description', sa.Text),
        sa.Column('assigned_user_id', sa.Integer, sa.ForeignKey('users.id')),
        sa.Column('status', sa.String(30), nullable=False, server_default='open'),
        sa.Column('quoted_amount', sa.Numeric(14,2), nullable=False, server_default='0'),
        sa.Column('actual_amount', sa.Numeric(14,2), nullable=False, server_default='0'),
        sa.Column('scheduled_date', sa.Date),
        sa.Column('completed_at', sa.DateTime),
        sa.Column('created_at', sa.DateTime, nullable=False),
    )
    _create(bind, 'loyalty_accounts',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('customer_id', sa.Integer, sa.ForeignKey('customers.id', ondelete='CASCADE'), unique=True, nullable=False),
        sa.Column('points_balance', sa.Integer, nullable=False, server_default='0'),
        sa.Column('lifetime_points', sa.Integer, nullable=False, server_default='0'),
    )
    _create(bind, 'loyalty_transactions',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('account_id', sa.Integer, sa.ForeignKey('loyalty_accounts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('points', sa.Integer, nullable=False),
        sa.Column('reason', sa.String(180), nullable=False),
        sa.Column('reference', sa.String(120)),
        sa.Column('created_at', sa.DateTime, nullable=False),
    )
    _create(bind, 'warehouse_bins',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('warehouse_id', sa.Integer, sa.ForeignKey('warehouses.id', ondelete='CASCADE'), nullable=False),
        sa.Column('code', sa.String(60), nullable=False),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default=sa.true()),
    )
    _create(bind, 'stock_adjustments',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('adjustment_no', sa.String(80), unique=True, nullable=False),
        sa.Column('warehouse_id', sa.Integer, sa.ForeignKey('warehouses.id'), nullable=False),
        sa.Column('reason', sa.String(255), nullable=False),
        sa.Column('status', sa.String(30), nullable=False, server_default='posted'),
        sa.Column('created_by', sa.Integer, sa.ForeignKey('users.id')),
        sa.Column('created_at', sa.DateTime, nullable=False),
    )
    _create(bind, 'stock_adjustment_items',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('adjustment_id', sa.Integer, sa.ForeignKey('stock_adjustments.id', ondelete='CASCADE'), nullable=False),
        sa.Column('product_id', sa.Integer, sa.ForeignKey('products.id'), nullable=False),
        sa.Column('quantity_delta', sa.Numeric(14,3), nullable=False),
    )
    _create(bind, 'approval_requests',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('request_type', sa.String(60), nullable=False),
        sa.Column('entity_type', sa.String(60), nullable=False),
        sa.Column('entity_id', sa.Integer, nullable=False),
        sa.Column('requested_by', sa.Integer, sa.ForeignKey('users.id')),
        sa.Column('approved_by', sa.Integer, sa.ForeignKey('users.id')),
        sa.Column('status', sa.String(30), nullable=False, server_default='pending'),
        sa.Column('reason', sa.Text),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.Column('decided_at', sa.DateTime),
    )

    # Transactional columns for full local ERP workflows.
    _add_column(bind, 'customers', sa.Column('group_id', sa.Integer, sa.ForeignKey('customer_groups.id')))
    _add_column(bind, 'products', sa.Column('tax_rate_id', sa.Integer, sa.ForeignKey('tax_rates.id')))
    for table in ('sales', 'purchases'):
        _add_column(bind, table, sa.Column('branch_id', sa.Integer, sa.ForeignKey('branches.id')))
        _add_column(bind, table, sa.Column('warehouse_id', sa.Integer, sa.ForeignKey('warehouses.id')))
    for col in (
        sa.Column('price_list_id', sa.Integer, sa.ForeignKey('price_lists.id')),
        sa.Column('shift_id', sa.Integer, sa.ForeignKey('shifts.id')),
        sa.Column('tax_rate_id', sa.Integer, sa.ForeignKey('tax_rates.id')),
        sa.Column('tax_amount', sa.Numeric(14,2), nullable=False, server_default='0'),
    ):
        _add_column(bind, 'sales', col)
    _add_column(bind, 'sale_items', sa.Column('batch_id', sa.Integer, sa.ForeignKey('product_batches.id')))
    _add_column(bind, 'sale_items', sa.Column('serial_id', sa.Integer, sa.ForeignKey('product_serials.id')))

    # Helpful uniqueness for location stock.
    if 'stock_balances' in _tables(bind):
        inspector = sa.inspect(bind)
        names = {x['name'] for x in inspector.get_indexes('stock_balances')} | {x['name'] for x in inspector.get_unique_constraints('stock_balances')}
        if 'uq_stock_balance_location_product' not in names:
            try:
                op.create_unique_constraint('uq_stock_balance_location_product', 'stock_balances', ['warehouse_id','product_id'])
            except Exception:
                try:
                    op.create_index('uq_stock_balance_location_product', 'stock_balances', ['warehouse_id','product_id'], unique=True)
                except Exception:
                    pass

    # Seed standard tax/payment configuration.
    conn = op.get_bind()
    if 'tax_rates' in _tables(conn):
        for name, rate, inclusive in [('No Tax', 0, False), ('VAT 18%', 18, False), ('VAT 18% Inclusive', 18, True)]:
            conn.execute(sa.text("INSERT INTO tax_rates(name,rate,inclusive,is_active) SELECT CAST(:n AS VARCHAR(80)),CAST(:r AS NUMERIC(8,4)),CAST(:i AS BOOLEAN),TRUE WHERE NOT EXISTS (SELECT 1 FROM tax_rates WHERE name=CAST(:n AS VARCHAR(80)))"), {'n':name,'r':rate,'i':inclusive})
    if 'payment_methods' in _tables(conn):
        methods=[('mobile_money','Mobile Money','mobile_money',3),('card','Card','card',4),('cheque','Cheque','cheque',6),('credit','Credit','credit',7),('split','Split Payment','split',8)]
        for code,name,kind,order in methods:
            conn.execute(sa.text("INSERT INTO payment_methods(code,name,method_type,is_active,sort_order) SELECT CAST(:c AS VARCHAR(40)),CAST(:n AS VARCHAR(100)),CAST(:k AS VARCHAR(30)),TRUE,:o WHERE NOT EXISTS (SELECT 1 FROM payment_methods WHERE code=CAST(:c AS VARCHAR(40)))"), {'c':code,'n':name,'k':kind,'o':order})


def downgrade():
    # Additive release: preserve operational data on downgrade.
    pass
