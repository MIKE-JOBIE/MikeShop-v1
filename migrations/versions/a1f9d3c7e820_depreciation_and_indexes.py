"""add deactivated_at for depreciation + index on sale.date

Revision ID: a1f9d3c7e820
Revises: c317524bf4b9
Create Date: 2026-09-19 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1f9d3c7e820'
down_revision = 'c317524bf4b9'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('shoe', sa.Column('deactivated_at', sa.DateTime(), nullable=True))
    op.add_column('product', sa.Column('deactivated_at', sa.DateTime(), nullable=True))

    # Sales History and Reports both filter/group by Sale.date over
    # growing amounts of history (year filters, period comparisons) —
    # this index keeps those queries fast as the table grows.
    op.create_index('ix_sale_date', 'sale', ['date'])
    op.create_index('ix_sale_sold_by', 'sale', ['sold_by'])


def downgrade():
    op.drop_index('ix_sale_sold_by', table_name='sale')
    op.drop_index('ix_sale_date', table_name='sale')
    op.drop_column('product', 'deactivated_at')
    op.drop_column('shoe', 'deactivated_at')
