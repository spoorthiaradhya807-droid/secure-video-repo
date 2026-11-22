import os

os.environ.update(AWS_ACCESS_KEY_ID="test", AWS_SECRET_ACCESS_KEY="test",
                  AWS_DEFAULT_REGION="ap-south-1")

import boto3
import pytest
from moto import mock_aws

from app import create_app
from app import s3_utils
from app.config import Config
from app.extensions import db
from app.models import User

BUCKET = "test-bucket"
PASSWORD = "CorrectHorse42"
FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


class AppTestConfig(Config):
    TESTING = True
    SECRET_KEY = "test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    S3_BUCKET = BUCKET
    MAX_UPLOAD_BYTES = 1024 * 1024
    SES_SENDER = ""
    APP_BASE_URL = "https://videos.example.com"


@pytest.fixture
def s3():
    with mock_aws():
        c = boto3.client("s3", region_name="ap-south-1")
        c.create_bucket(Bucket=BUCKET,
                        CreateBucketConfiguration={"LocationConstraint": "ap-south-1"})
        yield c


@pytest.fixture
def app(s3, monkeypatch):
    monkeypatch.setattr(s3_utils, "client_for", lambda role, username: s3)
    app = create_app(AppTestConfig)
    with app.app_context():
        db.create_all()
        for name, role, email in [("ins1", "instructor", "ins1@example.com"),
                                  ("ins2", "instructor", "ins2@example.com"),
                                  ("view1", "viewer", "view1@example.com")]:
            u = User(username=name, role=role, email=email)
            u.set_password(PASSWORD)
            db.session.add(u)
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def login(client, username, password=PASSWORD):
    return client.post("/login", data={"username": username, "password": password})
