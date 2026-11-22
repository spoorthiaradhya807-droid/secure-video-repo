from datetime import datetime, timezone

from flask import request
from flask_login import UserMixin, current_user
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(30), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, index=True, nullable=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="viewer")  # viewer | instructor
    created_at = db.Column(db.DateTime, default=utcnow)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class Video(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(500), default="")
    s3_key = db.Column(db.String(200), unique=True, nullable=False)
    content_type = db.Column(db.String(50), nullable=False)
    size = db.Column(db.BigInteger, default=0)            # declared at init, verified at completion
    duration = db.Column(db.Integer, nullable=True)       # seconds (display only)
    upload_id = db.Column(db.String(256), nullable=True)  # S3 multipart upload id
    part_size = db.Column(db.Integer, nullable=True)
    parts = db.Column(db.Integer, nullable=True)
    uploader_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    status = db.Column(db.String(20), default="pending")  # pending | ready | rejected | cancelled
    created_at = db.Column(db.DateTime, default=utcnow)
    uploader = db.relationship("User")


class Event(db.Model):
    """Audit log: login_ok, login_fail, view, url_renew, upload, upload_rejected, denied,
    register, password_reset_requested, password_reset."""
    id = db.Column(db.Integer, primary_key=True)
    ts = db.Column(db.DateTime, default=utcnow, index=True)
    user_id = db.Column(db.Integer, nullable=True)
    username = db.Column(db.String(30), default="anonymous")
    event_type = db.Column(db.String(30), index=True)
    video_id = db.Column(db.Integer, nullable=True)
    ip = db.Column(db.String(64))
    detail = db.Column(db.String(250), default="")


def log_event(event_type, video_id=None, detail="", username=None):
    uid = None
    if current_user.is_authenticated:
        uid = current_user.id
        username = username or current_user.username
    db.session.add(Event(user_id=uid, username=username or "anonymous",
                         event_type=event_type, video_id=video_id,
                         ip=request.remote_addr, detail=detail[:250]))
    db.session.commit()
