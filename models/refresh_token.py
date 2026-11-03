from datetime import datetime, timezone, timedelta
from database import db


def _utcnow():
    """UTC now as naive datetime (for MySQL compatibility)"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class RefreshToken(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(255), unique=True, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    revoked = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=_utcnow)

    user = db.relationship('User', backref=db.backref('refresh_tokens', lazy='dynamic'))

    def is_expired(self):
        return _utcnow() > self.expires_at

    def is_valid(self):
        return not self.revoked and not self.is_expired()

    def __repr__(self):
        return f'<RefreshToken user_id={self.user_id} revoked={self.revoked}>'
