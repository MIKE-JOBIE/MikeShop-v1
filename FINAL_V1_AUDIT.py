"""
MikeShop V1 Freeze Audit
Read-only audit. No business data changes.
Checks: database integrity, canonical duplicates, migrations, secrets hygiene,
environment/social configuration, security configuration, cache policy,
notification synchronization, accessibility source structure, and scalability hazards.
"""
from pathlib import Path
import os, re, sqlite3, ast

ROOT = Path(__file__).resolve().parent
DB = ROOT / "instance" / "sneakers.db"
errors, warnings = [], []

def ok(label): print(f"[OK] {label}")
def fail(label): errors.append(label); print(f"[FAIL] {label}")
def warn(label): warnings.append(label); print(f"[WARN] {label}")

# ---------- Files / configuration ----------
required = [
    "app.py","config.py","database.py","decorators.py","requirements.txt",
    "routes/admin.py","routes/api.py","routes/auth.py","routes/customers.py",
    "routes/reports.py","models/core.py","models/customer.py","models/store.py",
    "static/script.js","static/style.css","templates/layouts/admin_layout.html",
    "templates/layouts/base.html","templates/admin/dashboard.html",
    "templates/admin/staff_list.html","migrations/versions/patch6_identity_keys_and_pagination.py"
]
for rel in required:
    (ok(f"required file: {rel}") if (ROOT/rel).exists() else fail(f"missing file: {rel}"))

req = (ROOT/"requirements.txt").read_text(encoding="utf-8")
if "reportlab==4.2.5\nitsdangerous>=2.1,<3" in req:
    ok("requirements.txt line separation is valid")
else:
    fail("requirements.txt has a malformed/concatenated dependency line")

# ---------- Database integrity ----------
if DB.exists():
    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys=ON")
    if con.execute("PRAGMA integrity_check").fetchone()[0] == "ok":
        ok("SQLite integrity_check")
    else: fail("SQLite integrity_check")
    fk = con.execute("PRAGMA foreign_key_check").fetchall()
    if not fk: ok("foreign-key integrity")
    else: fail(f"foreign-key violations: {len(fk)}")

    def dup(sql):
        return con.execute(sql).fetchall()
    checks = [
        ("shoe identity", "SELECT identity_key, COUNT(*) FROM shoe GROUP BY identity_key HAVING COUNT(*)>1"),
        ("shoe variant", "SELECT shoe_id, variant_key, COUNT(*) FROM shoe_size GROUP BY shoe_id, variant_key HAVING COUNT(*)>1"),
        ("product identity", "SELECT identity_key, COUNT(*) FROM product GROUP BY identity_key HAVING COUNT(*)>1"),
        ("product variant", "SELECT product_id, variant_key, COUNT(*) FROM product_variant GROUP BY product_id, variant_key HAVING COUNT(*)>1"),
    ]
    for label, sql in checks:
        rows=dup(sql)
        if not rows: ok(f"canonical duplicate check: {label}")
        else: fail(f"canonical duplicates: {label} -> {rows}")
    version = con.execute("SELECT version_num FROM alembic_version").fetchone()
    if version and version[0] == "patch6_identity_keys":
        ok("database migration head: patch6_identity_keys")
    else:
        fail(f"unexpected migration head: {version[0] if version else None}")
    con.close()
else:
    warn("instance/sneakers.db not present; database checks skipped")

# ---------- .env / secrets ----------
env_path = ROOT/".env"
if env_path.exists():
    env = env_path.read_text(encoding="utf-8", errors="ignore")
    if re.search(r"(?m)^\s*(Facebook|Instagram|LinkedIn|WhatsApp)\s*:", env):
        fail(".env contains human-readable social labels instead of SOCIAL_* variables")
    else:
        ok(".env social variable naming")
    if "OWNER_PASSWORD=" in env:
        warn(".env contains a secret; it must remain untracked and must not be packaged for sharing")
else:
    ok("private .env is absent from distributable project (expected)")
envex = (ROOT/".env.example").read_text(encoding="utf-8")
for key in ["SOCIAL_FACEBOOK_URL","SOCIAL_INSTAGRAM_URL","SOCIAL_LINKEDIN_URL","SOCIAL_WHATSAPP_URL"]:
    if re.search(rf"(?m)^{key}=", envex): ok(f".env.example contains {key}")
    else: fail(f".env.example missing {key}")

