"""
LIVE authorised-vs-unauthorised test against your real AWS bucket.
Writes docs/test-report.md (use it, plus screenshots, in your report).

Run from your laptop or the EC2 VM (needs credentials allowed to assume both roles):
    python tests/live_access_test.py
"""
import os
import time
import uuid
from datetime import datetime, timezone

import boto3
import requests
from botocore.config import Config
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()
REGION = os.environ.get("AWS_REGION", "ap-south-1")
BUCKET = os.environ["S3_BUCKET"]
INSTR_ARN = os.environ["INSTRUCTOR_ROLE_ARN"]
VIEW_ARN = os.environ["VIEWER_ROLE_ARN"]
KEY = f"videos/_probe/{uuid.uuid4().hex}.mp4"
BODY = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
results, cleanup_keys, cleanup_uploads = [], [KEY], []


def s3_as(arn, name):
    c = boto3.client("sts", region_name=REGION).assume_role(RoleArn=arn, RoleSessionName=name)["Credentials"]
    return boto3.client("s3", region_name=REGION, aws_access_key_id=c["AccessKeyId"],
                        aws_secret_access_key=c["SecretAccessKey"], aws_session_token=c["SessionToken"],
                        config=Config(signature_version="s3v4"))


def record(tid, desc, expected, actual, passed):
    results.append((tid, desc, expected, actual, "PASS" if passed else "FAIL"))
    print(f"[{'PASS' if passed else 'FAIL'}] {tid} {desc}: {actual}")


def http(method, url, **kw):
    r = requests.request(method, url, timeout=30, **kw)
    code = ""
    if "<Code>" in r.text:
        code = r.text.split("<Code>")[1].split("</Code>")[0]
    return r.status_code, code, r


def client_err(fn):
    try:
        fn()
        return "allowed"
    except ClientError as e:
        return e.response["Error"]["Code"]


instr, viewer = s3_as(INSTR_ARN, "live-test-instructor"), s3_as(VIEW_ARN, "live-test-viewer")
public_url = f"https://{BUCKET}.s3.{REGION}.amazonaws.com/{KEY}"

# Setup: instructor uploads a probe object
instr.put_object(Bucket=BUCKET, Key=KEY, Body=BODY, ContentType="video/mp4", ServerSideEncryption="AES256")

# --- unauthorised ---
s, c, _ = http("GET", public_url)
record("T1", "Anonymous GET of object URL", "403 AccessDenied", f"{s} {c}", s == 403)
s, c, _ = http("GET", f"https://{BUCKET}.s3.{REGION}.amazonaws.com/")
record("T2", "Anonymous bucket listing", "403 AccessDenied", f"{s} {c}", s == 403)

a = client_err(lambda: viewer.put_object(Bucket=BUCKET, Key=f"videos/_probe/v-{uuid.uuid4().hex}.mp4",
                                         Body=BODY, ServerSideEncryption="AES256"))
record("T3", "Viewer role direct PutObject", "AccessDenied", a, a == "AccessDenied")

a = client_err(lambda: viewer.list_objects_v2(Bucket=BUCKET))
record("T4", "Viewer role ListBucket", "AccessDenied", a, a == "AccessDenied")

put_url = viewer.generate_presigned_url("put_object", Params={"Bucket": BUCKET, "Key": KEY + ".v",
                                        "ServerSideEncryption": "AES256"}, ExpiresIn=60)
s, c, _ = http("PUT", put_url, data=BODY, headers={"x-amz-server-side-encryption": "AES256"})
record("T5", "Viewer-signed pre-signed PUT URL", "403 AccessDenied", f"{s} {c}", s == 403)

# Encryption: no way to store an object unencrypted or with another algorithm
nosse_key = KEY + ".nosse"
cleanup_keys.append(nosse_key)
instr.put_object(Bucket=BUCKET, Key=nosse_key, Body=BODY, ContentType="video/mp4")
sse = instr.head_object(Bucket=BUCKET, Key=nosse_key).get("ServerSideEncryption")
record("T6", "Upload without SSE header is still encrypted at rest (bucket default encryption)",
       "AES256", str(sse), sse == "AES256")
a = client_err(lambda: instr.put_object(Bucket=BUCKET, Key=KEY + ".kms", Body=BODY, ServerSideEncryption="aws:kms"))
record("T6b", "Upload requesting a different algorithm (aws:kms) is refused", "AccessDenied", a, a == "AccessDenied")

# --- authorised ---
url = viewer.generate_presigned_url("get_object", Params={"Bucket": BUCKET, "Key": KEY}, ExpiresIn=300)
s, c, _ = http("GET", url)
record("T7", "Viewer pre-signed GET URL", "200 OK", str(s), s == 200)
s, c, _ = http("GET", url, headers={"Range": "bytes=0-15"})
record("T8", "Range request (needed for video seeking)", "206 Partial Content", str(s), s == 206)

