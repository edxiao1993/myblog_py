from datetime import datetime
from database import db
import uuid


class SecretMoment(db.Model):
    id = db.Column(db.String(32), primary_key=True, default=lambda: str(uuid.uuid4().hex))
    # Legacy moments have no recorded author and remain hidden until assigned.
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
    moment = db.Column(db.Text, nullable=False)
    create_time = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def __repr__(self):
        return f'<SecretMoment {self.id}>'
