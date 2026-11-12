from datetime import datetime
from database import db


class PushDevice(db.Model):
    installation_id = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    device_token = db.Column(db.String(512), nullable=False)
    environment = db.Column(db.String(16), nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
