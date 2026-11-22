import re
import uuid
from datetime import timedelta

from botocore.exceptions import ClientError
from flask import Blueprint, abort, current_app, jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import func

from . import s3_utils as s3
from .auth import role_required
from .extensions import db
from .models import Event, User, Video, log_event, utcnow

bp = Blueprint("main", __name__)
ETAG_RE = re.compile(r'^"?[0-9A-Za-z\-]{8,100}"?$')
MIB = 1024 * 1024


@bp.route("/healthz")
def healthz():
    return "ok"


@bp.route("/")
@login_required
def index():
    videos = Video.query.filter_by(status="ready").order_by(Video.created_at.desc()).all()
    return render_template("index.html", videos=videos)


# ---------- streaming (any logged-in user: viewer or instructor) ----------
def _ready_video_or_404(vid):
    return Video.query.filter_by(id=vid, status="ready").first_or_404()


@bp.route("/watch/<int:vid>")
@login_required
def watch(vid):
    v = _ready_video_or_404(vid)
    url = s3.presign_get(v.s3_key, current_user.role, current_user.username)
    log_event("view", video_id=v.id)
    others = (Video.query.filter(Video.status == "ready", Video.id != v.id)
              .order_by(Video.created_at.desc()).limit(6).all())
    return render_template("watch.html", video=v, url=url, others=others,
                           ttl=current_app.config["PRESIGN_SECONDS"])


@bp.route("/api/stream/<int:vid>")
@login_required
def stream_renew(vid):
    v = _ready_video_or_404(vid)
    url = s3.presign_get(v.s3_key, current_user.role, current_user.username)
    log_event("url_renew", video_id=v.id)
    return jsonify(url=url, expires_in=current_app.config["PRESIGN_SECONDS"])


