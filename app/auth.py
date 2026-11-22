import hashlib
import re
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect, render_template,
                   request, session, url_for)
from flask_login import current_user, login_required, login_user, logout_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .extensions import db, limiter, login_manager
from .mailer import send_invite_email, send_reset_email
from .models import User, log_event

bp = Blueprint("auth", __name__)
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,30}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def role_required(role):
    """Server-side RBAC. Denied attempts are written to the audit log."""
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                if request.path.startswith("/api/"):
                    return jsonify(error="authentication required"), 401
                return login_manager.unauthorized()
            if current_user.role != role:
                log_event("denied", detail=f"{request.method} {request.path} requires role={role}")
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return deco


def password_problem(password, username=""):
    if len(password) < 10:
        return "Password must be at least 10 characters."
    if username and password.lower() == username.lower():
        return "Password must not be the same as the username."
    if password.isdigit() or password.isalpha():
        return "Use a mix of letters and numbers (or symbols)."
    return None


# ---------------- password reset tokens ----------------
def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="password-reset")


def _fingerprint(user):
    return hashlib.sha256(user.password_hash.encode()).hexdigest()[:20]


def make_reset_token(user):
    # Includes a fingerprint of the current password hash => the link dies once it is used.
    return _serializer().dumps({"u": user.id, "h": _fingerprint(user)})


def load_reset_token(token, max_age=None):
    max_age = current_app.config["RESET_TOKEN_SECONDS"] if max_age is None else max_age
    try:
        data = _serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    user = db.session.get(User, data.get("u"))
    if not user or data.get("h") != _fingerprint(user):
        return None
    return user


# ---------------- instructor invitations ----------------
INVITE_SECONDS = 48 * 3600


def _invite_serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="instructor-invite")


def load_invite(token, max_age=INVITE_SECONDS):
    """Valid while signed, unexpired and the invited e-mail has no account yet (=> single use)."""
    try:
        data = _invite_serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    email = data.get("e")
    if not email or User.query.filter_by(email=email).first():
        return None
    return data


# ---------------- views ----------------
@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(request.form.get("password", "")):
            login_user(user)
            session.permanent = True
            log_event("login_ok")
            return redirect(url_for("main.index"))
        log_event("login_fail", username=username[:30])
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@bp.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def register():
    """Self-registration ALWAYS creates a viewer. Instructors are created only via CLI."""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        problem = password_problem(password, username)
        if not USERNAME_RE.match(username):
            flash("Username: 3-30 letters, digits or underscore.", "error")
        elif not EMAIL_RE.match(email) or len(email) > 120:
            flash("Please enter a valid e-mail address.", "error")
        elif problem:
            flash(problem, "error")
        elif User.query.filter_by(username=username).first() or User.query.filter_by(email=email).first():
            flash("That username or e-mail is already registered.", "error")
        else:
            u = User(username=username, email=email, role="viewer")
            u.set_password(password)
            db.session.add(u)
            db.session.commit()
            log_event("register", username=username)
            flash("Account created. Please log in.", "success")
            return redirect(url_for("auth.login"))
    return render_template("register.html")


@bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first() if EMAIL_RE.match(email) else None
        if user:
            base = current_app.config["APP_BASE_URL"] or request.url_root.rstrip("/")
            link = f"{base}{url_for('auth.reset_password', token=make_reset_token(user))}"
            send_reset_email(user.email, link)
            log_event("password_reset_requested", username=user.username)
        # Same answer whether or not the account exists (no user enumeration).
        flash("If that e-mail is registered, a reset link is on its way. It is valid for 30 minutes.", "info")
        return redirect(url_for("auth.login"))
    return render_template("forgot.html")


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def reset_password(token):
    user = load_reset_token(token)
    if not user:
        flash("This reset link is invalid or has expired. Please request a new one.", "error")
        return redirect(url_for("auth.forgot_password"))
    if request.method == "POST":
        pw, confirm = request.form.get("password", ""), request.form.get("confirm", "")
        problem = password_problem(pw, user.username)
        if pw != confirm:
            flash("The two passwords do not match.", "error")
        elif problem:
            flash(problem, "error")
        else:
            user.set_password(pw)
            db.session.commit()
            log_event("password_reset", username=user.username)
            flash("Password updated. You can log in now.", "success")
            return redirect(url_for("auth.login"))
    return render_template("reset.html", token=token)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@bp.route("/invite", methods=["GET", "POST"])
@role_required("instructor")
@limiter.limit("20 per hour", methods=["POST"])
def invite():
    """An instructor invites a colleague; only a signed, expiring, single-use link grants the role."""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not EMAIL_RE.match(email) or len(email) > 120:
            flash("Please enter a valid e-mail address.", "error")
        elif User.query.filter_by(email=email).first():
            flash("That e-mail already has an account.", "error")
        else:
            token = _invite_serializer().dumps({"e": email, "by": current_user.id})
            base = current_app.config["APP_BASE_URL"] or request.url_root.rstrip("/")
            link = f"{base}{url_for('auth.join', token=token)}"
            sent = send_invite_email(email, link, current_user.username)
            log_event("instructor_invited", detail=email)
            return render_template("invite.html", link=link, invited=email, sent=sent)
    return render_template("invite.html", link=None)


@bp.route("/join/<token>", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def join(token):
    data = load_invite(token)
    if not data:
        flash("This invitation is invalid, already used, or has expired. Ask an instructor for a new one.", "error")
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        pw, confirm = request.form.get("password", ""), request.form.get("confirm", "")
        problem = password_problem(pw, username)
        if not USERNAME_RE.match(username):
            flash("Username: 3-30 letters, digits or underscore.", "error")
        elif User.query.filter_by(username=username).first():
            flash("That username is taken. Please choose another.", "error")
        elif pw != confirm:
            flash("The two passwords do not match.", "error")
        elif problem:
            flash(problem, "error")
        else:
            u = User(username=username, email=data["e"], role="instructor")
            u.set_password(pw)
            db.session.add(u)
            db.session.commit()
            log_event("instructor_joined", username=username, detail=f"invited by user id {data.get('by')}")
            flash("Instructor account created. You can log in now.", "success")
            return redirect(url_for("auth.login"))
    return render_template("join.html", token=token, email=data["e"])
