"""Password-protected account linking and manual YouTube submission page."""

import os
import json
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from flask import flash, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash

from app import app
from app.admin_security import admin_configured, admin_required, check_csrf, csrf_token, is_admin, password_version
from app.admin_store import (
    PODCASTS, add_submission, clear_login_attempts, login_blocked,
    recent_submissions, record_failed_login, set_submission_status,
    record_youtube_upload, get_youtube_upload, record_manual_upload,
    deployed_clip_metadata,
)
from app.youtube_metadata import MetadataLookupError, get_youtube_video_metadata
from app.config import (
    TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, TIKTOK_TOKEN_FILE,
    YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_TOKEN_FILE,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
WORK_ROOT = Path("/tmp/podcast-clips")
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


def _compilation_episodes():
    episodes = []
    if not WORK_ROOT.is_dir():
        return episodes
    for workdir in sorted(WORK_ROOT.iterdir(), reverse=True):
        if not workdir.is_dir() or not (workdir / "source.mp4").is_file() or not (workdir / "transcript.json").is_file():
            continue
        title = workdir.name
        episode_data = workdir / "episode_data.json"
        if episode_data.is_file():
            try:
                title = json.loads(episode_data.read_text(encoding="utf-8")).get("episode_title") or title
            except (OSError, json.JSONDecodeError):
                pass
        episodes.append({"id": workdir.name, "title": title})
    return episodes


def _compilation_workdir(episode_id):
    if not episode_id or Path(episode_id).name != episode_id:
        return None
    workdir = WORK_ROOT / episode_id
    if not workdir.is_dir() or not (workdir / "source.mp4").is_file() or not (workdir / "transcript.json").is_file():
        return None
    return workdir


def _deployed_clip(podcast, episode, clip_num):
    if podcast not in PODCASTS or Path(episode).name != episode or not str(clip_num).isdigit():
        return None
    episode_dir = REPO_ROOT / "app" / "static" / "clips" / podcast / episode
    captions_path = episode_dir / "captions.json"
    if not episode_dir.is_dir() or not captions_path.is_file():
        return None
    try:
        captions = json.loads(captions_path.read_text(encoding="utf-8"))
        caption = captions[str(clip_num)]
        clip_path = episode_dir / str(caption["clip"])
        if clip_path.parent != episode_dir or not clip_path.is_file():
            return None
        return clip_path, caption
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _deployed_clips():
    root = REPO_ROOT / "app" / "static" / "clips"
    clips = []
    if not root.is_dir():
        return clips
    for podcast_dir in sorted(root.iterdir()):
        if not podcast_dir.is_dir() or podcast_dir.name not in PODCASTS:
            continue
        for episode_dir in sorted(podcast_dir.iterdir(), reverse=True):
            captions_path = episode_dir / "captions.json"
            if not episode_dir.is_dir() or not captions_path.is_file():
                continue
            try:
                captions = json.loads(captions_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(captions, dict):
                continue
            for clip_num, caption in captions.items():
                if not (str(clip_num).isdigit() and isinstance(caption, dict)):
                    continue
                clip_name = str(caption.get("clip", "") or "")
                if not (Path(clip_name).name == clip_name and (episode_dir / clip_name).is_file()):
                    continue
                manual_meta = deployed_clip_metadata(
                    podcast_dir.name, episode_dir.name, clip_num, caption=caption,
                )
                clips.append({
                    "podcast": podcast_dir.name,
                    "episode": episode_dir.name,
                    "clip_num": str(clip_num),
                    "title": caption.get("title", f"Clip {clip_num}"),
                    "upload": get_youtube_upload(podcast_dir.name, episode_dir.name, clip_num),
                    "manual": manual_meta,
                })
                # --- Auto-generate the companion .metadata.json next to the clip once, if missing
                # (so even without going through manual form, users can copy-paste directly from disk)
                if manual_meta and not (episode_dir / "captions.json").with_name(Path(manual_meta["video_filename"]).name + ".metadata.json").exists():
                    try:
                        clip_path = Path(manual_meta["video_path"])
                        out = clip_path.parent / (clip_path.name + ".metadata.json")
                        if not out.exists():
                            payload = {
                                "podcast": podcast_dir.name,
                                "episode": episode_dir.name,
                                "clip_num": int(clip_num) if str(clip_num).isdigit() else str(clip_num),
                                "source_mp4": manual_meta["video_filename"],
                                "source_mp4_size_bytes": manual_meta["file_size"],
                                "source_mp4_size_mb": manual_meta["file_size_mb"],
                                "source_mp4_sha256": manual_meta["sha256"],
                                "title": manual_meta["title"],
                                "title_plain": manual_meta["title_plain"],
                                "description": manual_meta["description"],
                                "tags": manual_meta["tags"],
                                "tags_csv": manual_meta["tags_csv"],
                                "category_id": 25,
                            }
                            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                    except OSError:
                        pass
    return clips


def _fallback_compilation_moments(transcript, limit=10):
    candidates = []
    index = 0
    while index < len(transcript) and len(candidates) < limit:
        try:
            start = float(transcript[index]["start"])
            end_index = index
            while end_index + 1 < len(transcript) and float(transcript[end_index]["end"]) - start < 45:
                end_index += 1
            end = float(transcript[end_index]["end"])
            if 35 <= end - start <= 75:
                text = str(transcript[index].get("text", "")).strip()
                candidates.append({
                    "start": start,
                    "end": end,
                    "title": text[:60] or f"Moment {len(candidates) + 1}",
                    "reason": "Kandidat otomatis dari rentang transcript; review sebelum build.",
                })
                index = end_index + 1
            else:
                index += 1
        except (KeyError, TypeError, ValueError):
            index += 1
    return candidates


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
        compilation_episodes=_compilation_episodes(),
        deployed_clips=_deployed_clips(),
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


@app.route("/admin/compilation/segments/<episode_id>")
@admin_required()
def admin_compilation_segments(episode_id):
    workdir = _compilation_workdir(episode_id)
    if workdir is None:
        return jsonify(error="Episode workspace not found."), 404
    try:
        transcript = json.loads((workdir / "transcript.json").read_text(encoding="utf-8"))
        segments = [
            {"start": float(segment["start"]), "end": float(segment["end"]), "text": str(segment["text"]).strip()}
            for segment in transcript
            if segment.get("text", "").strip()
        ]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return jsonify(error="Transcript is invalid."), 422
    return jsonify(segments=segments)


@app.route("/admin/compilation/moments", methods=["POST"])
@admin_required()
def admin_compilation_moments():
    payload = request.get_json(silent=True) or {}
    submitted_csrf = payload.get("csrf_token", "")
    expected_csrf = session.get("admin_csrf", "")
    if not expected_csrf or not isinstance(submitted_csrf, str) or not secrets.compare_digest(expected_csrf, submitted_csrf):
        return jsonify(error="Invalid form token."), 400
    workdir = _compilation_workdir(payload.get("episode_id"))
    if workdir is None:
        return jsonify(error="Episode workspace not found."), 404
    api_key = os.environ.get("HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY", "")
    if not api_key:
        return jsonify(error="LLM API key is not configured on the server."), 503
    try:
        transcript = json.loads((workdir / "transcript.json").read_text(encoding="utf-8"))
        transcript_lines = [
            f"[{float(segment['start']):.2f}-{float(segment['end']):.2f}] {segment['text']}"
            for segment in transcript
            if str(segment.get("text", "")).strip()
        ]
        transcript_text = "\n".join(transcript_lines)
        max_prompt_chars = 60000
        if len(transcript_text) > max_prompt_chars:
            step = max(1, len(transcript_lines) // 600)
            sampled_lines = transcript_lines[::step]
            transcript_text = "\n".join(sampled_lines)[:max_prompt_chars]
        prompt = (
            "Pilih maksimal 10 momen punchline dari transkrip podcast Indonesia berikut untuk kompilasi TOP 5. "
            "Kembalikan HANYA JSON array berisi objek start, end, title, reason. "
            "Start/end harus tepat pada batas segmen, durasi 35-75 detik, jangan mengarang konteks.\n\n"
            + transcript_text
        )
        request_body = json.dumps({
            "model": "MiniMax-M2.7-highspeed",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }).encode()
        llm_request = urllib.request.Request(
            "https://ai.sumopod.com/v1/chat/completions",
            data=request_body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(llm_request, timeout=20) as response:
            content = json.load(response)["choices"][0]["message"]["content"].strip()
        candidates = json.loads(content[content.index("["):content.rindex("]") + 1])
        normalized = []
        for candidate in candidates[:10]:
            start = float(candidate["start"])
            end = float(candidate["end"])
            if start < 0 or end <= start or end - start < 35 or end - start > 75:
                continue
            normalized.append({
                "start": start,
                "end": end,
                "title": str(candidate.get("title", "Moment"))[:60],
                "reason": str(candidate.get("reason", ""))[:160],
            })
        return jsonify(candidates=normalized)
    except TimeoutError:
        fallback = _fallback_compilation_moments(transcript)
        return jsonify(candidates=fallback, source="transcript-fallback", warning="LLM timeout; kandidat dibuat dari transcript.")
    except (OSError, urllib.error.URLError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        app.logger.warning("Compilation moment suggestion failed: %s", exc)
        return jsonify(error="Moment suggestions could not be generated."), 502


@app.route("/admin/compilation/build", methods=["POST"])
@admin_required()
def admin_compilation_build():
    payload = request.get_json(silent=True) or {}
    submitted_csrf = payload.get("csrf_token", "")
    expected_csrf = session.get("admin_csrf", "")
    if not expected_csrf or not isinstance(submitted_csrf, str) or not secrets.compare_digest(expected_csrf, submitted_csrf):
        return jsonify(error="Invalid form token."), 400
    workdir = _compilation_workdir(payload.get("episode_id"))
    items = payload.get("items")
    if workdir is None or not isinstance(items, list) or len(items) != 5:
        return jsonify(error="Choose an episode and exactly five moments."), 400
    try:
        normalized_items = []
        segments = []
        for index, item in enumerate(items):
            start = float(item["start"])
            end = float(item["end"])
            label = str(item.get("label", "")).strip()[:60]
            if not label or start < 0 or end <= start:
                raise ValueError
            normalized_items.append({"label": label})
            segments.append([start, end, index])
    except (KeyError, TypeError, ValueError):
        return jsonify(error="Each moment needs valid start, end, and label values."), 400

    job_id = f"compilation-{workdir.name}-{int(time.time())}"
    config_path = workdir / f".{job_id}.json"
    output_path = workdir / "clips" / "top5_compilation.mp4"
    config_path.write_text(json.dumps({
        "header_l1": str(payload.get("header_l1", "TOP 5 MOMEN"))[:80],
        "header_l2": str(payload.get("header_l2", ""))[:80],
        "subheader": str(payload.get("subheader", ""))[:120],
        "items": normalized_items,
        "segs": segments,
    }, ensure_ascii=False), encoding="utf-8")
    log_dir = Path(os.environ.get("ADMIN_JOB_LOG_DIR", "/tmp/podcast-clips/jobs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{job_id}.log"
    command = [sys.executable, str(REPO_ROOT / "scripts" / "build_compilation.py"), str(workdir), str(output_path), str(config_path), str(log_path)]
    try:
        subprocess.Popen(command, cwd=str(REPO_ROOT), start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env={**os.environ, "PYTHONUNBUFFERED": "1"})
    except OSError:
        return jsonify(error="Could not start compilation worker."), 503
    return jsonify(job_id=job_id, output=f"/admin/compilation/preview/{workdir.name}")


@app.route("/admin/compilation/preview/<episode_id>")
@admin_required()
def admin_compilation_preview(episode_id):
    workdir = _compilation_workdir(episode_id)
    output_path = workdir / "clips" / "top5_compilation.mp4" if workdir else None
    if output_path is None or not output_path.is_file():
        return "Compilation not ready", 404
    return send_file(output_path, mimetype="video/mp4", conditional=True)


@app.route("/admin/compilation/status/<episode_id>")
@admin_required()
def admin_compilation_status(episode_id):
    workdir = _compilation_workdir(episode_id)
    if workdir is None:
        return jsonify(status="invalid"), 404
    output_path = workdir / "clips" / "top5_compilation.mp4"
    if output_path.is_file():
        return jsonify(status="completed", preview=url_for("admin_compilation_preview", episode_id=episode_id))
    log_files = sorted(Path(os.environ.get("ADMIN_JOB_LOG_DIR", "/tmp/podcast-clips/jobs")).glob(f"compilation-{episode_id}-*.log"))
    if log_files:
        log_text = log_files[-1].read_text(encoding="utf-8", errors="replace")
        if "EXIT_CODE=" in log_text and not log_text.rstrip().endswith("EXIT_CODE=0"):
            return jsonify(status="failed")
        return jsonify(status="running")
    return jsonify(status="idle")


@app.route("/admin/clips/<podcast>/<episode>/<clip_num>/upload", methods=["POST"])
@admin_required()
def admin_upload_clip(podcast, episode, clip_num):
    if not check_csrf():
        return "Invalid form token.", 400
    clip = _deployed_clip(podcast, episode, clip_num)
    if clip is None:
        flash("Clip or caption not found.", "error")
        return redirect(url_for("admin_page"))
    existing = get_youtube_upload(podcast, episode, clip_num)
    if existing and existing.get("status") in ("uploaded", "manual"):
        vid = existing.get("video_id") or ""
        flash(f"Clip sudah di-upload ({existing['status']}): {vid}", "success")
        return redirect(url_for("admin_page"))
    clip_path, caption = clip
    meta = deployed_clip_metadata(podcast, episode, clip_num, caption=caption, clip_path=clip_path) or {}
    record_youtube_upload(
        podcast, episode, clip_num, "uploading",
        sha256=meta.get("sha256", ""), file_size=meta.get("file_size", 0),
    )
    tags = ["shorts", "podcast", "indonesia", podcast, "tempo", "berita", "politik", "viral", "fyp", "news", "video", "opini", "analisis", "terkini", "podcastindonesia"]
    try:
        from scripts.youtube_upload import upload_clip
        result = upload_clip(clip_path, caption.get("title", f"Clip {clip_num}"), caption.get("caption", "").strip(), tags=tags)
        if "error" in result:
            message = result["error"]
            status = "quota" if "uploadLimitExceeded" in message else "failed"
            record_youtube_upload(podcast, episode, clip_num, status, error=message,
                                  sha256=meta.get("sha256", ""), file_size=meta.get("file_size", 0))
            flash("Kuota harian habis — retry besok." if status == "quota" else f"Upload gagal: {message}", "error")
        else:
            record_youtube_upload(podcast, episode, clip_num, "uploaded", video_id=result.get("video_id"),
                                  sha256=meta.get("sha256", ""), file_size=meta.get("file_size", 0))
            flash(f"Upload berhasil: {result.get('url', result.get('video_id', ''))}", "success")
    except Exception as exc:
        message = str(exc)
        status = "quota" if "uploadLimitExceeded" in message else "failed"
        record_youtube_upload(podcast, episode, clip_num, status, error=message,
                              sha256=meta.get("sha256", ""), file_size=meta.get("file_size", 0))
        flash("Kuota harian habis — retry besok." if status == "quota" else f"Upload gagal: {message}", "error")
    return redirect(url_for("admin_page"))


@app.route("/admin/clips/<podcast>/<episode>/<clip_num>/manual-upload", methods=["POST"])
@admin_required()
def admin_manual_upload_clip(podcast, episode, clip_num):
    if not check_csrf():
        return "Invalid form token.", 400
    clip_info = _deployed_clip(podcast, episode, clip_num)
    if clip_info is None:
        flash("Clip or caption not found.", "error")
        return redirect(url_for("admin_page"))
    clip_path, caption = clip_info
    video_id_or_url = (request.form.get("video_id") or "").strip()
    reviewer = "admin"
    existing = get_youtube_upload(podcast, episode, clip_num)
    if existing and existing.get("status") == "uploaded":
        flash(f"Clip already uploaded via API: {existing.get('video_id','')}", "error")
        return redirect(url_for("admin_page"))
    meta = deployed_clip_metadata(podcast, episode, clip_num, caption=caption, clip_path=clip_path) or {}
    try:
        saved_id = record_manual_upload(
            podcast, episode, clip_num, video_id_or_url, reviewed_by=reviewer,
            sha256=meta.get("sha256", ""), file_size=meta.get("file_size", 0),
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin_page"))
    # Write the companion metadata JSON next to the clip so the file, description hash never
    # diverge from the Studio copy.
    try:
        json_out = Path(clip_path).with_suffix(Path(clip_path).suffix + ".metadata.json")
        payload = {
            "podcast": podcast,
            "episode": episode,
            "clip_num": int(clip_num) if str(clip_num).isdigit() else str(clip_num),
            "video_id": saved_id,
            "youtube_url": f"https://www.youtube.com/watch?v={saved_id}",
            "youtube_short_url": f"https://youtu.be/{saved_id}",
            "youtube_studio_url": f"https://studio.youtube.com/video/{saved_id}/edit",
            "upload_method": "manual_youtube_studio",
            "reviewed_by": reviewer,
            "title": meta.get("title", ""),
            "title_plain": meta.get("title_plain", ""),
            "description": meta.get("description", ""),
            "tags": meta.get("tags", []),
            "tags_csv": meta.get("tags_csv", ""),
            "category_id": 25,
            "source_mp4": meta.get("video_filename", ""),
            "source_mp4_size_bytes": meta.get("file_size", 0),
            "source_mp4_size_mb": meta.get("file_size_mb", 0),
            "source_mp4_sha256": meta.get("sha256", ""),
            "created_at_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        }
        json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    flash(f"Manual upload dicatat: https://youtu.be/{saved_id}", "success")
    return redirect(url_for("admin_page"))


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
