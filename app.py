
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, abort
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import date, datetime, timedelta
from functools import wraps
from sqlalchemy import func
from flask_socketio import SocketIO, emit, join_room
from flask import jsonify
import csv, io, os
from flask_migrate import Migrate
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Import config
from config import config
from decorators import login_required, role_required

# ==================== APP SETUP ====================

app = Flask(__name__)

# Load config based on environment
env = os.environ.get('FLASK_ENV', 'development')
app.config.from_object(config[env])
app.config.setdefault('MAX_CONTENT_LENGTH', 2 * 1024 * 1024)

# ---- CSRF protection (Flask-WTF) ----
from flask_wtf.csrf import CSRFProtect, CSRFError
csrf = CSRFProtect(app)

# Import database extensions
from database import db, migrate, socketio, limiter

# Initialize extensions with app
db.init_app(app)
migrate.init_app(app, db)
socketio.init_app(
    app,
    cors_allowed_origins=app.config.get('CORS_ORIGINS') or [],
    message_queue=os.environ.get('REDIS_URL')
)
limiter.init_app(app)


@app.route('/healthz')
@limiter.exempt
def healthz():
    from sqlalchemy import text
    try:
        db.session.execute(text('SELECT 1'))
        db.session.commit()
        return {'status': 'ok', 'db': 'ok'}, 200
    except Exception as e:
        app.logger.error(f"healthz DB probe failed: {type(e).__name__}: {e}")
        db.session.rollback()
        return {'status': 'degraded', 'db': 'error'}, 503


    
   # ==================== CONSTANTS ====================

USD_TO_SLL = app.config.get('USD_TO_SLL', 23000)
LOW_STOCK_THRESHOLD = 10
PER_PAGE = 15

OWNER_USERNAME = "MichaelJobieMusa"
OWNER_PASSWORD = os.environ.get("OWNER_PASSWORD")

# ==================== HELPERS ====================
# IMPORTANT: usd_to_sll + depreciated_value MUST be defined before the
# route imports below. routes/reports.py and routes/api.py do
# `from app import app, csrf, depreciated_value` at import time. If these
# functions live below the route imports, Python hits a circular import.

def usd_to_sll(value):
    """Convert USD to Sierra Leonean Leone using the configured rate."""
    return round((value or 0) * USD_TO_SLL, 2)

def depreciated_value(original_cost_value, deactivated_at):
    """Straight-line depreciation: an item's cost value declines to $0 over
    DEPRECIATION_PERIOD_DAYS after it was marked inactive. Returns the
    original value unchanged if it's still active (deactivated_at is None),
    and 0 once the depreciation period has fully elapsed."""
    if not deactivated_at or original_cost_value <= 0:
        return original_cost_value
    period_days = app.config.get('DEPRECIATION_PERIOD_DAYS', 365)
    days_elapsed = (datetime.utcnow() - deactivated_at).total_seconds() / 86400
    remaining_fraction = max(0.0, 1 - (days_elapsed / period_days))
    return round(original_cost_value * remaining_fraction, 2)

app.jinja_env.globals.update(usd_to_sll=usd_to_sll)

# ==================== IMPORT MODELS ====================
from models.core import Role, User, Shoe, Sale, Expense, Product, AuditLog, Notification, Restock
from models.customer import Customer, CustomerPurchase
from models.store import StoreOrder, StoreOrderItem

# ==================== IMPORT ROUTES ====================
from routes.auth import *
from routes.admin import *
from routes.customers import *
from routes.reports import *
from routes.api import *

# ==================== HELPERS (continued) ====================

def log(action, commit=False):
    """Add an audit-log entry."""
    try:
        db.session.add(
            AuditLog(
                user=session.get("username", "system"),
                action=action
            )
        )
        if commit:
            db.session.commit()
    except Exception:
        db.session.rollback()
        raise



@app.after_request
def add_security_headers(response):
    """Apply baseline browser security headers to every response."""
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
    # The UI currently contains a small amount of inline CSS/JS and loads
    # trusted CDN assets, so the CSP intentionally allows those sources.
    response.headers.setdefault(
        'Content-Security-Policy',
        "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; "
        "object-src 'none'; img-src 'self' data: https:; font-src 'self' data: https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdn.socket.io https://cdnjs.cloudflare.com; "
        "connect-src 'self' ws: wss:;"
    )
    # Authenticated HTML/JSON is user-specific and must not be served from a
    # stale browser/proxy cache after another action changes the database.
    # Static assets are intentionally left cacheable.
    if session.get('user_id') and (
        response.content_type.startswith('text/html') or
        response.content_type.startswith('application/json')
    ):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        response.headers['Vary'] = 'Cookie'

    if app.config.get('FLASK_ENV') == 'production' and request.is_secure:
        response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
    return response


