"""Authorised vs unauthorised access + chunked upload flow (S3 mocked with moto)."""
import json

from app.extensions import db
from app.models import Event, User, Video
from tests.conftest import BUCKET, FAKE_MP4, PASSWORD, login


def _post(client, url, body=None):
    return client.post(url, data=json.dumps(body or {}), content_type="application/json")


def _init(client, **over):
    body = dict(title="Lecture 1", description="", filename="lec.mp4", size=len(FAKE_MP4), duration=75)
    body.update(over)
    return _post(client, "/api/upload/init", body)


def _ready_video(uploader="ins1"):
    u = User.query.filter_by(username=uploader).first()
    v = Video(title="Ready", s3_key="videos/ready.mp4", content_type="video/mp4",
              uploader_id=u.id, status="ready", size=10, duration=65)
    db.session.add(v)
    db.session.commit()
    return v


def _upload_all_parts(s3, video, body):
    """Do what the browser does: PUT each chunk, collect ETags."""
    parts = []
    ps = video.part_size
    for n in range(1, video.parts + 1):
        r = s3.upload_part(Bucket=BUCKET, Key=video.s3_key, UploadId=video.upload_id,
                           PartNumber=n, Body=body[(n - 1) * ps: n * ps])
        parts.append({"PartNumber": n, "ETag": r["ETag"]})
    return parts


# ---------------- anonymous ----------------
def test_anonymous_redirected_from_home(client):
    assert client.get("/").status_code == 302


def test_anonymous_api_is_401(client):
    assert _init(client).status_code == 401


def test_anonymous_cannot_stream(client):
    v = _ready_video()
    assert client.get(f"/watch/{v.id}").status_code == 302
    assert client.get(f"/api/stream/{v.id}").status_code == 302


def test_public_pages_render(client):
    for path in ("/login", "/register", "/forgot-password"):
        r = client.get(path)
        assert r.status_code == 200 and b"SecureStream" in r.data


# ---------------- viewer role ----------------
def test_viewer_cannot_upload_and_denial_is_logged(client):
    login(client, "view1")
    assert client.get("/upload").status_code == 403
    assert _init(client).status_code == 403
    assert Event.query.filter_by(event_type="denied", username="view1").count() == 2


def test_viewer_cannot_use_any_upload_endpoint(client):
    login(client, "view1")
    for path in ("/api/upload/1/part/1", "/api/upload/1/complete", "/api/upload/1/abort"):
        assert _post(client, path).status_code == 403


def test_viewer_cannot_see_analytics_or_audit(client):
    login(client, "view1")
    for path in ("/analytics", "/api/analytics", "/audit"):
        assert client.get(path).status_code == 403


def test_viewer_can_stream_with_short_presigned_url(client):
    v = _ready_video()
    login(client, "view1")
    r = client.get(f"/watch/{v.id}")
    assert r.status_code == 200
    assert b"X-Amz-Signature" in r.data and b"X-Amz-Expires=300" in r.data
    assert Event.query.filter_by(event_type="view", username="view1").count() == 1


def test_library_and_stream_pages_render_for_each_role(client):
    v = _ready_video()
    login(client, "view1")
    assert b"Upload video" not in client.get("/").data          # no upload button for viewers
    assert b"Ready" in client.get("/").data
    client.post("/logout")
    login(client, "ins1")
    home = client.get("/").data
    assert b"Upload video" in home and b"Ready" in home
    assert client.get("/upload").status_code == 200
    assert client.get("/analytics").status_code == 200
    assert client.get("/audit").status_code == 200
    assert client.get(f"/watch/{v.id}").status_code == 200


def test_pending_video_is_not_streamable(client):
    v = _ready_video()
    v.status = "pending"
    db.session.commit()
    login(client, "view1")
    assert client.get(f"/watch/{v.id}").status_code == 404


# ---------------- registration / login ----------------
def test_registration_cannot_create_instructor(client):
    client.post("/register", data={"username": "eve_x", "email": "eve@example.com",
                                   "password": "longpassword1", "role": "instructor"})
    assert User.query.filter_by(username="eve_x").one().role == "viewer"


