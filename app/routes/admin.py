"""Password-protected account linking and manual YouTube submission page."""

import os
import re
import subprocess
import sys
from pathlib import Path

from flask import flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from app import app
from app.admin_security import admin_configured, admin_required, check_csrf, csrf_token, is_admin, password_version
from app.admin_store import (
    PODCASTS, add_submission, clear_login_attempts, login_blocked,
    recent_submissions, record_failed_login, set_submission_status,
)
from app.youtube_metadata import MetadataLookupError, get_youtube_video_metadata
from app.config import (
    TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, TIKTOK_TOKEN_FILE,
    YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_TOKEN_FILE,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
JOB_STAGES = {
    "download": ("Mengunduh video", 10),
    "curate": ("Memilih momen klip", 55),
    "cut": ("Merender klip dan subtitle", 75),
    "caption": ("Membuat caption", 90),
}


def _job_progress(item):
    status = item["status"]
    if status == "pending":
        return {"label": "Menunggu", "stage": "Dalam antrean", "percent": 0}
    if status == "completed":
        return {"label": "Selesai", "stage": "Selesai", "percent": 100}
    if status == "failed":
        return {"label": "Gagal", "stage": "Perlu diperiksa", "percent": 100}

    log_path = Path(os.environ.get("ADMIN_JOB_LOG_DIR", "/tmp/podcast-clips/jobs")) / f"{item['video_id']}.log"
    try:
        with log_path.open("rb") as log_file:
            log_file.seek(0, os.SEEK_END)
            log_file.seek(max(0, log_file.tell() - 65536))
            log_text = log_file.read().decode("utf-8", errors="replace")
    except OSError:
        log_text = ""

    stages = re.findall(r"^=== ([a-z_]+) ===\s*$", log_text, flags=re.MULTILINE)
    stage = stages[-1] if stages else None
    if stage == "download":
        match = re.search(r"^EPISODE_DIR=(.+)$", log_text, flags=re.MULTILINE)
        if match:
            episode_dir = Path(match.group(1).strip())
            if episode_dir.parent == Path("/tmp/podcast-clips"):
                if (episode_dir / "transcript.json").exists():
                    return {"label": "Berjalan", "stage": "Menyiapkan kurasi klip", "percent": 35}
                if (episode_dir / "source.mp4").exists():
                    return {"label": "Berjalan", "stage": "Mentranskripsikan audio", "percent": 25}
        return {"label": "Berjalan", "stage": "Mengunduh video", "percent": 10}
    if stage in JOB_STAGES:
        label, percent = JOB_STAGES[stage]
        return {"label": "Berjalan", "stage": label, "percent": percent}
    return {"label": "Berjalan", "stage": "Memulai proses", "percent": 3}


def _submissions_with_progress():
    rows = recent_submissions()
    for item in rows:
        item["progress"] = _job_progress(item)
    return rows


@app.after_request
def protect_admin_responses(response):
    if request.path.startswith("/admin") or request.path in ("/auth", "/oauth", "/tiktok-auth", "/tiktok-oauth"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "DENY"
    return response


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if not admin_configured():
        return "Admin login needs ADMIN_PASSWORD_HASH and FLASK_SECRET_KEY.", 503
    if is_admin():
        return redirect(url_for("admin_page"))
    if request.method == "POST":
        if not check_csrf():
            return "Invalid form token.", 400
        client_key = request.remote_addr or "unknown"
        if login_blocked(client_key):
            return "Too many login attempts. Try again later.", 429
        password = request.form.get("password", "")
        stored_hash = os.environ["ADMIN_PASSWORD_HASH"]
        try:
            valid_password = len(password) <= 1024 and check_password_hash(stored_hash, password)
        except ValueError:
            valid_password = False
        if valid_password:
            clear_login_attempts(client_key)
            session.clear()
            session["admin_authenticated"] = True
            session["admin_password_version"] = password_version()
            session.permanent = True
            csrf_token()
            return redirect(url_for("admin_page"))
        record_failed_login(client_key)
        flash("Incorrect password.", "error")
    return render_template("admin_login.html", csrf_token=csrf_token())


@app.route("/admin/logout", methods=["POST"])
@admin_required()
def admin_logout():
    if not check_csrf():
        return "Invalid form token.", 400
    session.clear()
    return redirect(url_for("admin_login"))


@app.route("/admin")
@admin_required()
def admin_page():
    return render_template(
        "admin.html",
        csrf_token=csrf_token(),
        podcasts=PODCASTS,
        submissions=_submissions_with_progress(),
        youtube_ready=bool(YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET),
        tiktok_ready=bool(TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET),
        youtube_connected=os.path.exists(YOUTUBE_TOKEN_FILE),
        tiktok_connected=os.path.exists(TIKTOK_TOKEN_FILE),
    )


@app.route("/admin/jobs")
@admin_required()
def admin_jobs():
    jobs = [
        {
            "video_id": item["video_id"],
            "status": item["status"],
            **item["progress"],
        }
        for item in _submissions_with_progress()
    ]
    return jsonify(jobs=jobs)


@app.route("/admin/youtube-metadata")
@admin_required()
def admin_youtube_metadata():
    try:
        metadata = get_youtube_video_metadata(request.args.get("url", ""))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except MetadataLookupError as exc:
        return jsonify(error=str(exc)), 502
    return jsonify(metadata)


@app.route("/admin/youtube-links", methods=["POST"])
@admin_required()
def admin_add_youtube_link():
    if not check_csrf():
        return "Invalid form token.", 400
    try:
        video_id = add_submission(
            request.form.get("url", ""),
            request.form.get("podcast", ""),
            request.form.get("title", ""),
        )
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        if request.form.get("action") == "process":
            log_dir = Path(os.environ.get("ADMIN_JOB_LOG_DIR", "/tmp/podcast-clips/jobs"))
            log_path = log_dir / f"{video_id}.log"
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
                set_submission_status(video_id, "in_progress")
                with log_path.open("ab") as output:
                    subprocess.Popen(
                        [sys.executable, str(REPO_ROOT / "scripts" / "process_episode.py"), video_id],
                        cwd=str(REPO_ROOT),
                        env={**os.environ, "PYTHONUNBUFFERED": "1"},
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
            except OSError:
                set_submission_status(video_id, "failed")
                app.logger.exception("Could not start episode processing worker")
                flash("Link disimpan, tetapi worker gagal dimulai. Periksa log server.", "error")
            else:
                flash(f"Pemrosesan video {video_id} dimulai. Status akan diperbarui di antrean.", "success")
        else:
            flash(f"Video {video_id} added to the pending queue.", "success")
    return redirect(url_for("admin_page"))
