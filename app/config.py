import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///videorepo.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
    S3_BUCKET = os.environ.get("S3_BUCKET", "")
    INSTRUCTOR_ROLE_ARN = os.environ.get("INSTRUCTOR_ROLE_ARN", "")
    VIEWER_ROLE_ARN = os.environ.get("VIEWER_ROLE_ARN", "")

    PRESIGN_SECONDS = int(os.environ.get("PRESIGN_SECONDS", "300"))
    # Large uploads: browser sends the file to S3 in chunks (multipart upload).
    # S3 allows up to 5 TiB per object; this is the cap YOU choose (cost / abuse control).
    MAX_UPLOAD_BYTES = int(float(os.environ.get("MAX_UPLOAD_GB", "10")) * 1024 ** 3)
    PART_SIZE_BYTES = int(os.environ.get("PART_SIZE_MB", "8")) * 1024 * 1024
    ALLOWED_TYPES = {"mp4": "video/mp4", "webm": "video/webm"}

    # Password reset e-mail (Amazon SES). Leave SES_SENDER empty for dev mode:
    # the reset link is then printed in the server terminal instead of e-mailed.
    SES_SENDER = os.environ.get("SES_SENDER", "")
    SES_REGION = os.environ.get("SES_REGION", os.environ.get("AWS_REGION", "ap-south-1"))
    APP_BASE_URL = os.environ.get("APP_BASE_URL", "").rstrip("/")
    RESET_TOKEN_SECONDS = 1800

    BEHIND_PROXY = os.environ.get("BEHIND_PROXY", "0") == "1"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=2)
