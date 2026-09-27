"""Read-only inventory duplicate audit using Patch 06 canonical normalization."""
import re
import sqlite3
from pathlib import Path

DB = Path("instance") / "sneakers.db"

def key(value):
    value = " ".join(str(value or "").strip().casefold().split())
    return re.sub(r"[^a-z0-9]+", "", value)

def groups(rows, fn):
    out = {}
    for row in rows:
        out.setdefault(fn(row), []).append(row)
    return {k: v for k, v in out.items() if len(v) > 1}

if not DB.exists():
    raise SystemExit(f"Database not found: {DB}")

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
try:
    shoes = con.execute("SELECT id, brand, model FROM shoe ORDER BY id").fetchall()
    products = con.execute("SELECT id, category, brand, model FROM product ORDER BY id").fetchall()
    sizes = con.execute("SELECT id, shoe_id, size FROM shoe_size ORDER BY id").fetchall()
    variants = con.execute("SELECT id, product_id, variant_label, variant_value FROM product_variant ORDER BY id").fetchall()

    checks = [
        ("Shoe identity duplicates", groups(shoes, lambda r: f"{key(r['brand'])}|{key(r['model'])}")),
        ("Shoe variant duplicates", groups(sizes, lambda r: f"{r['shoe_id']}|{key(r['size'])}")),
        ("Product identity duplicates", groups(products, lambda r: f"{key(r['category'])}|{key(r['brand'])}|{key(r['model'])}")),
        ("Product variant duplicates", groups(variants, lambda r: f"{r['product_id']}|{key(r['variant_label'])}|{key(r['variant_value'])}")),
    ]
    failed = False
    for title, dupes in checks:
        print(title + ':')
        if not dupes:
            print('  none')
            continue
        failed = True
        for identity, rows in dupes.items():
            print(' ', identity, '=>', [dict(r) for r in rows])
    if failed:
        raise SystemExit("Canonical duplicate groups exist. Resolve them before completing Patch 06.")
    print("No canonical duplicate groups detected.")
finally:
    con.close()
