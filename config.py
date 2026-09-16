import os

# ... existing config ...

# USD → SLL conversion rate. Set USD_TO_SLL in .env to override.
USD_TO_SLL = float(os.environ.get('USD_TO_SLL', 23000))


from dotenv import load_dotenv

load_dotenv()

class Config:
    """Base configuration"""
    
    # Application
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    APP_NAME = os.environ.get('APP_NAME', 'MikeShop & Clothings')
    APP_VERSION = '2.0.0'
    
    # Database - PostgreSQL for production
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL',
        'postgresql://user:password@localhost:5432/mikeShop'
    ).replace('postgres://', 'postgresql://')  # Render fix
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 10,
        'pool_recycle': 300,
        'pool_pre_ping': True,
    }
    
    # Security
    SESSION_COOKIE_SECURE = os.environ.get('FLASK_ENV') == 'production'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    WTF_CSRF_ENABLED = True
    WTF_CSRF_SECRET_KEY = os.environ.get('CSRF_SECRET_KEY', SECRET_KEY)
    
    # Rate Limiting (with Redis)
    RATELIMIT_ENABLED = os.environ.get('RATELIMIT_ENABLED', 'True') == 'True'
    RATELIMIT_STORAGE_URI = os.environ.get('REDIS_URL', 'memory://')
    RATELIMIT_DEFAULT = "200 per day;50 per hour"
    RATELIMIT_STRATEGY = 'fixed-window'
    
    # Email
    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'True') == 'True'
    MAIL_USE_SSL = os.environ.get('MAIL_USE_SSL', 'False') == 'True'
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', 'noreply@mikeShop.com')
    
    # Store Settings
    STORE_NAME = os.environ.get('STORE_NAME', 'MikeShop & Clothings')
    STORE_CURRENCY = os.environ.get('STORE_CURRENCY', 'USD')
    USD_TO_SLL = int(os.environ.get('USD_TO_SLL', 23000))
    
    # Online Store
    ONLINE_STORE_ENABLED = os.environ.get('ONLINE_STORE_ENABLED', 'True') == 'True'
    STORE_URL = os.environ.get('STORE_URL', 'https://mikeShop.com')
    
    # Backup
    BACKUP_ENABLED = os.environ.get('BACKUP_ENABLED', 'True') == 'True'
    BACKUP_PATH = os.environ.get('BACKUP_PATH', '/backups')
    BACKUP_RETENTION_DAYS = int(os.environ.get('BACKUP_RETENTION_DAYS', 30))
    
    # CORS (for mobile app)
    CORS_ORIGINS = os.environ.get('CORS_ORIGINS', '*').split(',')
    
    # API
    API_KEY = os.environ.get('API_KEY')
    
class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True
    FLASK_ENV = 'development'
    SESSION_COOKIE_SECURE = False

class ProductionConfig(Config):
    """Production configuration"""
    DEBUG = False
    FLASK_ENV = 'production'
    SESSION_COOKIE_SECURE = True
    
    # Force HTTPS in production
    PREFERRED_URL_SCHEME = 'https'

# Select config based on environment
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}