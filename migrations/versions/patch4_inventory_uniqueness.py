"""Patch 04: enforce inventory uniqueness at the database level.

Revises: patch3_inventory_integrity
"""
from alembic import op
import sqlalchemy as sa

revision = "patch4_inventory_uniqueness"
down_revision = "patch3_inventory_integrity"
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    # Fail clearly rather than silently corrupting/merging existing inventory.
    duplicate_checks = [
        ("shoe_size", "SELECT 1 FROM shoe_size GROUP BY shoe_id, lower(trim(size)) HAVING count(*) > 1 LIMIT 1"),
        ("product_variant", "SELECT 1 FROM product_variant GROUP BY product_id, lower(trim(variant_label)), lower(trim(variant_value)) HAVING count(*) > 1 LIMIT 1"),
    ]
    for table, sql in duplicate_checks:
        if bind.execute(sa.text(sql)).first():
            raise RuntimeError(f"Cannot apply Patch 04: duplicate records exist in {table}. Run audit_inventory_duplicates.py and resolve them first.")

    # Existing product/shoe identity constraints remain. These functional
    # indexes close the case/whitespace gap for variants on SQLite and Postgres.
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_shoe_size_normalized ON shoe_size (shoe_id, lower(trim(size)))")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_product_variant_normalized ON product_variant (product_id, lower(trim(variant_label)), lower(trim(variant_value)))")

def downgrade():
    op.execute("DROP INDEX IF EXISTS uq_product_variant_normalized")
    op.execute("DROP INDEX IF EXISTS uq_shoe_size_normalized")
