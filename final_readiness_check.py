"""Read-only final V1 readiness check for the local SQLite database.

This script never modifies the database. Run it after `flask db upgrade`.
"""
import sqlite3
from pathlib import Path
import re

DB=Path('instance')/'sneakers.db'

def key(v):
    v=' '.join(str(v or '').strip().casefold().split())
    return re.sub(r'[^a-z0-9]+','',v)

if not DB.exists(): raise SystemExit(f'Database not found: {DB}')
con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
try:
    version=con.execute('select version_num from alembic_version').fetchone()[0]
    print(f'[MIGRATION] {version}')
    required=[('shoe','identity_key'),('shoe_size','variant_key'),('product','identity_key'),('product_variant','variant_key')]
    for table,col in required:
        cols={r[1] for r in con.execute(f'pragma table_info({table})')}
        print(f'[COLUMN] {table}.{col}: {"OK" if col in cols else "MISSING"}')
    shoes=con.execute('select id,brand,model from shoe').fetchall()
    products=con.execute('select id,category,brand,model from product').fetchall()
    sizes=con.execute('select id,shoe_id,size from shoe_size').fetchall()
    variants=con.execute('select id,product_id,variant_label,variant_value from product_variant').fetchall()
    checks=[
      ('shoe identity', shoes, lambda r:f'{key(r["brand"])}|{key(r["model"])}'),
      ('shoe variant', sizes, lambda r:f'{r["shoe_id"]}|{key(r["size"])}'),
      ('product identity', products, lambda r:f'{key(r["category"])}|{key(r["brand"])}|{key(r["model"])}'),
      ('product variant', variants, lambda r:f'{r["product_id"]}|{key(r["variant_label"])}|{key(r["variant_value"])}')]
    failed=False
    for name,rows,fn in checks:
        seen={}
        for r in rows: seen.setdefault(fn(r),0); seen[fn(r)]+=1
        dup={k:v for k,v in seen.items() if v>1}
        print(f'[DUPLICATES] {name}: {"FAIL" if dup else "OK"}')
        if dup: failed=True; print(' ',dup)
    dangling=[]
    for table,col,parent,parentcol in [('sale','shoe_id','shoe','id'),('sale','shoe_size_id','shoe_size','id'),('restock','shoe_id','shoe','id'),('restock','shoe_size_id','shoe_size','id')]:
        rows=con.execute(f'SELECT {table}.id FROM {table} LEFT JOIN {parent} ON {table}.{col}={parent}.{parentcol} WHERE {table}.{col} IS NOT NULL AND {parent}.{parentcol} IS NULL LIMIT 10').fetchall()
        if rows: dangling.append((table,col,[r[0] for r in rows]))
    print(f'[REFERENTIAL INTEGRITY] {"FAIL" if dangling else "OK"}')
    if dangling: print(dangling); failed=True
    print(f'[RESULT] {"NOT READY" if failed or version != "patch6_identity_keys" else "V1 READY TO FREEZE"}')
finally:
    con.close()
