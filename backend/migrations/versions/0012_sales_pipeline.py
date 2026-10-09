"""Add generic quotation and sales-order workflow."""
from alembic import op
import sqlalchemy as sa

revision = '0012_sales_pipeline'
down_revision = '0011_platform_core'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if 'sales_quotes' not in tables:
        op.create_table(
            'sales_quotes',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('quote_no', sa.String(length=80), nullable=False, unique=True),
            sa.Column('customer_id', sa.Integer(), sa.ForeignKey('customers.id')),
            sa.Column('issue_date', sa.DateTime(), nullable=False),
            sa.Column('valid_until', sa.Date(), nullable=True),
            sa.Column('subtotal', sa.Numeric(14,2), nullable=False, server_default='0'),
            sa.Column('discount', sa.Numeric(14,2), nullable=False, server_default='0'),
            sa.Column('total', sa.Numeric(14,2), nullable=False, server_default='0'),
            sa.Column('status', sa.String(length=30), nullable=False, server_default='draft'),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id')),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )
    if 'sales_quote_items' not in tables:
        op.create_table(
            'sales_quote_items',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('quote_id', sa.Integer(), sa.ForeignKey('sales_quotes.id', ondelete='CASCADE'), nullable=False),
            sa.Column('product_id', sa.Integer(), sa.ForeignKey('products.id'), nullable=False),
            sa.Column('unit_id', sa.Integer(), sa.ForeignKey('units.id')),
            sa.Column('quantity', sa.Numeric(14,3), nullable=False),
            sa.Column('unit_price', sa.Numeric(14,2), nullable=False),
            sa.Column('line_total', sa.Numeric(14,2), nullable=False),
        )
    if 'sales_orders' not in tables:
        op.create_table(
            'sales_orders',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('order_no', sa.String(length=80), nullable=False, unique=True),
            sa.Column('quote_id', sa.Integer(), sa.ForeignKey('sales_quotes.id')),
            sa.Column('customer_id', sa.Integer(), sa.ForeignKey('customers.id')),
            sa.Column('order_date', sa.DateTime(), nullable=False),
            sa.Column('subtotal', sa.Numeric(14,2), nullable=False, server_default='0'),
            sa.Column('discount', sa.Numeric(14,2), nullable=False, server_default='0'),
            sa.Column('total', sa.Numeric(14,2), nullable=False, server_default='0'),
            sa.Column('status', sa.String(length=30), nullable=False, server_default='draft'),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id')),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )
    if 'sales_order_items' not in tables:
        op.create_table(
            'sales_order_items',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('order_id', sa.Integer(), sa.ForeignKey('sales_orders.id', ondelete='CASCADE'), nullable=False),
            sa.Column('product_id', sa.Integer(), sa.ForeignKey('products.id'), nullable=False),
            sa.Column('unit_id', sa.Integer(), sa.ForeignKey('units.id')),
            sa.Column('quantity', sa.Numeric(14,3), nullable=False),
            sa.Column('unit_price', sa.Numeric(14,2), nullable=False),
            sa.Column('line_total', sa.Numeric(14,2), nullable=False),
        )


def downgrade():
    for table in ('sales_order_items','sales_orders','sales_quote_items','sales_quotes'):
        op.drop_table(table)
