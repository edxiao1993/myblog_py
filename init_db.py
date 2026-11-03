#!/usr/bin/env python3
"""
Database initialization script
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from database import db
from models.post import Post  # noqa: F401 -- register all tables
from models.refresh_token import RefreshToken  # noqa: F401
from models.secret_moment import SecretMoment  # noqa: F401
from models.user import User

def init_database():
    # Do not import app: its module-level app starts database initialization.
    load_dotenv(Path(__file__).resolve().with_name('.env'))
    database_url = os.environ.get('DATABASE_URL', '').strip()
    if not database_url:
        raise ValueError('Set DATABASE_URL in the environment or project .env file.')

    url = make_url(database_url)
    if url.get_backend_name() in ('mysql', 'mariadb'):
        if not url.database:
            raise ValueError('DATABASE_URL must include the existing database name.')
        if 'charset' not in url.query:
            url = url.update_query_dict({'charset': 'utf8mb4'})

    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI=url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={'pool_pre_ping': True},
    )
    db.init_app(app)

    with app.app_context():
        try:
            db.create_all()
            print('Database tables created/verified successfully!')
            username = os.environ.get('DEFAULT_ADMIN_USERNAME', 'admin')
            email = os.environ.get('DEFAULT_ADMIN_EMAIL', 'admin@example.com')
            user = User.query.filter_by(username=username).first()
            if user:
                print("Initial user already exists; password unchanged.")
            else:
                default_password = os.environ.get('DEFAULT_ADMIN_PASSWORD')
                if not default_password:
                    print('Set DEFAULT_ADMIN_PASSWORD to create the initial user.')
                else:
                    user = User(username=username, email=email)
                    user.set_password(default_password)
                    db.session.add(user)
                    db.session.commit()
                    print("Initial user created successfully!")
        except Exception:
            db.session.rollback()
            raise
        finally:
            db.session.remove()
            db.engine.dispose()

if __name__ == "__main__":
    try:
        init_database()
    except (SQLAlchemyError, ValueError) as exc:
        # Do not print connection URLs, passwords, or SQL parameters.
        code = getattr(getattr(exc, 'orig', None), 'args', (None,))[0]
        hints = {
            1045: 'Access denied: check database credentials and host grants.',
            1044: 'Access denied to the database: check user privileges.',
            1049: 'Database not found: create the database named in DATABASE_URL first.',
            2002: 'Cannot connect: check the database host, port, and server status.',
            2003: 'Cannot connect: check the database host, port, and firewall.',
            1406: 'Column too short: existing tables need a schema migration; create_all does not alter columns.',
        }
        detail = str(exc) if isinstance(exc, ValueError) else hints.get(
            code, 'Check DATABASE_URL and database permissions/schema.'
        )
        print(f'Database initialization failed: {detail}', file=sys.stderr)
        sys.exit(1)
