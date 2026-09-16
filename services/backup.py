# services/backup.py
import os
import subprocess
from datetime import datetime, timedelta
from app import app
import shutil
import glob

def backup_database():
    """Backup PostgreSQL database using pg_dump"""
    if not app.config.get('BACKUP_ENABLED', True):
        return
    
    database_url = app.config['SQLALCHEMY_DATABASE_URI']
    backup_path = app.config.get('BACKUP_PATH', '/backups')
    
    # Parse database URL
    import re
    match = re.match(r'postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)', database_url)
    if not match:
        return
    
    user, password, host, port, dbname = match.groups()
    
    # Create backup directory
    os.makedirs(backup_path, exist_ok=True)
    
    # Create backup file
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_file = os.path.join(backup_path, f'backup_{timestamp}.sql')
    
    # Use pg_dump
    env = os.environ.copy()
    env['PGPASSWORD'] = password
    
    cmd = [
        'pg_dump',
        '-h', host,
        '-p', port,
        '-U', user,
        '-d', dbname,
        '-F', 'c',  # Custom format
        '-f', backup_file
    ]
    
    try:
        subprocess.run(cmd, env=env, check=True, capture_output=True)
        # Cleanup old backups
        cleanup_old_backups(backup_path)
        return backup_file
    except subprocess.CalledProcessError as e:
        app.logger.error(f"Backup failed: {e.stderr}")
        return None

def cleanup_old_backups(backup_path):
    """Remove backups older than retention period"""
    retention_days = app.config.get('BACKUP_RETENTION_DAYS', 30)
    cutoff = datetime.now() - timedelta(days=retention_days)
    
    pattern = os.path.join(backup_path, 'backup_*.sql')
    for filepath in glob.glob(pattern):
        mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
        if mtime < cutoff:
            try:
                os.remove(filepath)
            except Exception:
                pass

def restore_database(backup_file):
    """Restore database from backup"""
    if not os.path.exists(backup_file):
        return False
    
    database_url = app.config['SQLALCHEMY_DATABASE_URI']
    
    import re
    match = re.match(r'postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)', database_url)
    if not match:
        return False
    
    user, password, host, port, dbname = match.groups()
    
    env = os.environ.copy()
    env['PGPASSWORD'] = password
    
    cmd = [
        'pg_restore',
        '-h', host,
        '-p', port,
        '-U', user,
        '-d', dbname,
        '-c',  # Clean (drop) before restore
        backup_file
    ]
    
    try:
        subprocess.run(cmd, env=env, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        app.logger.error(f"Restore failed: {e.stderr}")
        return False