def test_registration_validation(client):
    base = {"username": "newuser", "email": "new@example.com", "password": "longpassword1"}
    client.post("/register", data={**base, "email": "not-an-email"})
    client.post("/register", data={**base, "password": "short"})
    client.post("/register", data={**base, "password": "1234567890123"})        # digits only
    client.post("/register", data={**base, "email": "VIEW1@example.com"})      # duplicate e-mail
    assert User.query.filter_by(username="newuser").first() is None


def test_wrong_password_logged(client):
    client.post("/login", data={"username": "view1", "password": "nope"})
    assert Event.query.filter_by(event_type="login_fail").count() == 1


# ---------------- instructor chunked upload ----------------
def test_upload_init_validation(client):
    login(client, "ins1")
    assert _init(client, filename="malware.exe").status_code == 400
    assert _init(client, size=10 * 1024 * 1024).status_code == 400
    assert _init(client, size=0).status_code == 400
    assert _init(client, title="").status_code == 400


def test_upload_init_returns_multipart_plan(client):
    login(client, "ins1")
    r = _init(client)
    assert r.status_code == 200
    j = r.get_json()
    assert j["parts"] >= 1 and j["part_size"] >= 5 * 1024 * 1024
    v = db.session.get(Video, j["video_id"])
    assert v.status == "pending" and v.upload_id and v.s3_key.startswith("videos/") and v.duration == 75


def test_part_size_grows_for_huge_files(app):
    from app.routes import plan_parts
    with app.test_request_context():
        ps, parts = plan_parts(5 * 1024 ** 4)          # 5 TiB (S3's maximum object size)
        assert parts <= 9000 and ps * parts >= 5 * 1024 ** 4
        ps, parts = plan_parts(1)
        assert (ps, parts) == (8 * 1024 * 1024, 1)


def test_part_url_is_presigned_and_validated(client):
    login(client, "ins1")
    vid = _init(client).get_json()["video_id"]
    r = _post(client, f"/api/upload/{vid}/part/1")
    assert r.status_code == 200
    url = r.get_json()["url"]
    assert "X-Amz-Signature" in url and "partNumber=1" in url and "uploadId=" in url
    assert _post(client, f"/api/upload/{vid}/part/2").status_code == 400    # beyond the plan
    assert _post(client, f"/api/upload/{vid}/part/0").status_code == 400    # parts are numbered from 1


def test_complete_accepts_real_mp4(client, s3):
    login(client, "ins1")
    vid = _init(client).get_json()["video_id"]
    video = db.session.get(Video, vid)
    parts = _upload_all_parts(s3, video, FAKE_MP4)
    r = _post(client, f"/api/upload/{vid}/complete", {"parts": parts})
    assert r.status_code == 200
    assert db.session.get(Video, vid).status == "ready"
    head = s3.head_object(Bucket=BUCKET, Key=video.s3_key)
    assert head["ServerSideEncryption"] == "AES256" and head["ContentLength"] == len(FAKE_MP4)


def test_complete_rejects_disguised_exe_and_deletes_it(client, s3):
    login(client, "ins1")
    exe = b"MZ" + b"\x00" * (len(FAKE_MP4) - 2)
    vid = _init(client).get_json()["video_id"]
    video = db.session.get(Video, vid)
    parts = _upload_all_parts(s3, video, exe)
    assert _post(client, f"/api/upload/{vid}/complete", {"parts": parts}).status_code == 422
    assert db.session.get(Video, vid).status == "rejected"
    assert s3.list_objects_v2(Bucket=BUCKET, Prefix=video.s3_key)["KeyCount"] == 0


def test_complete_rejects_wrong_declared_size(client, s3):
    login(client, "ins1")
    vid = _init(client, size=5000).get_json()["video_id"]       # claims 5000 B, sends 76 B
    video = db.session.get(Video, vid)
    parts = _upload_all_parts(s3, video, FAKE_MP4)
    assert _post(client, f"/api/upload/{vid}/complete", {"parts": parts}).status_code == 422
    assert db.session.get(Video, vid).status == "rejected"


