"""Patch 03: inventory integrity indexes.

Revision ID: patch3_inventory_integrity
Revises: a1f9d3c7e820
"""
from alembic import op
import sqlalchemy as sa

revision = "patch3_inventory_integrity"
down_revision = "a1f9d3c7e820"
branch_labels = None
depends_on = None

def upgrade():
    # The ORM already declares these unique constraints. These indexes improve
    # lookup performance without changing existing records or deleting data.
    op.create_index("ix_shoe_active_brand_model", "shoe", ["is_active", "brand", "model"], unique=False)
    op.create_index("ix_product_active_category_brand_model", "product", ["is_active", "category", "brand", "model"], unique=False)
    op.create_index("ix_sale_date_shoe", "sale", ["date", "shoe_id"], unique=False)
    op.create_index("ix_sale_date_product", "sale", ["date", "product_id"], unique=False)

def downgrade():
    op.drop_index("ix_sale_date_product", table_name="sale")
    op.drop_index("ix_sale_date_shoe", table_name="sale")
    op.drop_index("ix_product_active_category_brand_model", table_name="product")
    op.drop_index("ix_shoe_active_brand_model", table_name="shoe")
