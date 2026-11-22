import click
from flask import Flask, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import Config
from .extensions import csrf, db, limiter, login_manager

CSP = ("default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
       "style-src 'self'; img-src 'self' data:; "
       "media-src 'self' blob: https://*.amazonaws.com; connect-src 'self' https://*.amazonaws.com; "
       "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


def fmt_duration(seconds):
    if not seconds:
        return ""
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def create_app(config_object=Config):
    app = Flask(__name__)
    app.config.from_object(config_object)
    if app.config.get("BEHIND_PROXY"):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = None

    from .auth import bp as auth_bp
    from .models import User
    from .routes import bp as main_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.jinja_env.filters["duration"] = fmt_duration
    app.jinja_env.filters["filesize"] = fmt_size

    @app.context_processor
    def pending_badge():
        from flask_login import current_user
        from .models import AccessRequest
        if current_user.is_authenticated and current_user.role == "instructor":
            return {"pending_requests": AccessRequest.query.filter_by(status="pending").count()}
        return {"pending_requests": 0}

    @login_manager.user_loader
    def load_user(uid):
        return db.session.get(User, int(uid))

    @app.after_request
    def security_headers(resp):
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        if resp.mimetype == "text/html":
            resp.headers["Cache-Control"] = "no-store"  # pages embed short-lived signed URLs
        if app.config["SESSION_COOKIE_SECURE"]:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000"
        return resp

    def _error(code, msg):
        if request.path.startswith("/api/"):
            return jsonify(error=msg), code
        return render_template("error.html", code=code, msg=msg), code

    app.register_error_handler(403, lambda e: _error(403, "Your role is not allowed to do this."))
    app.register_error_handler(404, lambda e: _error(404, "We could not find that page."))
    app.register_error_handler(429, lambda e: _error(429, "Too many attempts. Please wait a bit and try again."))
    app.register_error_handler(400, lambda e: _error(400, "Bad request (expired form or invalid input). Reload the page and retry."))

    with app.app_context():
        db.create_all()
        from .migrate import ensure_schema
        ensure_schema(db)

    @app.cli.command("create-user")
    @click.argument("username")
    @click.option("--role", type=click.Choice(["viewer", "instructor"]), default="viewer")
    @click.option("--email", default=None, help="needed for 'forgot password'")
    @click.password_option()
    def create_user(username, role, email, password):
        """flask --app wsgi create-user alice --role instructor --email a@x.com"""
        if User.query.filter_by(username=username).first():
            raise click.ClickException("user exists")
        u = User(username=username, role=role, email=email.lower() if email else None)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        click.echo(f"created {role} '{username}'")

    @app.cli.command("set-email")
    @click.argument("username")
    @click.argument("email")
    def set_email(username, email):
        """flask --app wsgi set-email teacher1 me@example.com"""
        u = User.query.filter_by(username=username).first()
        if not u:
            raise click.ClickException("no such user")
        u.email = email.lower()
        db.session.commit()
        click.echo(f"email set for {username}")

    @app.cli.command("reset-password")
    @click.argument("username")
    @click.password_option()
    def reset_password_cli(username, password):
        """Admin reset: flask --app wsgi reset-password teacher1"""
        u = User.query.filter_by(username=username).first()
        if not u:
            raise click.ClickException("no such user")
        u.set_password(password)
        db.session.commit()
        click.echo("password updated")

    return app