def test_complete_rejects_bad_part_lists(client, s3):
    login(client, "ins1")
    vid = _init(client).get_json()["video_id"]
    assert _post(client, f"/api/upload/{vid}/complete", {"parts": []}).status_code == 400
    assert _post(client, f"/api/upload/{vid}/complete", {"parts": [{"PartNumber": 1, "ETag": "x y"}]}).status_code == 400
    assert _post(client, f"/api/upload/{vid}/complete", {"parts": [{"PartNumber": 2, "ETag": "abcdef0123456789"}]}).status_code == 400
    assert db.session.get(Video, vid).status == "pending"


def test_instructor_cannot_touch_someone_elses_upload(client):
    login(client, "ins1")
    vid = _init(client).get_json()["video_id"]
    client.post("/logout")
    login(client, "ins2")
    assert _post(client, f"/api/upload/{vid}/part/1").status_code == 403
    assert _post(client, f"/api/upload/{vid}/complete", {"parts": []}).status_code == 403
    assert _post(client, f"/api/upload/{vid}/abort").status_code == 403


def test_abort_cancels_upload(client, s3):
    login(client, "ins1")
    vid = _init(client).get_json()["video_id"]
    assert _post(client, f"/api/upload/{vid}/abort").status_code == 200
    assert db.session.get(Video, vid).status == "cancelled"
    assert s3.list_multipart_uploads(Bucket=BUCKET).get("Uploads", []) == []


def test_instructor_sees_analytics(client):
    login(client, "ins1")
    r = client.get("/api/analytics")
    assert r.status_code == 200 and "totals" in r.get_json()


# ---------------- forgot / reset password ----------------
def _capture_links(monkeypatch):
    sent = []
    monkeypatch.setattr("app.auth.send_reset_email", lambda to, link: sent.append((to, link)))
    return sent


def test_forgot_password_sends_link_and_reset_works_once(client, monkeypatch):
    sent = _capture_links(monkeypatch)
    r = client.post("/forgot-password", data={"email": "VIEW1@example.com"}, follow_redirects=True)
    assert b"reset link is on its way" in r.data
    assert len(sent) == 1 and sent[0][0] == "view1@example.com"
    link = sent[0][1]
    assert link.startswith("https://videos.example.com/reset-password/")   # base URL from config, not Host header
    path = link.replace("https://videos.example.com", "")
    assert client.get(path).status_code == 200

    r = client.post(path, data={"password": "BrandNewPass99", "confirm": "BrandNewPass99"})
    assert r.status_code == 302
    assert login(client, "view1", "BrandNewPass99").status_code == 302       # new password works
    client.post("/logout")
    assert client.post("/login", data={"username": "view1", "password": PASSWORD}).status_code == 200  # old one dead
    assert client.get(path, follow_redirects=True).status_code == 200
    assert b"invalid or has expired" in client.get(path, follow_redirects=True).data       # single use
    assert Event.query.filter_by(event_type="password_reset").count() == 1


def test_forgot_password_does_not_reveal_unknown_emails(client, monkeypatch):
    sent = _capture_links(monkeypatch)
    r = client.post("/forgot-password", data={"email": "nobody@example.com"}, follow_redirects=True)
    assert b"reset link is on its way" in r.data and sent == []


def test_reset_rejects_weak_or_mismatched_passwords(client, monkeypatch):
    sent = _capture_links(monkeypatch)
    client.post("/forgot-password", data={"email": "view1@example.com"})
    path = sent[0][1].replace("https://videos.example.com", "")
    client.post(path, data={"password": "short", "confirm": "short"})
    client.post(path, data={"password": "BrandNewPass99", "confirm": "different99999"})
    client.post(path, data={"password": "12345678901234", "confirm": "12345678901234"})
    assert login(client, "view1").status_code == 302                         # original password still valid


