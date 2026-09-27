# MikeShop Patch 06 — Real-world readiness checklist

## Role matrix

| Capability | Owner | Admin | Staff |
|---|---:|---:|---:|
| Dashboard / inventory viewing | Yes | Yes | Yes |
| Sell inventory | Yes | Yes | Yes |
| Add product | Yes | Yes | No |
| Restock / add variant | Yes | Yes | No |
| Activate/deactivate products | Yes | Yes | No |
| Add/edit customers | Yes | Yes | No |
| Add expenses | Yes | Yes | No |
| Reports / exports | Yes | Yes | No |
| View receipts | Yes | Yes | Yes |
| Add staff accounts | Yes | No | No |
| Change staff roles | Yes | No | No |
| Reset staff passwords | Yes | No | No |
| Activate/deactivate staff | Yes | No | No |
| Delete staff | Yes | No | No |

## Inventory identity rule

Brand/product name + model are one product identity. Comparison ignores case, whitespace and punctuation, so examples such as `Nike AirMax90`, `Nike Air Max 90`, and `Nike-AirMax 90` resolve to the same identity.

Variants are separate stock units under that identity. Add Product creates the product and its first variant; Restock updates an existing variant or adds a new variant to an existing product.

## Before first run of Patch 06

1. Back up the current SQLite database.
2. Run:
   `python repair_inventory_duplicates.py`
3. Review the proposed normalized duplicate groups.
4. If correct, run:
   `python repair_inventory_duplicates.py --apply`
5. Run database migrations:
   `flask db upgrade`
6. Start the application and perform the runtime test plan below.

The repair tool is deliberately dry-run by default. It makes a timestamped database backup before applying merges.

## Runtime acceptance test

- Create `Nike AirMax90 / 40`.
- Try `Nike Air Max 90 / 41` in Add Product: it must block and direct to Restock.
- Add size 41 through Restock.
- Reduce a variant to 10: Low Stock must show.
- Restock it to 11+: Low Stock must clear and Stock Control must show In stock.
- Sell 5+ units in seven days: High Demand must show.
- Confirm the Inventory table and Dashboard use the same current quantity.
- Confirm receipts never expose profit.
- Owner can manage staff; Admin/Staff cannot access Owner-only staff routes.
- Staff can sell but cannot add/restock/deactivate products or add expenses.
- Mark a notification read from the bell and verify the badge updates.
- Create a notification-producing event and verify only the intended user's live session receives it.

## Ten-year scalability boundary

No source-code audit can guarantee ten years of heavy traffic. Patch 06 removes the major unbounded UI and hot-query patterns and adds indexes for sales, restock, notifications and audit history. For production scale, use PostgreSQL, Redis-backed rate limiting/socket message queue, automated backups, monitoring and a load test before increasing worker count.
