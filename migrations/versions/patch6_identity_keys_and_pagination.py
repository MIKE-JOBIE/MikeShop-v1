"""Patch 06: canonical identity keys for product/variant deduplication.

Revises: patch4_inventory_uniqueness
"""
from alembic import op
import sqlalchemy as sa
import re

revision = "patch6_identity_keys"
down_revision = "patch4_inventory_uniqueness"
branch_labels = None
depends_on = None

def key(v):
    v = " ".join(str(v or "").strip().casefold().split())
    return re.sub(r"[^a-z0-9]+", "", v)

def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for table, col, typ in [
        ("shoe", "identity_key", sa.String(320)),
        ("shoe_size", "variant_key", sa.String(120)),
        ("product", "identity_key", sa.String(360)),
        ("product_variant", "variant_key", sa.String(120)),
    ]:
        if col not in [c["name"] for c in insp.get_columns(table)]:
            op.add_column(table, sa.Column(col, typ, nullable=True))

    shoes = bind.execute(sa.text("SELECT id, brand, model FROM shoe")).mappings().all()
    for r in shoes:
        bind.execute(sa.text("UPDATE shoe SET identity_key=:k WHERE id=:id"), {"k": f"{key(r['brand'])}|{key(r['model'])}", "id": r['id']})
    sizes = bind.execute(sa.text("SELECT id, size FROM shoe_size")).mappings().all()
    for r in sizes:
        bind.execute(sa.text("UPDATE shoe_size SET variant_key=:k WHERE id=:id"), {"k": key(r['size']), "id": r['id']})
    products = bind.execute(sa.text("SELECT id, category, brand, model FROM product")).mappings().all()
    for r in products:
        bind.execute(sa.text("UPDATE product SET identity_key=:k WHERE id=:id"), {"k": f"{key(r['category'])}|{key(r['brand'])}|{key(r['model'])}", "id": r['id']})
    variants = bind.execute(sa.text("SELECT id, variant_label, variant_value FROM product_variant")).mappings().all()
    for r in variants:
        bind.execute(sa.text("UPDATE product_variant SET variant_key=:k WHERE id=:id"), {"k": f"{key(r['variant_label'])}|{key(r['variant_value'])}", "id": r['id']})

    checks = [
        ("shoe", "identity_key"), ("product", "identity_key"),
        ("shoe_size", "shoe_id, variant_key"),
        ("product_variant", "product_id, variant_key"),
    ]
    for table, cols in checks:
        if bind.execute(sa.text(f"SELECT 1 FROM {table} GROUP BY {cols} HAVING count(*) > 1 LIMIT 1")).first():
            raise RuntimeError(f"Patch 06 cannot continue: normalized duplicate identity exists in {table} ({cols}). Run audit_inventory_duplicates.py and resolve it first.")

    # Existing databases may contain legacy rows; after backfill all rows must have keys.
    for table, col in [("shoe","identity_key"),("shoe_size","variant_key"),("product","identity_key"),("product_variant","variant_key")]:
        bind.execute(sa.text(f"UPDATE {table} SET {col}='' WHERE {col} IS NULL"))

    # Batch mode keeps this migration compatible with SQLite as well as PostgreSQL.
    for table, col in [("shoe","identity_key"),("shoe_size","variant_key"),("product","identity_key"),("product_variant","variant_key")]:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(col, nullable=False)

    for table, name, cols in [
        ("shoe", "uq_shoe_identity_key", ["identity_key"]),
        ("product", "uq_product_identity_key", ["identity_key"]),
        ("shoe_size", "uq_shoe_size_variant_key", ["shoe_id", "variant_key"]),
        ("product_variant", "uq_product_variant_key", ["product_id", "variant_key"]),
    ]:
        with op.batch_alter_table(table) as batch:
            batch.create_unique_constraint(name, cols)

    # High-value indexes for the hot paths used by sales, restocking, notifications and audit/history.
    existing_indexes = { (i['name'], i['column_names'][0]) for t in ['sale','restock','notification','audit_log'] for i in insp.get_indexes(t) }
    indexes = [
        ('ix_sale_date', 'sale', ['date']),
        ('ix_sale_customer_date', 'sale', ['customer_id','date']),
        ('ix_sale_shoe_date', 'sale', ['shoe_id','date']),
        ('ix_sale_product_date', 'sale', ['product_id','date']),
        ('ix_restock_created', 'restock', ['created_at']),
        ('ix_restock_shoe', 'restock', ['shoe_id','created_at']),
        ('ix_restock_product', 'restock', ['product_id','created_at']),
        ('ix_notification_recipient_read_date', 'notification', ['user_id','is_read','created_at']),
        ('ix_audit_timestamp', 'audit_log', ['timestamp']),
    ]
    for name, table, cols in indexes:
        if not any(i[0] == name for i in existing_indexes):
            op.create_index(name, table, cols)

def downgrade():
    for name, table in [("ix_audit_timestamp","audit_log"),("ix_notification_recipient_read_date","notification"),("ix_restock_product","restock"),("ix_restock_shoe","restock"),("ix_restock_created","restock"),("ix_sale_product_date","sale"),("ix_sale_shoe_date","sale"),("ix_sale_customer_date","sale"),("ix_sale_date","sale")]:
        try: op.drop_index(name, table_name=table)
        except Exception: pass
    for name, table in [("uq_product_variant_key","product_variant"),("uq_shoe_size_variant_key","shoe_size"),("uq_product_identity_key","product"),("uq_shoe_identity_key","shoe")]:
        try: op.drop_constraint(name, table, type_="unique")
        except Exception: pass
    for table, col in [("product_variant","variant_key"),("shoe_size","variant_key"),("product","identity_key"),("shoe","identity_key")]:
        try: op.drop_column(table, col)
        except Exception: pass