def test_reset_token_expiry_and_tampering(app):
    from app.auth import load_reset_token, make_reset_token
    with app.test_request_context():
        user = User.query.filter_by(username="view1").one()
        token = make_reset_token(user)
        assert load_reset_token(token).id == user.id
        assert load_reset_token(token, max_age=-1) is None                   # expired
        assert load_reset_token(token[:-2] + "xx") is None                   # tampered


def test_dev_mode_prints_link_instead_of_emailing(app, caplog):
    from app.mailer import send_reset_email
    with app.test_request_context():
        assert send_reset_email("a@b.com", "https://x/y") is False
    assert "https://x/y" in caplog.text


def test_database_migration_adds_new_columns(tmp_path):
    """An old SQLite file (no e-mail / multipart columns) is upgraded in place."""
    import sqlite3
    from app import create_app
    from app.config import Config
    db_file = tmp_path / "old.db"
    con = sqlite3.connect(db_file)
    con.executescript("""
      CREATE TABLE user (id INTEGER PRIMARY KEY, username VARCHAR(30) UNIQUE NOT NULL,
          password_hash VARCHAR(256) NOT NULL, role VARCHAR(20) NOT NULL, created_at DATETIME);
      CREATE TABLE video (id INTEGER PRIMARY KEY, title VARCHAR(120) NOT NULL, description VARCHAR(500),
          s3_key VARCHAR(200) UNIQUE NOT NULL, content_type VARCHAR(50) NOT NULL, size BIGINT,
          uploader_id INTEGER NOT NULL, status VARCHAR(20), created_at DATETIME);
      INSERT INTO user VALUES (1,'teacher1','x','instructor',NULL);
      INSERT INTO video VALUES (1,'old','', 'videos/a.mp4','video/mp4',100,1,'ready',NULL);
    """)
    con.commit(); con.close()

    class Cfg(Config):
        TESTING = True
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_file}"
        RATELIMIT_ENABLED = False
    app = create_app(Cfg)
    with app.app_context():
        u = User.query.filter_by(username="teacher1").one()
        assert u.email is None
        u.email = "t@example.com"
        db.session.commit()
        v = db.session.get(Video, 1)
        assert v.title == "old" and v.upload_id is None and v.duration is None


# ---------------- delete video ----------------
def _publish(client, s3):
    vid = _init(client).get_json()["video_id"]
    video = db.session.get(Video, vid)
    _post(client, f"/api/upload/{vid}/complete", {"parts": _upload_all_parts(s3, video, FAKE_MP4)})
    assert db.session.get(Video, vid).status == "ready"
    return video


def test_instructor_can_delete_own_video(client, s3):
    login(client, "ins1")
    video = _publish(client, s3)
    assert b"Delete this video" in client.get(f"/watch/{video.id}").data
    assert _post(client, f"/api/videos/{video.id}/delete").status_code == 200
    assert db.session.get(Video, video.id).status == "deleted"
    assert s3.list_objects_v2(Bucket=BUCKET, Prefix=video.s3_key)["KeyCount"] == 0
    assert client.get(f"/watch/{video.id}").status_code == 404
    assert b"Lecture 1" not in client.get("/").data
    assert Event.query.filter_by(event_type="video_deleted", video_id=video.id).count() == 1


def test_only_the_uploader_can_delete(client, s3):
    login(client, "ins1")
    video = _publish(client, s3)
    client.post("/logout")
    login(client, "ins2")
    assert b"Delete this video" not in client.get(f"/watch/{video.id}").data
    assert _post(client, f"/api/videos/{video.id}/delete").status_code == 403
    client.post("/logout")
    login(client, "view1")
    assert b"Delete this video" not in client.get(f"/watch/{video.id}").data
    assert _post(client, f"/api/videos/{video.id}/delete").status_code == 403
    assert db.session.get(Video, video.id).status == "ready"
    assert s3.list_objects_v2(Bucket=BUCKET, Prefix=video.s3_key)["KeyCount"] == 1


def test_cannot_delete_unpublished_or_anonymous(client):
    v = _ready_video()
    assert _post(client, f"/api/videos/{v.id}/delete").status_code == 401
    login(client, "ins1")
    v.status = "pending"
    db.session.commit()
    assert _post(client, f"/api/videos/{v.id}/delete").status_code == 400


