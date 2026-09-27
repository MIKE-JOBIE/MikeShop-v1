"""
MikeShop V1 runtime smoke test.
Run this inside the project's Python environment after installing requirements.
Read-only: does not create/update/delete business records.
"""
import os
os.environ.setdefault("FLASK_ENV","development")

from app import app, db
from models.core import User, Role

with app.test_client() as client:
    with app.app_context():
        owner = User.query.filter_by(username="MichaelJobieMusa").first()
        assert owner is not None, "Owner account missing"
        assert owner.role and owner.role.name == "owner", "Owner role mismatch"
        owner_id = owner.id

    with client.session_transaction() as sess:
        sess["user_id"] = owner_id
        sess["username"] = "MichaelJobieMusa"
        sess["role"] = "owner"

    checks = [
        ("/healthz", 200),
        ("/dashboard", 200),
        ("/staff_list", 200),
        ("/notifications-feed", 200),
        ("/sales_history", 200),
        ("/customers", 200),
        ("/reports", 200),
    ]
    for path, expected in checks:
        r=client.get(path, headers={"Cache-Control":"no-cache"})
        assert r.status_code == expected, f"{path}: expected {expected}, got {r.status_code}"
        if path != "/healthz":
            cc=(r.headers.get("Cache-Control") or "").lower()
            assert "no-store" in cc, f"{path}: missing no-store cache policy"

    r=client.get("/notifications-feed")
    data=r.get_json()
    assert isinstance(data, dict) and isinstance(data.get("notifications"), list), "invalid notification feed"
    assert isinstance(data.get("unread_count"), int), "invalid unread_count"

    # Security headers
    r=client.get("/dashboard")
    for header in ["X-Content-Type-Options","X-Frame-Options","Referrer-Policy","Content-Security-Policy"]:
        assert r.headers.get(header), f"missing security header: {header}"

print("[RESULT] RUNTIME SMOKE TEST PASSED")