# ---------- upload (instructors only) ----------
def plan_parts(size):
    """Pick a chunk size: configured default, grown if the file would need >9000 parts."""
    ps = current_app.config["PART_SIZE_BYTES"]
    if -(-size // ps) > 9000:
        ps = -(-(-(-size // 9000)) // MIB) * MIB
    return ps, -(-size // ps)


@bp.route("/upload")
@role_required("instructor")
def upload_page():
    cfg = current_app.config
    return render_template("upload.html", max_bytes=cfg["MAX_UPLOAD_BYTES"])


@bp.post("/api/upload/init")
@role_required("instructor")
def upload_init():
    cfg = current_app.config
    data = request.get_json(silent=True) or {}
    title = str(data.get("title", "")).strip()[:120]
    description = str(data.get("description", "")).strip()[:500]
    filename = str(data.get("filename", ""))
    try:
        size = int(data.get("size", 0))
    except (TypeError, ValueError):
        size = 0
    try:
        duration = int(float(data.get("duration"))) if data.get("duration") else None
        if duration is not None and not 0 < duration < 7 * 24 * 3600:
            duration = None
    except (TypeError, ValueError):
        duration = None
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    ctype = cfg["ALLOWED_TYPES"].get(ext)

    if not title:
        return jsonify(error="Title is required."), 400
    if not ctype:
        log_event("upload_rejected", detail=f"bad extension: {ext[:10]}")
        return jsonify(error="Only .mp4 and .webm files are allowed."), 400
    if not 0 < size <= cfg["MAX_UPLOAD_BYTES"]:
        gb = cfg["MAX_UPLOAD_BYTES"] / 1024 ** 3
        return jsonify(error=f"File size must be between 1 byte and {gb:g} GB."), 400

    key = f"videos/{uuid.uuid4().hex}.{ext}"
    part_size, parts = plan_parts(size)
    try:
        upload_id = s3.create_multipart(key, ctype, current_user.username)
    except ClientError:
        current_app.logger.exception("create_multipart failed")
        return jsonify(error="Storage refused to start the upload."), 502
    video = Video(title=title, description=description, s3_key=key, content_type=ctype,
                  size=size, duration=duration, upload_id=upload_id, part_size=part_size,
                  parts=parts, uploader_id=current_user.id, status="pending")
    db.session.add(video)
    db.session.commit()
    return jsonify(video_id=video.id, part_size=part_size, parts=parts)


def _own_pending_upload(vid):
    v = db.get_or_404(Video, vid)
    if v.uploader_id != current_user.id or v.status != "pending":
        log_event("denied", video_id=vid, detail="access to foreign/non-pending upload")
        abort(403)
    return v


@bp.post("/api/upload/<int:vid>/part/<int:n>")
@role_required("instructor")
def upload_part_url(vid, n):
    v = _own_pending_upload(vid)
    if not 1 <= n <= (v.parts or 0):
        return jsonify(error="Invalid part number."), 400
    url = s3.presign_part(v.s3_key, v.upload_id, n, current_user.username)
    return jsonify(url=url)


@bp.post("/api/upload/<int:vid>/complete")
@role_required("instructor")
def upload_complete(vid):
    v = _own_pending_upload(vid)
    data = request.get_json(silent=True) or {}
    raw = data.get("parts")
    if not isinstance(raw, list) or len(raw) != v.parts:
        return jsonify(error="Part list does not match the upload plan."), 400
    parts = []
    for p in raw:
        try:
            num, etag = int(p["PartNumber"]), str(p["ETag"])
        except (KeyError, TypeError, ValueError):
            return jsonify(error="Malformed part list."), 400
        if not ETAG_RE.match(etag):
            return jsonify(error="Malformed part list."), 400
        parts.append({"PartNumber": num, "ETag": etag})
    if sorted(p["PartNumber"] for p in parts) != list(range(1, v.parts + 1)):
        return jsonify(error="Part numbers are incomplete."), 400

    def reject(reason):
        s3.abort_multipart(v.s3_key, v.upload_id, current_user.username)
        s3.delete_object(v.s3_key, current_user.username)
        v.status = "rejected"
        db.session.commit()
        log_event("upload_rejected", video_id=v.id, detail=reason)
        return jsonify(error=f"Upload rejected: {reason}"), 422

    try:
        s3.complete_multipart(v.s3_key, v.upload_id, parts, current_user.username)
    except ClientError:
        current_app.logger.exception("complete_multipart failed")
        return reject("storage could not assemble the uploaded parts")
    ok, reason, size = s3.verify_object(v.s3_key, v.content_type, v.size, current_user.username)
    if not ok:
        return reject(reason)
    v.status, v.size = "ready", size
    db.session.commit()
    log_event("upload", video_id=v.id, detail=f"{size} bytes in {v.parts} parts")
    return jsonify(ok=True, video_id=v.id)


@bp.post("/api/upload/<int:vid>/abort")
@role_required("instructor")
def upload_abort(vid):
    v = db.get_or_404(Video, vid)
    if v.uploader_id != current_user.id:
        log_event("denied", video_id=vid, detail="abort of foreign upload")
        abort(403)
    if v.status == "pending":
        s3.abort_multipart(v.s3_key, v.upload_id, current_user.username)
        v.status = "cancelled"
        db.session.commit()
        log_event("upload_rejected", video_id=v.id, detail="cancelled by instructor")
    return jsonify(ok=True)


@bp.route("/manage")
@role_required("instructor")
def manage():
    """Instructor's own videos with delete buttons."""
    views = dict(db.session.query(Event.video_id, func.count(Event.id))
                 .filter(Event.event_type == "view").group_by(Event.video_id).all())
    videos = (Video.query.filter_by(uploader_id=current_user.id, status="ready")
              .order_by(Video.created_at.desc()).all())
    return render_template("manage.html", videos=videos, views=views)


@bp.post("/api/videos/<int:vid>/delete")
@role_required("instructor")
def delete_video(vid):
    """An instructor can delete only the videos they uploaded."""
    v = db.get_or_404(Video, vid)
    if v.uploader_id != current_user.id:
        log_event("denied", video_id=vid, detail="delete of a video uploaded by someone else")
        abort(403)
    if v.status != "ready":
        return jsonify(error="Only published videos can be deleted."), 400
    if not s3.delete_object(v.s3_key, current_user.username):
        return jsonify(error="Storage refused the deletion. Nothing was changed."), 502
    v.status = "deleted"          # soft delete: keeps audit history and analytics intact
    db.session.commit()
    log_event("video_deleted", video_id=v.id, detail=v.title)
    return jsonify(ok=True)


# ---------- analytics + audit (instructors only) ----------
@bp.route("/analytics")
@role_required("instructor")
def analytics_page():
    return render_template("analytics.html")


@bp.route("/api/analytics")
@role_required("instructor")
def analytics_data():
    days = [(utcnow() - timedelta(days=i)).date() for i in range(13, -1, -1)]
    since = utcnow() - timedelta(days=14)

    def count(t):
        return Event.query.filter_by(event_type=t).count()

    per_video = (db.session.query(Video.title, func.count(Event.id))
                 .join(Event, Event.video_id == Video.id)
                 .filter(Event.event_type == "view", Video.status != "deleted")
                 .group_by(Video.id).order_by(func.count(Event.id).desc()).limit(10).all())
    daily = {}
    rows = (db.session.query(func.date(Event.ts), Event.event_type, func.count(Event.id))
            .filter(Event.ts >= since, Event.event_type.in_(["view", "denied", "login_fail"]))
            .group_by(func.date(Event.ts), Event.event_type).all())
    for d, t, n in rows:
        daily[(str(d), t)] = n
    uploads = (db.session.query(User.username, func.count(Video.id))
               .join(Video, Video.uploader_id == User.id)
               .filter(Video.status == "ready").group_by(User.id).all())
    top_viewers = (db.session.query(Event.username, func.count(Event.id))
                   .filter(Event.event_type == "view").group_by(Event.username)
                   .order_by(func.count(Event.id).desc()).limit(5).all())
    return jsonify(
        totals=dict(users=User.query.count(),
                    videos=Video.query.filter_by(status="ready").count(),
                    views=count("view"), denied=count("denied"),
                    failed_logins=count("login_fail")),
        views_per_video=dict(labels=[r[0] for r in per_video], data=[r[1] for r in per_video]),
        daily=dict(labels=[d.isoformat() for d in days],
                   views=[daily.get((d.isoformat(), "view"), 0) for d in days],
                   denied=[daily.get((d.isoformat(), "denied"), 0) for d in days],
                   failed_logins=[daily.get((d.isoformat(), "login_fail"), 0) for d in days]),
        uploads_per_instructor=dict(labels=[r[0] for r in uploads], data=[r[1] for r in uploads]),
        top_viewers=dict(labels=[r[0] for r in top_viewers], data=[r[1] for r in top_viewers]),
    )


@bp.route("/audit")
@role_required("instructor")
def audit():
    events = Event.query.order_by(Event.ts.desc()).limit(200).all()
    types = [t for (t,) in db.session.query(Event.event_type).distinct().order_by(Event.event_type)]
    return render_template("audit.html", events=events, types=types)
