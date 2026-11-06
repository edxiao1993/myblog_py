import os
import logging
from datetime import timedelta
from flask import Flask, redirect, request, session
from flask_login import LoginManager, login_required, current_user
from dotenv import load_dotenv

from database import db, init_db
from models.user import User
from routes import register_blueprints
from utils.logger import setup_logging
from utils.i18n import init_i18n, translate
from services.pi_memory_scheduler import start_memory_scheduler

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__)

    # Initialize logging first
    setup_logging(app)
    logger.info("Creating Flask application")

    # Configuration
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key')
    app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY', 'dev-jwt-secret-key')
    database_url = os.environ.get('DATABASE_URL', '').strip()
    if not database_url:
        raise ValueError('Set DATABASE_URL in the environment or project .env file.')
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['JSON_AS_ASCII'] = False  # Disable ASCII encoding for JSON
    app.config['PERMANENT_SESSION_LIFETIME'] = 36 * 60 * 60  # 36 hours in seconds
    app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV', 'development') == 'production'
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    
    # Connection pool configuration for better performance
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_size': 10,
        'pool_recycle': 280,  # Recycle connections after 280 seconds
        'pool_pre_ping': True,  # Enable connection health checks
        'pool_timeout': 30
    }

    logger.info("App configuration loaded (env=%s)", os.environ.get('FLASK_ENV', 'development'))

    # Pi inherits the service account's provider credentials and installed extensions.
    app.config.update(
        PI_EXECUTABLE=os.environ.get('PI_EXECUTABLE', 'pi'),
        PI_DATA_DIR=os.path.abspath(os.environ.get('PI_DATA_DIR', 'data/pi')),
        PI_WORKSPACE=os.path.abspath(os.environ.get('PI_WORKSPACE', 'data/pi-workspace')),
        PI_SYSTEM_PROMPT_FILE=os.path.abspath(os.environ.get(
            'PI_SYSTEM_PROMPT_FILE', 'config/pi-system-prompt.txt')),
        PI_PROVIDER=os.environ.get('PI_PROVIDER', ''),
        PI_MODEL=os.environ.get('PI_MODEL', ''),
        PI_TIMEOUT=int(os.environ.get('PI_TIMEOUT', '600')),
        PI_MEMORY_MODEL=os.environ.get('PI_MEMORY_MODEL', ''),
        PI_MEMORY_ENABLED=os.environ.get('PI_MEMORY_ENABLED', 'true').lower() in ('true', '1'),
        PI_MEMORY_HOUR=int(os.environ.get('PI_MEMORY_HOUR', '3')),
        PI_MAX_UPLOAD_BYTES=int(os.environ.get('PI_MAX_UPLOAD_BYTES', str(200 * 1024 * 1024))),
    )
    os.makedirs(app.config['PI_WORKSPACE'], mode=0o700, exist_ok=True)

    init_i18n(app)

    # Initialize database
    init_db(app)
    logger.info("Database initialised")

    # Initialize Flask-Login
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.localize_callback = translate

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # Force HTTPS in production
    @app.before_request
    def force_https():
        if not request.is_secure and os.environ.get('FLASK_ENV', 'development') == 'production':
            return redirect(request.url.replace('http://', 'https://'))

    # Update session on each request to extend timeout
    @app.before_request
    def update_session():
        if current_user.is_authenticated:
            session.permanent = True
            app.permanent_session_lifetime = timedelta(hours=36)
            session.modified = True

    # WSGI fallback: start once per worker after fork, never in a preload master.
    @app.before_request
    def ensure_memory_scheduler():
        start_memory_scheduler(app)

    # Register blueprints
    register_blueprints(app)
    logger.info("All blueprints registered")

    return app

app = create_app()

if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    if not debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        start_memory_scheduler(app)
    app.run(debug=debug)
