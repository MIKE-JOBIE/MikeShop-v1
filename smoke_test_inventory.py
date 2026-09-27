"""Static/runtime-independent smoke checks for MikeShop inventory invariants.
Run in project root: python smoke_test_inventory.py
"""
import ast, pathlib, sqlite3, re, sys
ROOT=pathlib.Path(__file__).resolve().parent
errs=[]
for p in ROOT.rglob('*.py'):
    try: ast.parse(p.read_text(), filename=str(p))
    except Exception as e: errs.append(f'{p}: {e}')
print('Python syntax:', 'PASS' if not errs else 'FAIL')
for e in errs: print(e)
# Check migration chain textually without importing app dependencies.
mig=ROOT/'migrations/versions/patch6_identity_keys_and_pagination.py'
text=mig.read_text()
checks={
 'migration_revision':'revision = "patch6_identity_keys"' in text,
 'migration_parent':'down_revision = "patch4_inventory_uniqueness"' in text,
 'identity_constraints':'uq_shoe_identity_key' in text and 'uq_product_identity_key' in text,
 'variant_constraints':'uq_shoe_size_variant_key' in text and 'uq_product_variant_key' in text,
 'hot_indexes':'ix_sale_date' in text and 'ix_notification_recipient_read_date' in text,
}
for k,v in checks.items(): print(k+':', 'PASS' if v else 'FAIL')
# If bundled DB is present, report normalized duplicates; do not mutate it.
db=ROOT/'instance/sneakers.db'
if db.exists():
    con=sqlite3.connect(db)
    def key(v): return re.sub(r'[^a-z0-9]+','', ' '.join(str(v or '').strip().casefold().split()))
    rows=con.execute('select id,brand,model from shoe').fetchall(); seen={}; dup=[]
    for r in rows:
        k=f'{key(r[1])}|{key(r[2])}'
        if k in seen: dup.append((seen[k],r[0],k))
        else: seen[k]=r[0]
    print('bundled DB normalized shoe duplicates:', dup or 'NONE')
    con.close()
