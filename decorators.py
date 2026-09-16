from functools import wraps
from flask import session, request, jsonify, abort, redirect, url_for, current_app
import hmac
from datetime import datetime, timedelta

def login_required(f):
    """Decorator to require login for routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def role_required(*roles):
    """Decorator to require specific roles"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            if session.get('role') not in roles:
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def api_key_required(f):
    """Decorator to require API key for API endpoints"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        expected_key = current_app.config.get('API_KEY')
        
        if not api_key or not expected_key or not hmac.compare_digest(api_key, expected_key):
            return jsonify({'error': 'Invalid or missing API key'}), 401
        return f(*args, **kwargs)
    return decorated_function

def log_activity(action):
    """Decorator to log user activity"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            result = f(*args, **kwargs)
            # Log the activity
            from models.core import AuditLog
            from app import db
            
            try:
                log_entry = AuditLog(
                    user=session.get('username', 'system'),
                    action=action
                )
                db.session.add(log_entry)
                db.session.commit()
            except Exception:
                db.session.rollback()
            
            return result
        return decorated_function
    return decorator