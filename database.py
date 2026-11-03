import os
import logging
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

logger = logging.getLogger(__name__)

db = SQLAlchemy()
migrate = Migrate()

def init_db(app):
    """Initialize database with app"""
    db.init_app(app)
    migrate.init_app(app, db)

    # Create tables if they don't exist
    with app.app_context():
        db.create_all()
        logger.info("Database tables created/verified")


# Connection pool configuration for better performance
# Add these to app.config in app.py:
# app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
#     'pool_size': 10,
#     'pool_recycle': 280,  # Recycle connections after 280 seconds
#     'pool_pre_ping': True,  # Enable connection health checks
#     'pool_timeout': 30
# }