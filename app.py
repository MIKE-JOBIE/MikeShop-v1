import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, abort
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import date, datetime, timedelta
from functools import wraps
from sqlalchemy import func
from flask_socketio import SocketIO, emit
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
    cors_allowed_origins=app.config.get('CORS_ORIGINS', '*'),
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
        db.session.rollback()
        return {'status': 'degraded', 'error': str(e)}, 200

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

# ==================== CONSTANTS ====================

USD_TO_SLL = app.config.get('USD_TO_SLL', 23000)
LOW_STOCK_THRESHOLD = 10
PER_PAGE = 15

OWNER_USERNAME = "MichaelJobieMusa"
OWNER_PASSWORD = os.environ.get("OWNER_PASSWORD")

# ==================== HELPERS ====================

def usd_to_sll(value):
    """Convert USD to Sierra Leonean Leone using the configured rate."""
    return round((value or 0) * USD_TO_SLL, 2)

app.jinja_env.globals.update(usd_to_sll=usd_to_sll)

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
    socketio.run(
        app,
        debug=env == 'development',
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 5000))
    )