def test_my_videos_page_lists_only_own_videos_with_delete(client, s3):
    login(client, "ins1")
    mine = _publish(client, s3)
    client.post("/logout")
    login(client, "ins2")
    other = _ready_video("ins1")                    # also uploaded by ins1
    page = client.get("/manage")
    assert page.status_code == 200 and b"no published videos" in page.data    # ins2 owns nothing
    client.post("/logout")
    login(client, "ins1")
    page = client.get("/manage").data
    assert page.count(b"data-delete") == 2 and b"Lecture 1" in page and b"Ready" in page
    assert _post(client, f"/api/videos/{mine.id}/delete").status_code == 200
    assert client.get("/manage").data.count(b"data-delete") == 1
    assert b"My videos" in client.get("/").data


def test_my_videos_page_is_instructor_only(client):
    assert client.get("/manage").status_code == 302
    login(client, "view1")
    assert client.get("/manage").status_code == 403
    assert b"My videos" not in client.get("/").data


# ---------------- instructor invitations ----------------
def _capture_invites(monkeypatch):
    sent = []
    monkeypatch.setattr("app.auth.send_invite_email", lambda to, link, inviter: sent.append((to, link, inviter)))
    return sent


def _join_path(link):
    return link.replace("https://videos.example.com", "")


def test_only_instructors_can_invite(client):
    assert client.get("/invite").status_code == 302
    login(client, "view1")
    assert client.get("/invite").status_code == 403
    assert client.post("/invite", data={"email": "x@example.com"}).status_code == 403
    assert Event.query.filter_by(event_type="denied", username="view1").count() == 2


def test_invited_teacher_can_join_and_upload(client, monkeypatch):
    sent = _capture_invites(monkeypatch)
    login(client, "ins1")
    r = client.post("/invite", data={"email": "New.Teacher@Example.com"})
    assert r.status_code == 200 and b"/join/" in r.data
    assert len(sent) == 1 and sent[0][0] == "new.teacher@example.com" and sent[0][2] == "ins1"
    path = _join_path(sent[0][1])
    assert Event.query.filter_by(event_type="instructor_invited").count() == 1
    client.post("/logout")

    assert client.get(path).status_code == 200
    r = client.post(path, data={"username": "teacher_new", "password": "BrandNewPass99", "confirm": "BrandNewPass99"})
    assert r.status_code == 302
    u = User.query.filter_by(username="teacher_new").one()
    assert u.role == "instructor" and u.email == "new.teacher@example.com"
    assert login(client, "teacher_new", "BrandNewPass99").status_code == 302
    assert client.get("/upload").status_code == 200                      # can reach the upload page
    assert _init(client).status_code == 200                               # and start an upload
    assert Event.query.filter_by(event_type="instructor_joined").count() == 1


def test_invite_link_is_single_use(client, monkeypatch):
    sent = _capture_invites(monkeypatch)
    login(client, "ins1")
    client.post("/invite", data={"email": "once@example.com"})
    path = _join_path(sent[0][1])
    client.post("/logout")
    client.post(path, data={"username": "once_user", "password": "BrandNewPass99", "confirm": "BrandNewPass99"})
    r = client.get(path, follow_redirects=True)
    assert b"invalid, already used, or has expired" in r.data
    r = client.post(path, data={"username": "twice_user", "password": "BrandNewPass99", "confirm": "BrandNewPass99"})
    assert r.status_code == 302 and User.query.filter_by(username="twice_user").first() is None


