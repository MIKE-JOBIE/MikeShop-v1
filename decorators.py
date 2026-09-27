from functools import wraps
from flask import session, request, jsonify, abort, redirect, url_for, current_app, g
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
        bearer = request.headers.get('Authorization', '')

        # Server-to-server clients may still use the explicit API key.
        if api_key and expected_key and hmac.compare_digest(api_key, expected_key):
            # A shared server-to-server key authenticates the client, but it
            # does not identify an employee. Treat it as non-owner for
            # confidentiality-sensitive responses.
            g.api_user = None
            g.api_auth_method = 'api_key'
            return f(*args, **kwargs)

        # Mobile/web clients should use the short-lived login token instead
        # of a shared credential.
        if bearer.startswith('Bearer '):
            from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
            from models.core import User
            token = bearer[7:].strip()
            try:
                data = URLSafeTimedSerializer(current_app.config['SECRET_KEY']).loads(token, max_age=43200)
                user = User.query.get(int(data['user_id']))
                if user and user.is_active:
                    g.api_user = user
                    g.api_auth_method = 'bearer'
                    return f(*args, **kwargs)
            except (BadSignature, SignatureExpired, ValueError, KeyError, TypeError):
                pass

        return jsonify({'error': 'Invalid or missing API credentials'}), 401
        
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