# --- token attacks ---
bad = url.replace("X-Amz-Signature=", "X-Amz-Signature=00")
s, c, _ = http("GET", bad)
record("T9", "Tampered signature", "403 SignatureDoesNotMatch", f"{s} {c}", s == 403)
s, c, _ = http("GET", url.replace(KEY, KEY + "x"))
record("T10", "Same signature reused for a different object", "403", f"{s} {c}", s == 403)
s, c, _ = http("GET", url.replace("https://", "http://", 1))
record("T11", "Plain HTTP (non-TLS) request with valid URL", "403 (bucket policy)", f"{s} {c}", s == 403)

short = viewer.generate_presigned_url("get_object", Params={"Bucket": BUCKET, "Key": KEY}, ExpiresIn=3)
s0, _, _ = http("GET", short)
time.sleep(12)   # longer than expiry + any local clock skew
s1, c1, _ = http("GET", short)
record("T12", "Pre-signed URL (3 s) before and after expiry", "200 then 403 expired", f"{s0} then {s1} {c1}", s0 == 200 and s1 == 403)

# --- chunked (multipart) upload: exactly what the web app does ---
mp_key = KEY + ".multipart"
cleanup_keys.append(mp_key)
try:
    mp = instr.create_multipart_upload(Bucket=BUCKET, Key=mp_key, ContentType="video/mp4", ServerSideEncryption="AES256")
    uid = mp["UploadId"]
    cleanup_uploads.append((mp_key, uid))
    part_url = instr.generate_presigned_url("upload_part", Params={"Bucket": BUCKET, "Key": mp_key,
                                            "UploadId": uid, "PartNumber": 1}, ExpiresIn=120)
    r = requests.put(part_url, data=BODY, timeout=30)
    etag = r.headers.get("ETag")
    instr.complete_multipart_upload(Bucket=BUCKET, Key=mp_key, UploadId=uid,
                                    MultipartUpload={"Parts": [{"PartNumber": 1, "ETag": etag}]})
    cleanup_uploads.pop()
    head = instr.head_object(Bucket=BUCKET, Key=mp_key)
    ok = r.status_code == 200 and head["ServerSideEncryption"] == "AES256" and head["ContentLength"] == len(BODY)
    record("T13", "Instructor chunked upload via pre-signed part URL", "200, object encrypted",
           f"part {r.status_code}, {head.get('ServerSideEncryption')}", ok)
except ClientError as e:
    record("T13", "Instructor chunked upload via pre-signed part URL", "200, object encrypted",
           e.response["Error"]["Code"], False)

a = client_err(lambda: viewer.create_multipart_upload(Bucket=BUCKET, Key=KEY + ".vmp", ServerSideEncryption="AES256"))
record("T14", "Viewer cannot start a chunked upload", "AccessDenied", a, a == "AccessDenied")

try:
    mp2 = instr.create_multipart_upload(Bucket=BUCKET, Key=KEY + ".mp2", ServerSideEncryption="AES256")
    cleanup_uploads.append((KEY + ".mp2", mp2["UploadId"]))
    vurl = viewer.generate_presigned_url("upload_part", Params={"Bucket": BUCKET, "Key": KEY + ".mp2",
                                         "UploadId": mp2["UploadId"], "PartNumber": 1}, ExpiresIn=60)
    s, c, _ = http("PUT", vurl, data=BODY)
    record("T15", "Viewer-signed chunk URL for an instructor's upload", "403 AccessDenied", f"{s} {c}", s == 403)
except ClientError as e:
    record("T15", "Viewer-signed chunk URL for an instructor's upload", "403 AccessDenied", e.response["Error"]["Code"], False)

# cleanup
for k, uid in cleanup_uploads:
    try:
        instr.abort_multipart_upload(Bucket=BUCKET, Key=k, UploadId=uid)
    except ClientError:
        pass
for k in cleanup_keys:
    try:
        instr.delete_object(Bucket=BUCKET, Key=k)
    except ClientError:
        pass

os.makedirs("docs", exist_ok=True)
with open("docs/test-report.md", "w", encoding="utf-8") as f:
    f.write(f"# Live access test report\n\nBucket: `{BUCKET}` | Region: `{REGION}` | "
            f"Run: {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}\n\n")
    f.write("| ID | Test | Expected | Actual | Result |\n|---|---|---|---|---|\n")
    for r in results:
        f.write("| " + " | ".join(r) + " |\n")
    f.write(f"\n**{sum(r[4]=='PASS' for r in results)}/{len(results)} passed.**\n")
print("\nReport written to docs/test-report.md")
