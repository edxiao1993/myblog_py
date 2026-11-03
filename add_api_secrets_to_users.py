"""
Migration script to add API secrets to existing users.
Run this after updating the User model with the api_secret field.

Usage:
    python add_api_secrets_to_users.py
"""

from app import create_app
from database import db
from models.user import User


def add_api_secrets():
    """Generate API secrets for all users that don't have one"""
    app = create_app()
    
    with app.app_context():
        users = db.session.execute(db.select(User)).scalars().all()
        
        updated_count = 0
        for user in users:
            if not user.api_secret:
                user.generate_api_secret()
                updated_count += 1
                print(f"Generated API secret for user: {user.username}")
        
        if updated_count > 0:
            db.session.commit()
            print(f"\n✓ Updated {updated_count} user(s) with new API secrets")
        else:
            print("\n✓ All users already have API secrets")


if __name__ == '__main__':
    add_api_secrets()