def test_invite_validation_and_bad_tokens(client, monkeypatch, app):
    sent = _capture_invites(monkeypatch)
    login(client, "ins1")
    client.post("/invite", data={"email": "not-an-email"})
    client.post("/invite", data={"email": "view1@example.com"})          # already registered
    assert sent == []
    client.post("/invite", data={"email": "weak@example.com"})
    path = _join_path(sent[0][1])
    client.post("/logout")
    assert client.get(path[:-3] + "abc", follow_redirects=True).status_code == 200      # tampered
    assert b"invalid, already used" in client.get(path[:-3] + "abc", follow_redirects=True).data
    for bad in ({"username": "ab", "password": "BrandNewPass99", "confirm": "BrandNewPass99"},
                {"username": "ins1", "password": "BrandNewPass99", "confirm": "BrandNewPass99"},      # taken
                {"username": "okname", "password": "BrandNewPass99", "confirm": "Different99999"},
                {"username": "okname", "password": "short", "confirm": "short"}):
        client.post(path, data=bad)
    assert User.query.filter_by(email="weak@example.com").first() is None
    from app.auth import load_invite
    with app.test_request_context():
        assert load_invite(path.split("/join/")[1], max_age=-1) is None                  # expired


def test_invite_page_renders_with_dev_mode_link(client):
    login(client, "ins1")
    page = client.post("/invite", data={"email": "dev@example.com"}).data
    assert b"No e-mail was sent" in page and b"Invitation created for dev@example.com" in page


# ---------------- instructor access requests ----------------
def _request_access(client, message="I teach cloud computing"):
    return client.post("/request-instructor", data={"message": message})


def test_request_access_page_rules(client):
    assert client.get("/request-instructor").status_code == 302             # anonymous
    login(client, "ins1")
    assert client.get("/request-instructor").status_code == 302             # instructors have nothing to request
    client.post("/logout")
    login(client, "view1")
    page = client.get("/request-instructor").data
    assert b"Become an instructor" in page and b"Send request" in page
    assert b"Become an instructor" in client.get("/").data                  # visible in the viewer's menu


def test_viewer_request_is_approved_by_instructor(client):
    from app.models import AccessRequest
    login(client, "view1")
    _request_access(client, "x" * 500)
    req = AccessRequest.query.one()
    assert req.status == "pending" and len(req.message) == 300              # message is trimmed
    assert b"Request pending" in client.get("/request-instructor").data
    _request_access(client)                                                 # no duplicate pending request
    assert AccessRequest.query.count() == 1
    assert client.get("/upload").status_code == 403                         # still a viewer
    client.post("/logout")

    login(client, "ins1")
    team = client.get("/invite").data
    assert b"view1" in team and b"Approve" in team and b'class="count"' in client.get("/").data
    assert _post(client, f"/api/requests/{req.id}/approve").status_code == 200
    assert _post(client, f"/api/requests/{req.id}/approve").status_code == 400   # already handled
    client.post("/logout")

    assert User.query.filter_by(username="view1").one().role == "instructor"
    login(client, "view1")
    assert client.get("/upload").status_code == 200                         # now an instructor
    assert Event.query.filter_by(event_type="instructor_requested").count() == 1
    assert Event.query.filter_by(event_type="instructor_approved").count() == 1


def test_rejected_request_keeps_viewer_and_can_be_retried(client):
    from app.models import AccessRequest
    login(client, "view1")
    _request_access(client)
    rid = AccessRequest.query.one().id
    client.post("/logout")
    login(client, "ins1")
    assert _post(client, f"/api/requests/{rid}/reject").status_code == 200
    client.post("/logout")
    login(client, "view1")
    assert User.query.filter_by(username="view1").one().role == "viewer"
    page = client.get("/request-instructor").data
    assert b"not approved" in page and b"Send request" in page
    _request_access(client, "second try")
    assert AccessRequest.query.filter_by(status="pending").count() == 1
    assert Event.query.filter_by(event_type="instructor_rejected").count() == 1


def test_viewers_cannot_decide_requests(client):
    from app.models import AccessRequest
    login(client, "view1")
    _request_access(client)
    rid = AccessRequest.query.one().id
    assert _post(client, f"/api/requests/{rid}/approve").status_code == 403    # cannot approve own request
    assert client.get("/invite").status_code == 403
    assert db.session.get(User, User.query.filter_by(username="view1").one().id).role == "viewer"
    client.post("/logout")
    login(client, "ins1")
    assert _post(client, f"/api/requests/{rid}/promote").status_code == 404    # unknown action
    assert _post(client, "/api/requests/9999/approve").status_code == 404
