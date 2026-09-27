"""Safely reconcile canonical inventory duplicates in local SQLite.

Dry-run by default. Use --apply only after reviewing. A timestamped backup is
created before any write. The repair preserves historical Sale/Restock rows by
repointing them to the canonical parent/variant before duplicate rows are removed.
"""
import argparse, re, shutil, sqlite3
from datetime import datetime
from pathlib import Path

def key(v):
    v = " ".join(str(v or "").strip().casefold().split())
    return re.sub(r"[^a-z0-9]+", "", v)

def groups(rows, fn):
    out = {}
    for r in rows: out.setdefault(fn(r), []).append(r)
    return {k:v for k,v in out.items() if len(v)>1}

def choose_primary(con, rows):
    scored=[]
    for r in rows:
        total=con.execute('SELECT COALESCE(SUM(quantity),0) FROM shoe_size WHERE shoe_id=?',(r['id'],)).fetchone()[0]
        scored.append((1 if r['is_active'] else 0, total, -r['id'], r))
    return max(scored, key=lambda x:(x[0],x[1],x[2]))[3]

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--db', default='instance/sneakers.db')
    ap.add_argument('--apply', action='store_true')
    args=ap.parse_args()
    db=Path(args.db)
    if not db.exists(): raise SystemExit(f'Database not found: {db}')
    con=sqlite3.connect(db); con.row_factory=sqlite3.Row
    try:
        # This repair is intentionally for the Patch 06 SQLite state, where the
        # new identity columns may exist but have not yet been constrained.
        cols={r[1] for r in con.execute('PRAGMA table_info(shoe)').fetchall()}
        if 'identity_key' not in cols:
            raise SystemExit('Patch 06 identity columns are not present. Run the Patch 06 migration first or use the earlier repair process.')
        con.execute("UPDATE shoe SET identity_key=? WHERE id=?", ('', -1)) if False else None
        shoes=con.execute('SELECT * FROM shoe ORDER BY id').fetchall()
        products=con.execute('SELECT * FROM product ORDER BY id').fetchall()
        sg=groups(shoes, lambda r:f'{key(r["brand"])}|{key(r["model"])}')
        pg=groups(products, lambda r:f'{key(r["category"])}|{key(r["brand"])}|{key(r["model"])}')
        print('Canonical duplicate shoe groups:', len(sg))
        for identity,rs in sg.items(): print(' SHOES',identity,'=>',[(r['id'],r['brand'],r['model'],r['is_active']) for r in rs])
        print('Canonical duplicate product groups:', len(pg))
        for identity,rs in pg.items(): print(' PRODUCTS',identity,'=>',[(r['id'],r['category'],r['brand'],r['model'],r['is_active']) for r in rs])
        if not args.apply:
            print('\nDry run only. Re-run with --apply after review.')
            return
        backup=db.with_name(db.name+'.backup_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
        shutil.copy2(db, backup); print('Backup:',backup)
        con.execute('BEGIN IMMEDIATE')
        # Canonicalize keys first so duplicate variants can be matched reliably.
        # Patch 06 key removes punctuation, so rebuild it exactly.
        for r in con.execute('SELECT id,brand,model FROM shoe').fetchall():
            con.execute('UPDATE shoe SET identity_key=? WHERE id=?',(f'{key(r["brand"])}|{key(r["model"])}',r['id']))
        for r in con.execute('SELECT id,size FROM shoe_size').fetchall():
            con.execute('UPDATE shoe_size SET variant_key=? WHERE id=?',(key(r['size']),r['id']))
        for r in con.execute('SELECT id,category,brand,model FROM product').fetchall():
            con.execute('UPDATE product SET identity_key=? WHERE id=?',(f'{key(r["category"])}|{key(r["brand"])}|{key(r["model"])}',r['id']))
        for r in con.execute('SELECT id,variant_label,variant_value FROM product_variant').fetchall():
            con.execute('UPDATE product_variant SET variant_key=? WHERE id=?',(f'{key(r["variant_label"])}|{key(r["variant_value"])}',r['id']))

        # Re-read after key backfill.
        shoes=con.execute('SELECT * FROM shoe ORDER BY id').fetchall()
        products=con.execute('SELECT * FROM product ORDER BY id').fetchall()
        sg=groups(shoes, lambda r:r['identity_key'])
        pg=groups(products, lambda r:r['identity_key'])

        for identity,rs in sg.items():
            primary=choose_primary(con, rs)['id']
            for dup in rs:
                did=dup['id']
                if did==primary: continue
                sizes=con.execute('SELECT * FROM shoe_size WHERE shoe_id=? ORDER BY id',(did,)).fetchall()
                for sz in sizes:
                    target=con.execute('SELECT * FROM shoe_size WHERE shoe_id=? AND variant_key=?',(primary,sz['variant_key'])).fetchone()
                    if target is None:
                        con.execute('UPDATE shoe_size SET shoe_id=? WHERE id=?',(primary,sz['id']))
                        continue
                    oldq=target['quantity'] or 0; newq=sz['quantity'] or 0; total=oldq+newq
                    weighted=((oldq*(target['cost_usd'] or 0))+(newq*(sz['cost_usd'] or 0)))/total if total else (target['cost_usd'] or sz['cost_usd'] or 0)
                    # Historical rows first: these references must never point at
                    # a variant that is about to be deleted.
                    con.execute('UPDATE sale SET shoe_id=?, shoe_size_id=? WHERE shoe_id=? AND shoe_size_id=?',(primary,target['id'],did,sz['id']))
                    con.execute('UPDATE restock SET shoe_id=?, shoe_size_id=? WHERE shoe_id=? AND shoe_size_id=?',(primary,target['id'],did,sz['id']))
                    con.execute('UPDATE shoe_size SET quantity=?, cost_usd=?, sell_usd=? WHERE id=?',(total,weighted,target['sell_usd'] if target['sell_usd'] is not None else sz['sell_usd'],target['id']))
                    con.execute('DELETE FROM shoe_size WHERE id=?',(sz['id'],))
                con.execute('UPDATE sale SET shoe_id=? WHERE shoe_id=?',(primary,did))
                con.execute('UPDATE restock SET shoe_id=? WHERE shoe_id=?',(primary,did))
                con.execute('DELETE FROM shoe WHERE id=?',(did,))

        # Products are handled the same way if canonical duplicates exist.
        for identity,rs in pg.items():
            primary=rs[0]['id']
            for dup in rs[1:]:
                did=dup['id']
                variants=con.execute('SELECT * FROM product_variant WHERE product_id=? ORDER BY id',(did,)).fetchall()
                for v in variants:
                    target=con.execute('SELECT * FROM product_variant WHERE product_id=? AND variant_key=?',(primary,v['variant_key'])).fetchone()
                    if target is None:
                        con.execute('UPDATE product_variant SET product_id=? WHERE id=?',(primary,v['id']))
                    else:
                        oldq=target['quantity'] or 0; newq=v['quantity'] or 0; total=oldq+newq
                        weighted=((oldq*(target['cost_usd'] or 0))+(newq*(v['cost_usd'] or 0)))/total if total else (target['cost_usd'] or v['cost_usd'] or 0)
                        con.execute('UPDATE sale SET product_id=?, product_variant_id=? WHERE product_id=? AND product_variant_id=?',(primary,target['id'],did,v['id']))
                        con.execute('UPDATE restock SET product_id=?, product_variant_id=? WHERE product_id=? AND product_variant_id=?',(primary,target['id'],did,v['id']))
                        con.execute('UPDATE product_variant SET quantity=?, cost_usd=?, sell_usd=? WHERE id=?',(total,weighted,target['sell_usd'] if target['sell_usd'] is not None else v['sell_usd'],target['id']))
                        con.execute('DELETE FROM product_variant WHERE id=?',(v['id'],))
                con.execute('UPDATE sale SET product_id=? WHERE product_id=?',(primary,did))
                con.execute('UPDATE restock SET product_id=? WHERE product_id=?',(primary,did))
                con.execute('DELETE FROM product WHERE id=?',(did,))

        # Final canonical duplicate check before commit.
        for table,col in [('shoe','identity_key'),('product','identity_key')]:
            if con.execute(f'SELECT 1 FROM {table} GROUP BY {col} HAVING COUNT(*)>1 LIMIT 1').fetchone():
                raise RuntimeError(f'Unresolved duplicate identity remains in {table}.')
        con.commit(); print('Inventory identity reconciliation completed successfully.')
    except Exception:
        con.rollback(); print('No changes committed. Backup remains at', backup if 'backup' in locals() else 'none'); raise
    finally: con.close()

if __name__=='__main__': main()
