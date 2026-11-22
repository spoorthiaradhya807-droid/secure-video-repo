"""
S3 access with *temporary, role-scoped* credentials.

Every request is signed by STS credentials of the user's IAM role (Instructor or
Viewer), never by long-lived keys. A pre-signed URL can never do more than the
role that signed it, so even a forged "upload" request from a Viewer is rejected by S3.

Large files use S3 multipart upload: the server only creates the upload and signs one
short-lived URL per chunk; the video bytes go browser -> S3 and never touch the VM.
"""
import re
import threading
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from flask import current_app

_cache = {}
_lock = threading.Lock()


def _session_name(username):
    return re.sub(r"[^\w+=,.@-]", "-", username)[:60] or "user"


def client_for(role, username):
    """S3 client using temporary creds of the Instructor/Viewer role (cached ~9 min)."""
    cfg = current_app.config
    arn = cfg["INSTRUCTOR_ROLE_ARN"] if role == "instructor" else cfg["VIEWER_ROLE_ARN"]
    key = (arn, username)
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and hit["exp"] - now > cfg["PRESIGN_SECONDS"] + 60:
            return hit["client"]
    creds = boto3.client("sts", region_name=cfg["AWS_REGION"]).assume_role(
        RoleArn=arn, RoleSessionName=_session_name(username), DurationSeconds=900
    )["Credentials"]
    client = boto3.client(
        "s3", region_name=cfg["AWS_REGION"],
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        config=Config(signature_version="s3v4"),
    )
    with _lock:
        _cache[key] = {"client": client, "exp": creds["Expiration"].timestamp()}
    return client


def presign_get(key, role, username, expires=None):
    cfg = current_app.config
    return client_for(role, username).generate_presigned_url(
        "get_object",
        Params={"Bucket": cfg["S3_BUCKET"], "Key": key},
        ExpiresIn=expires or cfg["PRESIGN_SECONDS"],
    )


# ---------------- multipart upload (instructors only) ----------------
def create_multipart(key, content_type, username):
    cfg = current_app.config
    r = client_for("instructor", username).create_multipart_upload(
        Bucket=cfg["S3_BUCKET"], Key=key, ContentType=content_type,
        ServerSideEncryption="AES256")
    return r["UploadId"]


def presign_part(key, upload_id, part_number, username, expires=None):
    cfg = current_app.config
    return client_for("instructor", username).generate_presigned_url(
        "upload_part",
        Params={"Bucket": cfg["S3_BUCKET"], "Key": key, "UploadId": upload_id,
                "PartNumber": part_number},
        ExpiresIn=expires or cfg["PRESIGN_SECONDS"],
    )


def complete_multipart(key, upload_id, parts, username):
    cfg = current_app.config
    client_for("instructor", username).complete_multipart_upload(
        Bucket=cfg["S3_BUCKET"], Key=key, UploadId=upload_id,
        MultipartUpload={"Parts": sorted(parts, key=lambda p: p["PartNumber"])})


def abort_multipart(key, upload_id, username):
    cfg = current_app.config
    try:
        client_for("instructor", username).abort_multipart_upload(
            Bucket=cfg["S3_BUCKET"], Key=key, UploadId=upload_id)
    except ClientError:
        pass


def verify_object(key, expected_type, expected_size, username):
    """Post-upload check: exists, encrypted, declared size, real mp4/webm magic bytes."""
    cfg = current_app.config
    c = client_for("instructor", username)
    try:
        head = c.head_object(Bucket=cfg["S3_BUCKET"], Key=key)
        first = c.get_object(Bucket=cfg["S3_BUCKET"], Key=key, Range="bytes=0-15")["Body"].read()
    except ClientError:
        return False, "object missing or not accessible", 0
    size = head["ContentLength"]
    if head.get("ServerSideEncryption") != "AES256":
        return False, "object is not encrypted (SSE-S3)", size
    if head.get("ContentType") != expected_type:
        return False, "content type mismatch", size
    if size > cfg["MAX_UPLOAD_BYTES"]:
        return False, "file too large", size
    if size != expected_size:
        return False, "uploaded size differs from the declared size", size
    if expected_type == "video/mp4" and first[4:8] != b"ftyp":
        return False, "file content is not a valid MP4", size
    if expected_type == "video/webm" and first[:4] != b"\x1a\x45\xdf\xa3":
        return False, "file content is not a valid WebM", size
    return True, "ok", size


def delete_object(key, username):
    """Returns True if S3 accepted the delete (bucket versioning keeps a hidden old version)."""
    cfg = current_app.config
    try:
        client_for("instructor", username).delete_object(Bucket=cfg["S3_BUCKET"], Key=key)
        return True
    except ClientError:
        return False
