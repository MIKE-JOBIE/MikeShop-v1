import sqlite3
import re
from pathlib import Path

DB = Path("instance") / "sneakers.db"

def key(value):
    value = " ".join(str(value or "").strip().casefold().split())
    return re.sub(r"[^a-z0-9]+", "", value)

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

print("=" * 70)
print("MIKESHOP PATCH 06 NORMALIZED DUPLICATE CHECK")
print("=" * 70)
print(f"Database: {DB.resolve()}")
print()

# ---------------------------------------------------------
# SHOES
# ---------------------------------------------------------
print("SHOES")
print("-" * 70)

rows = conn.execute("""
    SELECT id, brand, model
    FROM shoe
    ORDER BY id
""").fetchall()

groups = {}

for row in rows:
    identity = f"{key(row['brand'])}|{key(row['model'])}"
    groups.setdefault(identity, []).append(row)

found = False

for identity, items in groups.items():
    if len(items) > 1:
        found = True
        print(f"\nDUPLICATE IDENTITY KEY: {identity}")

        for item in items:
            print(
                f"  ID={item['id']} | "
                f"Brand={item['brand']!r} | "
                f"Model={item['model']!r}"
            )

if not found:
    print("  None found.")

# ---------------------------------------------------------
# SHOE SIZES
# ---------------------------------------------------------
print("\nSHOE VARIANTS / SIZES")
print("-" * 70)

rows = conn.execute("""
    SELECT id, shoe_id, size
    FROM shoe_size
    ORDER BY shoe_id, id
""").fetchall()

groups = {}

for row in rows:
    variant = key(row["size"])
    identity = (row["shoe_id"], variant)
    groups.setdefault(identity, []).append(row)

found = False

for identity, items in groups.items():
    if len(items) > 1:
        found = True
        print(f"\nDUPLICATE VARIANT: shoe_id={identity[0]}, key={identity[1]}")

        for item in items:
            print(
                f"  ID={item['id']} | "
                f"Size={item['size']!r}"
            )

if not found:
    print("  None found.")

# ---------------------------------------------------------
# PRODUCTS
# ---------------------------------------------------------
print("\nPRODUCTS")
print("-" * 70)

rows = conn.execute("""
    SELECT id, category, brand, model
    FROM product
    ORDER BY id
""").fetchall()

groups = {}

for row in rows:
    identity = (
        f"{key(row['category'])}|"
        f"{key(row['brand'])}|"
        f"{key(row['model'])}"
    )
    groups.setdefault(identity, []).append(row)

found = False

for identity, items in groups.items():
    if len(items) > 1:
        found = True
        print(f"\nDUPLICATE PRODUCT IDENTITY: {identity}")

        for item in items:
            print(
                f"  ID={item['id']} | "
                f"Category={item['category']!r} | "
                f"Brand={item['brand']!r} | "
                f"Model={item['model']!r}"
            )

if not found:
    print("  None found.")

# ---------------------------------------------------------
# PRODUCT VARIANTS
# ---------------------------------------------------------
print("\nPRODUCT VARIANTS")
print("-" * 70)

rows = conn.execute("""
    SELECT id, product_id, variant_label, variant_value
    FROM product_variant
    ORDER BY product_id, id
""").fetchall()

groups = {}

for row in rows:
    variant = (
        f"{key(row['variant_label'])}|"
        f"{key(row['variant_value'])}"
    )
    identity = (row["product_id"], variant)
    groups.setdefault(identity, []).append(row)

found = False

for identity, items in groups.items():
    if len(items) > 1:
        found = True
        print(
            f"\nDUPLICATE PRODUCT VARIANT: "
            f"product_id={identity[0]}, key={identity[1]}"
        )

        for item in items:
            print(
                f"  ID={item['id']} | "
                f"Label={item['variant_label']!r} | "
                f"Value={item['variant_value']!r}"
            )

if not found:
    print("  None found.")

print()
print("=" * 70)
print("CHECK COMPLETE — NO DATA WAS MODIFIED")
print("=" * 70)

conn.close()