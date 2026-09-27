"""Admin session, CSRF, and private token file helpers."""

import json
import hashlib
import os
import secrets
import tempfile
from functools import wraps
from pathlib import Path

from flask import abort, redirect, request, session, url_for


def admin_configured():
    return bool(os.environ.get("ADMIN_PASSWORD_HASH") and os.environ.get("FLASK_SECRET_KEY"))


def password_version():
    return hashlib.sha256(os.environ["ADMIN_PASSWORD_HASH"].encode()).hexdigest()


def is_admin():
    return bool(
        admin_configured()
        and session.get("admin_authenticated") is True
        and session.get("admin_password_version")
        and secrets.compare_digest(session["admin_password_version"], password_version())
    )


def admin_required(callback=False):
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not is_admin():
                if callback:
                    abort(403)
                return redirect(url_for("admin_login"))
            return view(*args, **kwargs)
        return wrapped
    return decorate


def csrf_token():
    if "admin_csrf" not in session:
        session["admin_csrf"] = secrets.token_urlsafe(32)
    return session["admin_csrf"]


def check_csrf():
    expected = session.get("admin_csrf")
    submitted = request.form.get("csrf_token")
    return bool(expected and submitted and secrets.compare_digest(expected, submitted))


def write_private_json(path, data):
    """Replace a token only after its complete JSON is safely written."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".token-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(data, output)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