@socketio.on('connect')
def socket_connect():
    user_id = session.get('user_id')
    if user_id:
        join_room(f'user_{user_id}')

# ==================== CONTEXT PROCESSOR ====================

@app.context_processor
def utility_processor():
    """Make commonly used functions available in templates"""
    from models.core import Notification
    
    unread_alerts = 0
    notifications = []
    
    if 'user_id' in session:
        unread_alerts = Notification.query.filter(
            ((Notification.user_id == session['user_id']) | (Notification.user_id == None)) &
            (Notification.is_read == False)
        ).count()
        
        notifications = Notification.query.filter(
            (Notification.user_id == session['user_id']) | (Notification.user_id == None)
        ).order_by(Notification.created_at.desc()).limit(10).all()
    
    return {
        'now': datetime.utcnow,
        'usd_to_sll': usd_to_sll,
        'get_loyalty_tier': lambda customer: customer.get_loyalty_tier() if customer else 'New',
        'unread_alerts': unread_alerts,
        'notifications': notifications
    }

# ==================== SEEDING ====================

DEFAULT_ROLES = ("owner", "admin", "staff")

def seed_roles():
    """Create the application's default roles if they don't exist."""
    for role_name in DEFAULT_ROLES:
        if not Role.query.filter_by(name=role_name).first():
            db.session.add(Role(name=role_name))
    db.session.commit()

def seed_owner():
    """Create the initial owner account if it does not already exist."""
    owner = User.query.filter_by(username=OWNER_USERNAME).first()
    if owner:
        return
    if not OWNER_PASSWORD:
        raise RuntimeError(
            "OWNER_PASSWORD environment variable is not configured. "
            "The initial owner account cannot be created."
        )
    if app.config.get('FLASK_ENV') == 'production' and len(OWNER_PASSWORD) < app.config.get('MIN_PASSWORD_LENGTH', 12):
        raise RuntimeError(f"OWNER_PASSWORD must be at least {app.config.get('MIN_PASSWORD_LENGTH', 12)} characters in production.")
    owner_role = Role.query.filter_by(name="owner").first()
    if not owner_role:
        raise RuntimeError("Owner role does not exist. Run seed_roles() first.")
    owner = User(
        username=OWNER_USERNAME,
        password_hash=generate_password_hash(OWNER_PASSWORD),
        role=owner_role
    )
    db.session.add(owner)
    db.session.commit()

def ensure_owner():
    """Ensure the default roles and owner account exist.
    Safe to call from multiple worker processes booting concurrently:
    if two workers race to insert the same row, the loser's commit fails
    with an IntegrityError, which we treat as "someone else already did
    it" rather than a real startup failure."""
    from sqlalchemy.exc import IntegrityError
    try:
        seed_roles()
        seed_owner()
    except IntegrityError:
        db.session.rollback()
    except Exception:
        db.session.rollback()
        raise


# ---- CSRF failure handler ----
AJAX_PATHS = (
    '/update_role', '/reset_password', '/delete_staff',
    '/mark_all_read', '/mark_notification_read',
)

@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    # AJAX / API paths → JSON 400
    if request.path.startswith('/api/') or \
       any(request.path.startswith(p) for p in AJAX_PATHS):
        return jsonify({
            'status': 'error',
            'message': 'CSRF token missing or invalid. Refresh and try again.'
        }), 400

    # Regular form → flash + redirect back
    flash("Security check failed. Please refresh the page and try again.")
    return redirect(request.referrer or url_for('dashboard'))

# ==================== INIT ====================

# Seed roles + owner account on import — this runs under gunicorn too
# (previously it only ran in the `if __name__ == "__main__"` dev block,
# which gunicorn never executes, so a fresh Render deploy had no roles
# and no owner account and nobody could log in).
# seed_roles()/seed_owner() both check for existing rows first, so this
# is safe to run once per worker process on boot.
with app.app_context():
    try:
        ensure_owner()
    except Exception as e:
        app.logger.error(f"Owner/role seeding failed on startup: {e}")

if __name__ == "__main__":
    # use_reloader=False is deliberate: eventlet.monkey_patch() (top of this
    # file, required for Socket.IO) conflicts with Werkzeug's auto-reloader,
    # which restarts the process via a fork/subprocess on every file save.
    # Under eventlet's patched os/threading/select, that restart can hang
    # or silently fail to pick up changes. debug=True is kept so you still
    # get Flask's error pages and tracebacks locally — you'll just need to
    # manually stop/restart (Ctrl+C, then re-run) after code changes.
    # Set MIKESHOP_USE_RELOADER=1 if you want to try the reloader anyway.
    socketio.run(
        app,
        debug=env == 'development',
        use_reloader=os.environ.get('MIKESHOP_USE_RELOADER') == '1',
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 5000))
    )