# ---------- Security / confidentiality ----------
config = (ROOT/"config.py").read_text(encoding="utf-8")
app = (ROOT/"app.py").read_text(encoding="utf-8")
if "SESSION_COOKIE_HTTPONLY = True" in config: ok("HttpOnly session cookie")
else: fail("HttpOnly session cookie setting")
if "SESSION_COOKIE_SAMESITE = 'Lax'" in config: ok("SameSite=Lax session cookie")
else: fail("SameSite cookie policy")
if "WTF_CSRF_ENABLED = True" in config: ok("CSRF enabled")
else: fail("CSRF not enabled")
for header in ["X-Content-Type-Options","X-Frame-Options","Referrer-Policy","Content-Security-Policy"]:
    if header in app: ok(f"security header configured: {header}")
    else: fail(f"security header missing: {header}")
if "SECRET_KEY" in config and "production" in config: ok("production secret requirement present")
else: fail("production secret requirement not found")
if "role_required" in (ROOT/"routes/admin.py").read_text(encoding="utf-8"): ok("RBAC decorators present in admin routes")
else: fail("RBAC decorators not found")

# Never allow private env/password values into normal source files.
source_files = [p for p in ROOT.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.name not in {".env","sneakers.db","FINAL_V1_AUDIT.py"}]
for p in source_files:
    txt=p.read_text(encoding="utf-8",errors="ignore")
    # Only flag literal values; environment-variable reads are safe.
    if re.search(r"(?i)OWNER_PASSWORD\s*=\s*['\"][^'\"]+['\"]", txt):
        fail(f"possible hard-coded owner password in {p.relative_to(ROOT)}")

# ---------- Immediate reflection ----------
js=(ROOT/"static/script.js").read_text(encoding="utf-8")
if 'cache: "no-store"' in js and "setInterval(() => syncNotifications(false), 3000)" in js:
    ok("client notification no-cache + 3-second reconciliation")
else: fail("notification synchronization fallback incomplete")
if "/notifications-feed" in (ROOT/"routes/admin.py").read_text(encoding="utf-8"):
    ok("server notification feed")
else: fail("server notification feed missing")
if "Cache-Control" in app and "no-store" in app:
    ok("authenticated HTML/JSON no-store policy")
else: fail("authenticated no-store policy missing")
if "_emit_notification(user_notification)" in (ROOT/"routes/admin.py").read_text(encoding="utf-8"):
    ok("staff creation emits notification after commit")
else: fail("staff creation notification emission missing")

# ---------- Accessibility source checks ----------
templates=list((ROOT/"templates").rglob("*.html"))
label_inputs=0
for p in templates:
    txt=p.read_text(encoding="utf-8",errors="ignore")
    for m in re.finditer(r'<input\b[^>]*\bid=["\']([^"\']+)["\']',txt,re.I):
        ident=m.group(1)
        if re.search(rf'<label\b[^>]*\bfor=["\']{re.escape(ident)}["\']',txt,re.I):
            label_inputs += 1
    for m in re.finditer(r'<img\b([^>]*)>',txt,re.I):
        if not re.search(r'\balt\s*=',m.group(1),re.I):
            fail(f"image without alt attribute: {p.relative_to(ROOT)}")
if "lang=\"en\"" in (ROOT/"templates/layouts/base.html").read_text(encoding="utf-8"):
    ok("document language declared")
else: fail("document language missing")
if label_inputs >= 5: ok(f"form-label associations found: {label_inputs}")
else: warn(f"only {label_inputs} explicit label associations found; manual WCAG review still required")
if "aria-label" in (ROOT/"templates/layouts/admin_layout.html").read_text(encoding="utf-8"):
    ok("accessible footer/navigation labels present")
else: warn("limited ARIA labelling detected")

# ---------- Scalability checks ----------
admin=(ROOT/"routes/admin.py").read_text(encoding="utf-8")
if ".paginate(" in admin: ok("pagination used on major list route(s)")
else: warn("pagination not detected in admin routes")
if "with_for_update()" in admin: ok("row-level locking used in inventory/sales paths")
else: warn("row-level locking not detected")
if "REDIS_URL" in config and "message_queue" in app: ok("Redis hooks available for rate limiting/socket scaling")
else: warn("Redis scaling hooks not detected")
if "sqlite:///sneakers.db" in config: ok("SQLite fallback retained for local development")
if "postgresql" in config: ok("PostgreSQL compatibility retained")
else: fail("PostgreSQL compatibility not detected")

print("\n=== FREEZE AUDIT RESULT ===")
print(f"Errors: {len(errors)}")
print(f"Warnings: {len(warnings)}")
if errors:
    print("NOT READY TO FREEZE")
    raise SystemExit(1)
print("STATIC/DB AUDIT PASSED")
if warnings:
    print("Warnings require awareness/manual review; they are not automatic freeze blockers.")
