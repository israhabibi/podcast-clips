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
    record_youtube_upload, claim_youtube_upload, get_youtube_upload, record_manual_upload,
    deployed_clip_metadata, get_setting, set_setting, delete_setting,
    get_llm_config, save_llm_config,
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
DEFAULT_PER_PAGE = 10
ALLOWED_PER_PAGE = (5, 10, 25, 50, 100)
UI_SELECT_PER_PAGE = (10, 25, 50, 100)

_TOKEN_CHECK_CACHE = {"ts": 0.0, "youtube_valid": None, "youtube_token_seen": None}
_TOKEN_CHECK_CACHE_TTL_SECONDS = 90


def _try_check_youtube_token_valid():
    """Non-blocking sanity check: coba refresh credentials YouTube.

    Returns tuple (bool_valid, status_msg_short). Tidak throw — gagal network/tidak terpasang lib
    diabaikan agar admin page tidak crash 500 karena network buruk.

    Hasil di-cache selama TTL supaya admin page GET berikutnya tidak nembak Google terus.
    """
    token_path = Path(YOUTUBE_TOKEN_FILE) if YOUTUBE_TOKEN_FILE else None
    if not (token_path and token_path.exists()):
        return False, "no_token"

    global _TOKEN_CHECK_CACHE
    now_ts = time.time()
    last_check = _TOKEN_CHECK_CACHE["ts"] or 0.0
    if last_check and (now_ts - last_check) <= _TOKEN_CHECK_CACHE_TTL_SECONDS:
        seen_cached = _TOKEN_CHECK_CACHE["youtube_token_seen"]
        if seen_cached and token_path.stat().st_mtime < last_check:
            # File tidak berubah sejak cek terakhir, aman reuse cache.
            return bool(_TOKEN_CHECK_CACHE["youtube_valid"]), "cached"

    # Quick check by stat: pastikan bukan 0 byte / JSON kosong (yang jelas invalid).
    try:
        stat = token_path.stat()
        if stat.st_size <= 8:
            _TOKEN_CHECK_CACHE.update({"ts": now_ts, "youtube_valid": False, "youtube_token_seen": str(token_path)})
            return False, "token_empty"
    except OSError:
        return False, "stat_error"

    try:
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request as GoogleRequest
        except Exception:
            return True, "skip_no_libs"  # Tidak bisa cek, anggap OK agar tidak bikin banner salah
        try:
            creds = Credentials.from_authorized_user_file(
                str(token_path), ["https://www.googleapis.com/auth/youtube"]
            )
        except (OSError, ValueError):
            _TOKEN_CHECK_CACHE.update({"ts": now_ts, "youtube_valid": False, "youtube_token_seen": str(token_path)})
            return False, "token_unreadable"
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                try:
                    creds.refresh(GoogleRequest())
                except Exception as exc:
                    hay = f"{type(exc).__name__} {str(exc)}".lower()
                    invalid = ("invalid_grant" in hay or "expired or revoked" in hay
                               or "revoked" in hay or "refresh_token" in hay and "invalid" in hay)
                    try:
                        for a in getattr(exc, "args", []) or []:
                            if isinstance(a, str) and ("invalid_grant" in a or "expired or revoked" in a.lower()):
                                invalid = True
                    except Exception:
                        pass
                    if invalid:
                        # Auto-wipe invalid cached token agar button reconnect muncul.
                        try:
                            token_path.unlink()
                        except OSError:
                            pass
                        _TOKEN_CHECK_CACHE.update({"ts": now_ts, "youtube_valid": False, "youtube_token_seen": str(token_path)})
                        return False, "invalid_grant"
                    # Network error / auth error lain: anggap valid-tidak-bisa-dicek, jangan ganggu UI.
                    _TOKEN_CHECK_CACHE.update({"ts": now_ts, "youtube_valid": True, "youtube_token_seen": str(token_path)})
                    return True, "refresh_error_unclassified"
            else:
                return True, "no_refresh_token_present"
        # Cek cepat selesai, credential tetap VALID.
        _TOKEN_CHECK_CACHE.update({"ts": now_ts, "youtube_valid": True, "youtube_token_seen": str(token_path)})
        return True, "valid"
    except Exception:
        return True, "check_error_skipped"  # jangan ganggu halaman admin kalau ada error tak terduga


def _coerce_int(raw, default, minimum=1):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if value < minimum:
        return minimum
    return value


def _build_url_for_page(endpoint, page, per_page, extra_args=None, page_key="page", per_key="per_page"):
    args = {}
    if extra_args:
        args.update({k: v for k, v in extra_args.items() if v not in (None, "", [])})
    args[page_key] = int(page)
    args[per_key] = int(per_page)
    return url_for(endpoint, **args)


def _paginate(items, page, per_page, endpoint, base_args=None, page_key="page", per_key="per_page"):
    """Slices items list (small, in-memory OK for 100-2000 rows) into page dict.

    Returns dict ready for Jinja render with:
      items, page, per_page, total, total_pages,
      has_prev, has_next, prev_url, next_url,
      pages (list of int|None for smart ellipsis),
      first_url, last_url,
      start_idx, end_idx, total.
    """
    total = len(items)
    per_page = per_page if per_page in ALLOWED_PER_PAGE else DEFAULT_PER_PAGE
    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    paged_items = items[start_idx:end_idx]

    # Build page number list with smart ellipsis (None = gap)
    pages = []
    if total_pages <= 7:
        pages = list(range(1, total_pages + 1))
    else:
        # Always show first, last, current ± 1; fill gaps with None
        def add_unique(xs, val):
            if xs and xs[-1] is None and val is None:
                return
            xs.append(val)
        window = sorted({1, total_pages, page - 1, page, page + 1})
        prev = 0
        for p in window:
            if p < 1 or p > total_pages:
                continue
            if prev and p - prev > 1:
                add_unique(pages, None)
            add_unique(pages, p)
            prev = p

    has_prev = page > 1
    has_next = page < total_pages
    prev_url = _build_url_for_page(endpoint, page - 1, per_page, base_args, page_key, per_key) if has_prev else None
    next_url = _build_url_for_page(endpoint, page + 1, per_page, base_args, page_key, per_key) if has_next else None
    first_url = _build_url_for_page(endpoint, 1, per_page, base_args, page_key, per_key) if page != 1 else None
    last_url = _build_url_for_page(endpoint, total_pages, per_page, base_args, page_key, per_key) if page != total_pages else None

    return {
        "items": paged_items,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "has_prev": has_prev,
        "has_next": has_next,
        "prev_url": prev_url,
        "next_url": next_url,
        "first_url": first_url,
        "last_url": last_url,
        "pages": pages,
        "allowed_per_page": tuple(UI_SELECT_PER_PAGE),
        "page_key": page_key,
        "per_key": per_key,
        # For "Menampilkan X-Y dari Z"
        "start_idx": 0 if total == 0 else start_idx + 1,
        "end_idx": min(end_idx, total),
    }


def _job_progress(item):
    status = item["status"]
    if status == "pending":
        return {"label": "Menunggu", "stage": "Dalam antrean", "percent": 0}
    if status == "completed":
        return {"label": "Selesai", "stage": "Selesai", "percent": 100}
    if status == "ready_for_review":
        return {"label": "Siap direview", "stage": "Klip lokal siap direview", "percent": 100}
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


def _group_submissions_by_video(submission_items):
    groups = {}
    order = []
    for submission in submission_items:
        vid = submission.get("video_id") or ""
        if not vid:
            continue
        if vid not in groups:
            title = (submission.get("title") or "").strip()
            group = {
                "video_id": vid,
                "video_url": submission.get("url") or f"https://www.youtube.com/watch?v={vid}",
                "podcast": submission.get("podcast") or "",
                "title": title or f"Episode YouTube {vid}",
                "submissions": [],
                "first_created": submission.get("created_at") or "",
                "last_created": submission.get("created_at") or "",
                "worst_status_weight": -1,
                "worst_status": submission.get("status") or "pending",
                "worst_progress": submission.get("progress") or {"stage": "-", "label": "Tunggu", "percent": 0},
                "running_count": 0,
                "review_count": 0,
                "completed_count": 0,
                "failed_count": 0,
                "pending_count": 0,
                "max_percent": 0,
            }
            groups[vid] = group
            order.append(vid)
        g = groups[vid]
        status = submission.get("status") or "pending"
        progress = submission.get("progress") or {"stage": "-", "label": "-", "percent": 0}
        weight = {
            "in_progress": 100,
            "pending": 50,
            "failed": 25,
            "ready_for_review": 10,
            "completed": 0,
        }.get(status, 10)
        if weight > g["worst_status_weight"]:
            g["worst_status_weight"] = weight
            g["worst_status"] = status
            g["worst_progress"] = progress
        g["max_percent"] = max(g["max_percent"], int(progress.get("percent") or 0))
        if status == "in_progress":
            g["running_count"] += 1
        elif status == "ready_for_review":
            g["review_count"] += 1
        elif status == "completed":
            g["completed_count"] += 1
        elif status == "failed":
            g["failed_count"] += 1
        else:
            g["pending_count"] += 1
        ca = submission.get("created_at") or ""
        if ca and ca < (g["first_created"] or ca):
            g["first_created"] = ca
        if ca and ca > (g["last_created"] or ca):
            g["last_created"] = ca
        g["submissions"].append(submission)
    # Sort: active work first, then failures/review, then legacy completed rows.
    STATUS_ORDER = {
        "in_progress": 0, "pending": 1, "failed": 2,
        "ready_for_review": 3, "completed": 4,
    }
    def sort_key(vid):
        g = groups[vid]
        return (STATUS_ORDER.get(g["worst_status"], 9), -(g["running_count"]), g["last_created"] or "")
    ordered_vids = sorted(order, key=sort_key)
    # Final aggregate status label per group
    STATUS_LABEL = {
        "in_progress": ("Berjalan", "chip chip-warning"),
        "pending":     ("Antrean",  "chip chip-info"),
        "failed":      ("Gagal",    "chip chip-danger"),
        "ready_for_review": ("Siap direview", "chip chip-info"),
        "completed":   ("Selesai",  "chip chip-success"),
    }
    out_groups = []
    for vid in ordered_vids:
        g = groups[vid]
        # Mirror "items" key (for template {{ group.items|length }}) WITHOUT shadowing dict.items() method:
        # NEVER use g["items"] / g.items hybrid via same name while iterating dict keys via .items();
        # instead build new_group cleanly using dedicated "items" key only after all dict-method iterations are done.
        submission_list = list(g["submissions"])
        label, cls = STATUS_LABEL.get(g["worst_status"], STATUS_LABEL["pending"])
        rc = g["running_count"]; vc = g["review_count"]; cc = g["completed_count"]; fc = g["failed_count"]; pc = g["pending_count"]
        if rc and not vc and not cc and not fc and not pc:
            label = f"Berjalan ({rc})"
        elif g["worst_status"] == "completed":
            label = f"Selesai ({cc})"
        elif g["worst_status"] == "pending":
            label = f"Antrean ({pc})"
        elif g["worst_status"] == "failed":
            label = f"Gagal ({fc})"
        elif g["worst_status"] == "ready_for_review":
            label = f"Siap direview ({vc})"
        else:
            totals = rc + pc
            if totals > 1:
                label = f"{label} ({totals})"
        counters = []
        if rc: counters.append(f"{rc} berjalan")
        if pc: counters.append(f"{pc} antre")
        if vc: counters.append(f"{vc} siap direview")
        if cc: counters.append(f"{cc} selesai")
        if fc: counters.append(f"{fc} gagal")
        new_group = {
            "video_id": g["video_id"], "video_url": g["video_url"], "podcast": g["podcast"],
            "title": g["title"],
            "first_created": g["first_created"], "last_created": g["last_created"],
            "worst_status": g["worst_status"], "worst_progress": dict(g["worst_progress"]),
            "running_count": rc, "review_count": vc, "completed_count": cc,
            "failed_count": fc, "pending_count": pc,
            "max_percent": g["max_percent"],
            "group_status_label": label, "group_status_class": cls, "counters": counters,
            "items": submission_list,
        }
        out_groups.append(new_group)
    return out_groups


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


def _compilation_lock_path(workdir):
    return workdir / ".compilation-build.lock"


def _compilation_is_locked(workdir):
    """Return True for an active build; discard locks older than six hours."""
    lock_path = _compilation_lock_path(workdir)
    try:
        if time.time() - lock_path.stat().st_mtime > 6 * 60 * 60:
            lock_path.unlink()
            return False
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _acquire_compilation_lock(workdir, job_id):
    lock_path = _compilation_lock_path(workdir)
    if _compilation_is_locked(workdir):
        return None
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return None
    with os.fdopen(descriptor, "w", encoding="utf-8") as lock_file:
        lock_file.write(job_id)
        lock_file.flush()
        os.fsync(lock_file.fileno())
    return lock_path


TOP5_CLIP_NUM = "99"  # Reserved clip_num slot for Top 5 compilation


def _episode_meta_from_workdir(workdir):
    """Return dict with podcast_slug, episode_date, episode_title, episode_slug.

    Baca dari episode_data.json (kaya). Fallback parse dari workdir name atau waktu sekarang.
    """
    if workdir is None:
        return None
    podcast = "unknown"
    date_str = time.strftime("%Y-%m-%d", time.gmtime(time.time()))
    title = workdir.name
    slug = re.sub(r"[^a-z0-9]+", "-", str(title).lower()).strip("-") or "episode"
    episode_data = workdir / "episode_data.json"
    if episode_data.is_file():
        try:
            meta = json.loads(episode_data.read_text(encoding="utf-8"))
            if isinstance(meta.get("podcast_slug"), str) and meta["podcast_slug"].strip():
                podcast = meta["podcast_slug"].strip()
            if isinstance(meta.get("episode_title"), str) and meta["episode_title"].strip():
                title = meta["episode_title"].strip()
            if isinstance(meta.get("episode_slug"), str) and meta["episode_slug"].strip():
                slug = meta["episode_slug"].strip()
            if isinstance(meta.get("episode_date"), str) and meta["episode_date"].strip():
                date_str = meta["episode_date"].strip()
        except (OSError, json.JSONDecodeError):
            pass
    if podcast not in PODCASTS:
        # Fallback: coba infer dari nama workdir
        for allowed in PODCASTS:
            if allowed in workdir.name.lower():
                podcast = allowed
                break
    return {
        "podcast": podcast if podcast in PODCASTS else list(PODCASTS)[0] if PODCASTS else "unknown",
        "date": date_str,
        "title": title,
        "slug": slug,
    }


def _slug_filename_part(text, maxlen=80):
    clean = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    if len(clean) > maxlen:
        clean = clean[:maxlen].rstrip("-")
    return clean


def _top5_filename(meta, suffix=".mp4"):
    """Nama file deploy untuk kompilasi Top 5: {podcast}_{date}_{slug}_top5.mp4

    Clip number top5 di-representasi sebagai "top5" literal di filename supaya
    mudah dibedakan dari per-klip NN. YouTube upload registration pakai TOP5_CLIP_NUM=99.
    """
    podcast_s = _slug_filename_part(meta["podcast"], 30)
    title_s = _slug_filename_part(meta["slug"], 60)
    base = f"{podcast_s}_{meta['date']}_{title_s}_top5"
    if len(base) > 180:
        title_s = _slug_filename_part(meta["slug"], max(10, 180 - (len(podcast_s) + len(meta["date"]) + 10)))
        base = f"{podcast_s}_{meta['date']}_{title_s}_top5"
    return base + suffix


def _top5_deployed_dir_and_file(meta):
    """Return tuple (deployed_episode_dir: Path, deployed_file_path: Path) in static clips."""
    podcast = meta["podcast"] if meta["podcast"] in PODCASTS else "unknown"
    episode_folder = f"{meta['date']}_{_slug_filename_part(meta['slug'], 80)}"
    deploy_dir = REPO_ROOT / "app" / "static" / "clips" / podcast / episode_folder
    filename = _top5_filename(meta)
    return deploy_dir, deploy_dir / filename


def _compilation_preview_path(episode_id):
    workdir = _compilation_workdir(episode_id)
    return workdir / "clips" / "top5_compilation.mp4" if workdir else None


def _compilation_deployed_path(episode_id, workdir=None):
    if workdir is None:
        workdir = _compilation_workdir(episode_id)
    if workdir is None:
        return None
    meta = _episode_meta_from_workdir(workdir)
    if not meta:
        return None
    _, dest = _top5_deployed_dir_and_file(meta)
    return dest if dest.is_file() else None


def _compilation_upload_status(episode_id, workdir=None):
    if workdir is None:
        workdir = _compilation_workdir(episode_id)
    if workdir is None:
        return None
    meta = _episode_meta_from_workdir(workdir)
    if not meta:
        return None
    podcast = meta["podcast"]
    episode_folder = f"{meta['date']}_{_slug_filename_part(meta['slug'], 80)}"
    return get_youtube_upload(podcast, episode_folder, TOP5_CLIP_NUM)


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
            # Fallback kompilasi top5: 8-12 detik per item, total max 60.
            while end_index + 1 < len(transcript) and float(transcript[end_index]["end"]) - start < 10:
                end_index += 1
            end = float(transcript[end_index]["end"])
            if 6 <= end - start <= 14:
                text = str(transcript[index].get("text", "")).strip()
                candidates.append({
                    "start": start,
                    "end": end,
                    "duration": round(end - start, 1),
                    "title": text[:60] or f"Moment {len(candidates) + 1}",
                    "reason": "Kandidat otomatis dari transcript; review durasi dan punchline sebelum build.",
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
    clips_page = _coerce_int(request.args.get("clip_page"), 1)
    clips_per_page = _coerce_int(request.args.get("clip_per_page"), DEFAULT_PER_PAGE)
    sub_page = _coerce_int(request.args.get("sub_page"), 1)
    sub_per_page = _coerce_int(request.args.get("sub_per_page"), DEFAULT_PER_PAGE)

    all_clips_full = _deployed_clips()
    # Keep failed/quota rows visible so the operator has a retry path. Only
    # confirmed API/manual uploads are removed from the ready-to-upload panel.
    all_clips = [
        c for c in all_clips_full
        if not c.get("upload") or c["upload"].get("status") not in ("uploaded", "manual")
    ]
    total_deployed_clips_count = len(all_clips_full)
    hidden_already_uploaded = total_deployed_clips_count - len(all_clips)
    clips_base_args = {
        "sub_page": sub_page if sub_page != 1 else None,
        "sub_per_page": sub_per_page if sub_per_page != DEFAULT_PER_PAGE else None,
    }
    clips_pager = _paginate(
        all_clips, clips_page, clips_per_page, "admin_page",
        base_args=clips_base_args,
        page_key="clip_page", per_key="clip_per_page",
    )
    clips_pager["endpoint"] = "admin_page"
    clips_pager["other_page_key"] = "sub_page"
    clips_pager["other_per_key"] = "sub_per_page"
    clips_pager["other_page_val"] = sub_page
    clips_pager["other_per_val"] = sub_per_page

    all_subs = _submissions_with_progress()
    grouped_subs = _group_submissions_by_video(all_subs)

    sub_base_args = {
        "clip_page": clips_page if clips_page != 1 else None,
        "clip_per_page": clips_per_page if clips_per_page != DEFAULT_PER_PAGE else None,
    }
    sub_pager = _paginate(
        grouped_subs, sub_page, sub_per_page, "admin_page",
        base_args=sub_base_args,
        page_key="sub_page", per_key="sub_per_page",
    )
    sub_pager["endpoint"] = "admin_page"
    sub_pager["other_page_key"] = "clip_page"
    sub_pager["other_per_key"] = "clip_per_page"
    sub_pager["other_page_val"] = clips_page
    sub_pager["other_per_val"] = clips_per_page
    sub_pager["total_items_flat"] = len(all_subs)

    youtube_connected_file = os.path.exists(YOUTUBE_TOKEN_FILE)
    yt_token_valid, yt_check_status = True, "skip_no_check"
    if youtube_connected_file:
        try:
            yt_token_valid, yt_check_status = _try_check_youtube_token_valid()
        except Exception:
            yt_token_valid, yt_check_status = True, "check_exception_ignored"
    youtube_token_expired = youtube_connected_file and (not yt_token_valid)
    youtube_connected_display = bool(youtube_connected_file and yt_token_valid)
    llm_cfg = get_llm_config(include_key=False)

    return render_template(
        "admin.html",
        csrf_token=csrf_token(),
        active_tab="dashboard",
        podcasts=PODCASTS,
        submissions=sub_pager["items"],
        submissions_pager=sub_pager,
        compilation_episodes=_compilation_episodes(),
        deployed_clips=clips_pager["items"],
        clips_pager=clips_pager,
        youtube_ready=bool(YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET),
        tiktok_ready=bool(TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET),
        youtube_connected=youtube_connected_display,
        youtube_token_expired=youtube_token_expired,
        youtube_token_check_status=str(yt_check_status),
        tiktok_connected=os.path.exists(TIKTOK_TOKEN_FILE),
        llm_config=llm_cfg,
        hidden_already_uploaded=hidden_already_uploaded,
        total_deployed_clips=total_deployed_clips_count,
    )


@app.route("/admin/top5")
@admin_required()
def admin_top5():
    """Dedicated halaman admin tab untuk Top 5 Kompilasi build/review/preview."""
    youtube_connected_file = os.path.exists(YOUTUBE_TOKEN_FILE)
    yt_token_valid, yt_check_status = True, "skip_no_check"
    if youtube_connected_file:
        try:
            yt_token_valid, yt_check_status = _try_check_youtube_token_valid()
        except Exception:
            yt_token_valid, yt_check_status = True, "check_exception_ignored"
    youtube_token_expired = youtube_connected_file and (not yt_token_valid)
    youtube_connected_display = bool(youtube_connected_file and yt_token_valid)
    llm_cfg = get_llm_config(include_key=False)

    return render_template(
        "admin.html",
        csrf_token=csrf_token(),
        active_tab="top5",
        podcasts=PODCASTS,
        compilation_episodes=_compilation_episodes(),
        youtube_ready=bool(YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET),
        tiktok_ready=bool(TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET),
        youtube_connected=youtube_connected_display,
        youtube_token_expired=youtube_token_expired,
        youtube_token_check_status=str(yt_check_status),
        tiktok_connected=os.path.exists(TIKTOK_TOKEN_FILE),
        llm_config=llm_cfg,
    )


@app.route("/admin/llm-config", methods=["GET"])
@admin_required()
def admin_llm_config_get():
    """Return JSON LLM config (masked key) buat AJAX live check / refresh state card."""
    accept_json = (request.is_json or
                   request.accept_mimetypes.accept_json or
                   request.headers.get("X-Requested-With") == "XMLHttpRequest")
    if accept_json:
        return jsonify(**get_llm_config(include_key=False))
    # Falls Bukan AJAX → redirect to /admin (page render llm_config card via Jinja)
    return redirect(url_for("admin_page"))


@app.route("/admin/llm-config/save", methods=["POST"])
@admin_required()
def admin_llm_config_save():
    """Save LLM konfigurasi (api_key, base_url, model). Bisa JSON POST atau form POST."""
    payload = request.get_json(silent=True) if request.is_json else None
    payload = payload if isinstance(payload, dict) else None
    if payload is None:
        payload = request.form.to_dict()
    submitted_csrf = str(payload.get("csrf_token", "") if isinstance(payload, dict) else request.form.get("csrf_token", "") or "")
    expected_csrf = str(session.get("admin_csrf", "") or "")
    if not submitted_csrf or not expected_csrf or not secrets.compare_digest(expected_csrf, submitted_csrf):
        return jsonify(error="Invalid form token."), 400

    api_key = str(payload.get("llm_api_key", "") or "").strip()
    base_url = str(payload.get("llm_base_url", "") or "").strip() if "llm_base_url" in payload else None
    model   = str(payload.get("llm_model", "") or "").strip()   if "llm_model"   in payload else None
    accept_json = (request.is_json or
                   request.accept_mimetypes.accept_json or
                   request.headers.get("X-Requested-With") == "XMLHttpRequest")
    clear_key = str(payload.get("llm_api_key_clear", "") or "")
    if clear_key.lower() in ("1", "true", "yes", "on"):
        api_key = ""
        # Delete setting (wipe DB stored key, if any)
        delete_setting("llm_api_key")
    try:
        if base_url is None and model is None and "llm_api_key" in payload:
            # Only key change (but validation handled in save_llm_config)
            cfg = save_llm_config(api_key=api_key)
        elif not (base_url is None and model is None and "llm_api_key" not in payload):
            cfg = save_llm_config(api_key=api_key, base_url=base_url, model=model)
        else:
            cfg = save_llm_config(api_key=api_key)
    except ValueError as exc:
        msg = str(exc)
        if accept_json:
            return jsonify(error=msg), 400
        flash(msg, "error")
        return redirect(url_for("admin_page"))
    if accept_json:
        return jsonify(ok=True, config=cfg)
    flash(f"LLM config tersimpan. Sumber: {cfg.get('configured_from','?')}", "success")
    return redirect(url_for("admin_page"))


@app.route("/admin/jobs")
@admin_required()
def admin_jobs():
    grouped = _group_submissions_by_video(_submissions_with_progress())
    jobs = []
    for g in grouped:
        for submission in g.get("items", g.get("submissions", [])):
            jobs.append({
                "video_id": submission["video_id"],
                "status": submission["status"],
                **submission["progress"],
            })
    groups_payload = [
        {
            "video_id": g["video_id"],
            "group_status": g["worst_status"],
            "group_status_label": g["group_status_label"],
            "group_status_class": g["group_status_class"],
            "stage": g["worst_progress"].get("stage", ""),
            "label": g["worst_progress"].get("label", ""),
            "percent": g["max_percent"],
            "running_count": g["running_count"],
                "pending_count": g["pending_count"],
                "review_count": g["review_count"],
                "completed_count": g["completed_count"],
            "failed_count": g["failed_count"],
        }
        for g in grouped
    ]
    return jsonify(jobs=jobs, groups=groups_payload)


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
    llm_cfg = get_llm_config(include_key=True)
    api_key = str(llm_cfg.get("api_key") or "").strip()
    base_url = str(llm_cfg.get("base_url") or "").strip()
    model = str(llm_cfg.get("model") or "").strip() or "MiniMax-M2.7-highspeed"
    cfg_source = llm_cfg.get("configured_from") or "none"
    if not api_key:
        return jsonify(error=f"LLM API key belum dikonfigurasi (sumber={cfg_source}). "
                             "Silahkan save dulu di halaman admin bagian 'Konfigurasi LLM'."), 503
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
            "Pilih 5-10 KANDIDAT moment TERBAIK dari transkrip podcast Indonesia berikut "
            "untuk KOMPILASI TOP 5 SHORTS (TOTAL DURASI MAKSIMAL 60 DETIK, jadi SETIAP MOMEN HANYA 6-14 DETIK SAJA).\n"
            "KRITERIA PEMILIHAN (URUT BERDAMPAK TINGGI KE RENDAH):\n"
            "  1. Punchline terkuat, kalimat closing berani, satir tajam, joke lucu ter-epic, reaksi kaget/kejengkelan host.\n"
            "  2. Argumen utama / inti perbincangan (setting konteks 2-3 kalimat sebelum + punchline).\n"
            "  3. Naratif emosi, provokasi sehat, opini viral, tawa penonton riuh (energy burst).\n"
            "  4. JANGAN pilih bagian perkenalan biasa, basa-basi, iklan, jeda, mengulang topik, atau cross-talk tanpa inti.\n"
            "  5. Setiap moment MANDIRI (jangan pecah satu topik jadi dua), start/end TEPAT di kalimat UTUH (tidak potong tengah kalimat).\n\n"
            "DURASI WAJIB:\n"
            "  - Per item: MIN 6s · MAKS 14s (ideal 9-12s)\n"
            "  - JUMLAHKAN SEMUA 5 ITEM NANTI TIDAK BOLEH > 60 DETIK.\n\n"
            "Keluaran HANYA JSON array (tanpa markdown ```, tanpa komentar) berisi objek:\n"
            "  start  <number>  detik mulai (harus TEPAT sama dengan salah satu segment.start di bawah)\n"
            "  end    <number>  detik selesai (harus TEPAT sama dengan segment.end, durasi end-start antara 6-14 detik)\n"
            "  title  <string>  label momen MAX 60 karakter (judul punchline / inti)\n"
            "  reason <string>  ALASAN kenapa dipilih MAX 160 karakter: sebutkan tipe (punchline/opening/argumen/emosi/viral) dan dampaknya.\n"
            "URUTKAN array dari TERBAIK dulu (paling berdampak / paling bagus jadi TOP #1, turun sampai TOP #5+).\n\n"
            "JANGAN mengarang teks di luar transcript. Jangan ambil segment panjang; 12 detik sudah cukup untuk 1 punchline.\n\n"
            "--- TRANSKRIP ---\n"
            + transcript_text
        )
        request_body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }).encode()
        llm_request = urllib.request.Request(
            base_url,
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
            if start < 0 or end <= start or end - start < 6 or end - start > 14:
                continue
            normalized.append({
                "start": start,
                "end": end,
                "duration": round(end - start, 1),
                "title": str(candidate.get("title", "Moment"))[:60],
                "reason": str(candidate.get("reason", ""))[:160],
            })
        return jsonify(candidates=normalized, source=f"llm:{cfg_source}",
                       model_used=model, endpoint=base_url,
                       max_total_duration=60, per_item_min=6, per_item_max=14)
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
        MAX_TOTAL_DURATION = 60.0
        PER_ITEM_MIN = 6.0
        PER_ITEM_MAX = 14.0
        normalized_items = []
        segments = []
        total_dur = 0.0
        per_item_durations = []
        for index, item in enumerate(items):
            start = float(item["start"])
            end = float(item["end"])
            label = str(item.get("label", "")).strip()[:60]
            if not label or start < 0 or end <= start:
                raise ValueError
            dur = end - start
            if dur < PER_ITEM_MIN:
                return jsonify(error=f"Momen #{index+1} terlalu pendek ({dur:.1f}s); minimal {PER_ITEM_MIN:.0f}s."), 400
            if dur > PER_ITEM_MAX:
                return jsonify(error=f"Momen #{index+1} terlalu panjang ({dur:.1f}s); maksimal {PER_ITEM_MAX:.0f}s."), 400
            total_dur += dur
            per_item_durations.append(dur)
            normalized_items.append({"label": label})
            segments.append([start, end, index])
        auto_warning = None
        if total_dur > MAX_TOTAL_DURATION:
            # Auto-truncate dari yang TERAKHIR di urutan (item index terbesar) kurangi end-nya supaya total tepat 60s.
            overflow = total_dur - MAX_TOTAL_DURATION
            idx = len(items) - 1
            while overflow > 1e-6 and idx >= 0:
                s_start, s_end_old, s_i = segments[idx]
                old_dur = per_item_durations[idx]
                # Kurangi item ini sebanyak overflow, tapi tetap jaga >= PER_ITEM_MIN
                new_dur = max(PER_ITEM_MIN, old_dur - overflow)
                reduced = old_dur - new_dur
                overflow -= reduced
                per_item_durations[idx] = new_dur
                segments[idx] = [s_start, s_start + new_dur, s_i]
                idx -= 1
            total_dur = sum(per_item_durations)
            auto_warning = (
                f"Total durasi melebihi {MAX_TOTAL_DURATION:.0f}s; auto-truncate momen terakhir menjadi total {total_dur:.1f}s. "
                f"Review kembali kualitas potongan sebelum deploy permanen / upload."
            )
    except (KeyError, TypeError, ValueError):
        return jsonify(error="Each moment needs valid start, end, and label values."), 400

    job_id = f"compilation-{workdir.name}-{int(time.time())}-{secrets.token_hex(4)}"
    lock_path = _acquire_compilation_lock(workdir, job_id)
    if lock_path is None:
        return jsonify(error="Build kompilasi lain masih berjalan untuk episode ini."), 409
    config_path = workdir / f".{job_id}.json"
    final_output_path = workdir / "clips" / "top5_compilation.mp4"
    job_output_path = workdir / "clips" / f".{job_id}.mp4"
    log_dir = Path(os.environ.get("ADMIN_JOB_LOG_DIR", "/tmp/podcast-clips/jobs"))
    log_path = log_dir / f"{job_id}.log"
    try:
        config_path.write_text(json.dumps({
            "header_l1": str(payload.get("header_l1", "TOP 5 MOMEN"))[:80],
            "header_l2": str(payload.get("header_l2", ""))[:80],
            "subheader": str(payload.get("subheader", ""))[:120],
            "items": normalized_items,
            "segs": segments,
            "auto_warning": auto_warning,
            "total_duration_seconds": round(total_dur, 2),
        }, ensure_ascii=False), encoding="utf-8")
        log_dir.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable, str(REPO_ROOT / "scripts" / "build_compilation.py"),
            str(workdir), str(job_output_path), str(final_output_path),
            str(config_path), str(log_path), str(lock_path),
        ]
        subprocess.Popen(command, cwd=str(REPO_ROOT), start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env={**os.environ, "PYTHONUNBUFFERED": "1"})
    except OSError:
        lock_path.unlink(missing_ok=True)
        job_output_path.unlink(missing_ok=True)
        return jsonify(error="Could not start compilation worker."), 503
    return jsonify(job_id=job_id, output=f"/admin/compilation/preview/{workdir.name}",
                   total_duration_seconds=round(total_dur, 2),
                   auto_warning=auto_warning)


@app.route("/admin/compilation/preview/<episode_id>")
@admin_required()
def admin_compilation_preview(episode_id):
    workdir = _compilation_workdir(episode_id)
    if workdir is not None and _compilation_is_locked(workdir):
        return "Compilation build in progress", 409
    output_path = workdir / "clips" / "top5_compilation.mp4" if workdir else None
    if output_path is None or not output_path.is_file():
        return "Compilation not ready", 404
    return send_file(output_path, mimetype="video/mp4", conditional=True)


@app.route("/admin/compilation/source/<episode_id>")
@admin_required()
def admin_compilation_source(episode_id):
    workdir = _compilation_workdir(episode_id)
    source_path = workdir / "source.mp4" if workdir else None
    if source_path is None or not source_path.is_file():
        return "Source video not found", 404
    return send_file(source_path, mimetype="video/mp4", conditional=True)


@app.route("/admin/compilation/status/<episode_id>")
@admin_required()
def admin_compilation_status(episode_id):
    workdir = _compilation_workdir(episode_id)
    if workdir is None:
        return jsonify(status="invalid"), 404
    output_path = workdir / "clips" / "top5_compilation.mp4"
    deployed_path = _compilation_deployed_path(episode_id, workdir=workdir)
    upload_info = _compilation_upload_status(episode_id, workdir=workdir)
    base = {}
    if deployed_path is not None:
        base["deployed"] = True
        base["deployed_file"] = deployed_path.name
        podcast_dir = deployed_path.parent.parent.name
        episode_dir = deployed_path.parent.name
        base["static_url"] = url_for(
            "static",
            filename=f"clips/{podcast_dir}/{episode_dir}/{deployed_path.name}",
        )
    else:
        base["deployed"] = False
    if upload_info is not None:
        base["upload_status"] = upload_info.get("status")
        base["upload_video_id"] = upload_info.get("video_id")
        base["upload_error"] = upload_info.get("error")
        if upload_info.get("video_id"):
            base["upload_url"] = f"https://youtube.com/watch?v={upload_info['video_id']}"
    else:
        base["upload_status"] = None
    if _compilation_is_locked(workdir):
        base["status"] = "running"
        return jsonify(**base)
    log_files = sorted(Path(os.environ.get("ADMIN_JOB_LOG_DIR", "/tmp/podcast-clips/jobs")).glob(f"compilation-{episode_id}-*.log"))
    if log_files:
        log_text = log_files[-1].read_text(encoding="utf-8", errors="replace")
        if "EXIT_CODE=" in log_text and not log_text.rstrip().endswith("EXIT_CODE=0"):
            base["status"] = "failed"
            return jsonify(**base)
        if "EXIT_CODE=" not in log_text:
            base["status"] = "failed"
            base["error"] = "Build worker stopped before recording an exit code."
            return jsonify(**base)
    if output_path.is_file():
        base["status"] = "completed"
        base["preview"] = url_for("admin_compilation_preview", episode_id=episode_id)
        return jsonify(**base)
    base["status"] = "idle"
    return jsonify(**base)


def _deploy_compilation(episode_id):
    workdir = _compilation_workdir(episode_id)
    if workdir is None:
        return None, "Workspace episode tidak valid."
    if _compilation_is_locked(workdir):
        return None, "Build kompilasi masih berjalan; tunggu sampai preview baru selesai."
    src = workdir / "clips" / "top5_compilation.mp4"
    if not src.is_file():
        return None, "Kompilasi belum di-render (file top5_compilation.mp4 tidak ada di workspace)."
    meta = _episode_meta_from_workdir(workdir)
    if not meta:
        return None, "Metadata episode tidak bisa dibaca."
    deploy_dir, dest = _top5_deployed_dir_and_file(meta)
    try:
        deploy_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return None, f"Gagal buat folder deploy: {exc}"

    import hashlib
    import shutil
    import tempfile

    # Tulisan atomic: copy ke .tmp, lalu os.replace (per project convention hard constraint).
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".top5deploy-", suffix=".tmp.mp4", dir=str(deploy_dir))
    try:
        with os.fdopen(tmp_fd, "wb") as out:
            with src.open("rb") as inp:
                shutil.copyfileobj(inp, out, length=1024 * 1024)
        os.replace(tmp_path, dest)
    except Exception as exc:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
        return None, f"Gagal menulis file deploy: {exc}"

    # Tulis companion .metadata.json
    try:
        stat = dest.stat()
        h = hashlib.sha256()
        with dest.open("rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        sha = h.hexdigest()
        compile_cfg = None
        # Coba baca config build terakhir untuk ambil header/items
        for cfg in sorted(workdir.glob(".compilation-*.json"), reverse=True):
            try:
                compile_cfg = json.loads(cfg.read_text(encoding="utf-8"))
                break
            except (OSError, json.JSONDecodeError):
                continue
        title_suffix = ""
        description_lines = []
        if compile_cfg:
            h1 = str(compile_cfg.get("header_l1") or "TOP 5 MOMEN").strip()
            h2 = str(compile_cfg.get("header_l2") or meta["podcast"].upper()).strip()
            sub = str(compile_cfg.get("subheader") or meta["title"]).strip()
            title_suffix = f"{h1} — {meta['title']}"
            description_lines.append(f"{h1} {h2}")
            description_lines.append(sub)
            items_raw = compile_cfg.get("items") or []
            if isinstance(items_raw, list):
                for i, it in enumerate(items_raw, 1):
                    if isinstance(it, dict):
                        label = it.get("label")
                    else:
                        label = it
                    if label:
                        description_lines.append(f"{i}. {label}")
        if not title_suffix:
            title_suffix = f"TOP 5 KOMPILASI — {meta['title']}"
        description = "\n".join(description_lines).strip() or f"Top 5 kompilasi terbaik dari episode {meta['title']}"
        metadata_json = {
            "podcast_slug": meta["podcast"],
            "episode_date": meta["date"],
            "episode_slug": meta["slug"],
            "episode_title": meta["title"],
            "clip_num": TOP5_CLIP_NUM,
            "video_filename": dest.name,
            "title": title_suffix[:200],
            "description": description,
            "tags": ["shorts", "podcast", "indonesia", meta["podcast"], "tempo",
                     "berita", "politik", "viral", "fyp", "news", "video",
                     "opini", "analisis", "terkini", "podcastindonesia",
                     "top5", "kompilasi", "top5kompilasi"],
            "tags_csv": ",".join(["shorts", "podcast", "indonesia", meta["podcast"], "tempo",
                                  "berita", "politik", "viral", "fyp", "news", "video",
                                  "opini", "analisis", "terkini", "podcastindonesia",
                                  "top5", "kompilasi"]),
            "sha256": sha,
            "file_size": stat.st_size,
            "file_size_mb": round(stat.st_size / (1024 * 1024), 2),
            "compilation": True,
        }
        meta_file = dest.with_suffix(dest.suffix + ".metadata.json")
        tmp_fd2, tmp_meta_path = tempfile.mkstemp(prefix=".top5meta-", suffix=".tmp.json", dir=str(deploy_dir))
        try:
            with os.fdopen(tmp_fd2, "w", encoding="utf-8") as out:
                json.dump(metadata_json, out, ensure_ascii=False, indent=2)
            os.replace(tmp_meta_path, meta_file)
        except Exception as exc:
            try:
                if os.path.exists(tmp_meta_path):
                    os.unlink(tmp_meta_path)
            except OSError:
                pass
            return (dest, meta), f"File tersimpan tapi gagal tulis metadata companion: {exc}"
    except OSError as exc:
        return (dest, meta), f"File tersimpan tapi gagal hitung hash/metadata: {exc}"

    # Simpan juga episode_data.json copy ke deploy dir for consistency
    try:
        src_ep = workdir / "episode_data.json"
        if src_ep.is_file() and not (deploy_dir / "episode_data.json").is_file():
            shutil.copy2(src_ep, deploy_dir / "episode_data.json")
    except OSError:
        pass

    return (dest, meta), None  # (ok, error)


@app.route("/admin/compilation/deploy/<episode_id>", methods=["POST"])
@admin_required()
def admin_compilation_deploy(episode_id):
    payload = request.get_json(silent=True) or {}
    submitted_csrf = payload.get("csrf_token", "") if isinstance(payload, dict) else ""
    if not submitted_csrf:
        submitted_csrf = request.form.get("csrf_token", "")
    expected_csrf = session.get("admin_csrf", "")
    if not expected_csrf or not isinstance(submitted_csrf, str) or not secrets.compare_digest(expected_csrf, submitted_csrf):
        return jsonify(error="Invalid form token."), 400
    result, err = _deploy_compilation(episode_id)
    accept_json = (request.is_json or
                   request.accept_mimetypes.accept_json or
                   request.headers.get("X-Requested-With") == "XMLHttpRequest")
    if result is None:
        if accept_json:
            return jsonify(error=err or "Deploy gagal."), 500
        flash(f"Deploy gagal: {err or 'tidak diketahui.'}", "error")
        return redirect(url_for("admin_top5") if request.referrer and "top5" in request.referrer else url_for("admin_page"))
    dest, meta = result
    podcast_dir = dest.parent.parent.name
    episode_dir = dest.parent.name
    static_url = url_for("static", filename=f"clips/{podcast_dir}/{episode_dir}/{dest.name}")
    if accept_json:
        return jsonify(ok=True, deployed_file=dest.name, static_url=static_url,
                       podcast=meta["podcast"], episode_title=meta["title"],
                       warning=err)
    flash(f"Deploy berhasil: {dest.name} — " + (f"catatan: {err}" if err else "tersimpan permanen."),
          "error" if err else "success")
    return redirect(url_for("admin_top5"))


@app.route("/admin/compilation/upload/<episode_id>", methods=["POST"])
@admin_required()
def admin_compilation_upload(episode_id):
    payload = request.get_json(silent=True) or {}
    submitted_csrf = payload.get("csrf_token", "") if isinstance(payload, dict) else ""
    if not submitted_csrf:
        submitted_csrf = request.form.get("csrf_token", "")
    expected_csrf = session.get("admin_csrf", "")
    if not expected_csrf or not isinstance(submitted_csrf, str) or not secrets.compare_digest(expected_csrf, submitted_csrf):
        return jsonify(error="Invalid form token."), 400
    workdir = _compilation_workdir(episode_id)
    if workdir is None:
        return jsonify(error="Workspace episode tidak valid."), 404
    deployed = _compilation_deployed_path(episode_id, workdir=workdir)
    if deployed is None:
        # Deploy otomatis dulu jika belum permanen
        result, err_deploy = _deploy_compilation(episode_id)
        if result is None:
            msg = f"Belum bisa upload: deploy permanen gagal duluan — {err_deploy or 'error tidak diketahui'}"
            accept_json = (request.is_json or
                           request.accept_mimetypes.accept_json or
                           request.headers.get("X-Requested-With") == "XMLHttpRequest")
            if accept_json:
                return jsonify(error=msg), 500
            flash(msg, "error")
            return redirect(url_for("admin_top5"))
        deployed, meta = result
    else:
        meta = _episode_meta_from_workdir(workdir)
    # Load metadata companion untuk title/description/tags, fallback build dari meta
    podcast_dir = deployed.parent.parent.name
    episode_folder = deployed.parent.name
    existing = get_youtube_upload(meta["podcast"], episode_folder, TOP5_CLIP_NUM)
    accept_json = (request.is_json or
                   request.accept_mimetypes.accept_json or
                   request.headers.get("X-Requested-With") == "XMLHttpRequest")
    if existing and existing.get("status") in ("uploaded", "manual"):
        vid = existing.get("video_id") or ""
        if accept_json:
            return jsonify(ok=True, already_uploaded=True, status=existing["status"],
                           video_id=vid, url=f"https://youtube.com/watch?v={vid}")
        flash(f"Top 5 sudah di-upload ({existing['status']}): {vid}", "success")
        return redirect(url_for("admin_top5"))
    if existing and existing.get("status") == "uploading":
        msg = "Upload sedang berjalan untuk episode ini — tunggu sebentar atau refresh status."
        if accept_json:
            return jsonify(ok=False, already_uploaded=False, status="uploading",
                           in_progress=True, error=msg), 409
        flash(msg, "error")
        return redirect(url_for("admin_top5"))
    # Baca companion atau fallback default
    meta_file = deployed.with_suffix(deployed.suffix + ".metadata.json")
    caption_title = f"TOP 5 KOMPILASI — {meta['title']}"
    caption_desc = f"Top 5 momen terbaik episode {meta['title']}"
    tags = ["shorts", "podcast", "indonesia", meta["podcast"], "tempo",
            "berita", "politik", "viral", "fyp", "news", "video",
            "opini", "analisis", "terkini", "podcastindonesia",
            "top5", "kompilasi"]
    if meta_file.is_file():
        try:
            companion = json.loads(meta_file.read_text(encoding="utf-8"))
            caption_title = companion.get("title") or caption_title
            caption_desc = companion.get("description") or caption_desc
            if isinstance(companion.get("tags"), list):
                tags = list(companion["tags"])
        except (OSError, json.JSONDecodeError):
            pass
    clip_meta = deployed_clip_metadata(
        meta["podcast"], episode_folder, TOP5_CLIP_NUM,
        caption={"title": caption_title, "caption": caption_desc, "clip": deployed.name,
                 "compilation": True},
        clip_path=deployed,
    ) or {}
    claimed, current = claim_youtube_upload(
        meta["podcast"], episode_folder, TOP5_CLIP_NUM,
        sha256=clip_meta.get("sha256", ""), file_size=clip_meta.get("file_size", 0),
    )
    if not claimed:
        current_status = (current or {}).get("status", "uploading")
        msg = f"Upload tidak dimulai karena status saat ini: {current_status}."
        if accept_json:
            return jsonify(error=msg, status=current_status), 409
        flash(msg, "error")
        return redirect(url_for("admin_top5"))
    try:
        from scripts.youtube_upload import upload_clip
        result = upload_clip(str(deployed), caption_title, caption_desc.strip(), tags=tags)
        def finalize_ok(flash_msg, flash_cat="success", extra_warn=None):
            record_youtube_upload(
                meta["podcast"], episode_folder, TOP5_CLIP_NUM, "uploaded",
                video_id=result.get("video_id"),
                sha256=clip_meta.get("sha256", ""), file_size=clip_meta.get("file_size", 0),
                error=extra_warn or "",
            )
            if accept_json:
                return jsonify(ok=True, uploaded=True,
                               status="uploaded",
                               video_id=result.get("video_id"),
                               url=result.get("url"),
                               title=result.get("title"),
                               warning=extra_warn or result.get("warning"))
            flash(flash_msg, flash_cat)
            return redirect(url_for("admin_top5"))
        if "error" in result:
            message = result["error"]
            is_invalid_grant = (bool(result.get("invalid_grant")) or
                                "expired or dicabut" in message or
                                "invalid_grant" in message)
            partial_success = bool(result.get("partial_success")) and bool(result.get("video_id"))
            if partial_success:
                return finalize_ok(
                    f"Upload BERHASIL ({result.get('video_id','')}) tapi: {message}",
                    "error",
                    extra_warn=message[:500],
                )
            try:
                if is_invalid_grant and os.path.exists(YOUTUBE_TOKEN_FILE):
                    os.remove(YOUTUBE_TOKEN_FILE)
            except OSError:
                pass
            status = "failed"
            err_msg = message
            if is_invalid_grant:
                err_msg = ("⚠️ Token YouTube KADALUARSA atau DICABUT. "
                           "Klik HUBUNGKAN YOUTUBE di 'Koneksi akun upload' untuk reconnect dulu.")
            elif "uploadLimitExceeded" in message:
                status = "quota"
                err_msg = "Kuota upload harian habis — retry besok."
            record_youtube_upload(
                meta["podcast"], episode_folder, TOP5_CLIP_NUM, status,
                error=err_msg[:500],
                sha256=clip_meta.get("sha256", ""), file_size=clip_meta.get("file_size", 0),
            )
            if accept_json:
                return jsonify(error=err_msg, status=status, invalid_grant=is_invalid_grant), 502
            flash(err_msg, "error")
            return redirect(url_for("admin_top5"))
        # Sukses tanpa error (mungkin ada warning metadata)
        warning_msg = result.get("warning")
        url = result.get("url", f"https://youtube.com/watch?v={result.get('video_id','')}")
        msg = f"Upload berhasil: {url}"
        cat = "success"
        if warning_msg:
            msg = f"Upload berhasil: {url} (catatan: {warning_msg})"
            cat = "error"
        return finalize_ok(msg, cat, extra_warn=warning_msg)
    except Exception as exc:
        message = str(exc)
        is_invalid_grant = ("invalid_grant" in message or
                            "expired or revoked" in message.lower() or
                            "expired or dicabut" in message)
        if is_invalid_grant:
            try:
                if os.path.exists(YOUTUBE_TOKEN_FILE):
                    os.remove(YOUTUBE_TOKEN_FILE)
            except OSError:
                pass
            err_msg = ("⚠️ Token YouTube KADALUARSA atau DICABUT. "
                       "Klik HUBUNGKAN YOUTUBE di 'Koneksi akun upload' untuk reconnect dulu.")
            status = "failed"
        else:
            status = "quota" if "uploadLimitExceeded" in message else "failed"
            err_msg = "Kuota harian habis — retry besok." if status == "quota" else f"Upload gagal: {message}"
        record_youtube_upload(
            meta["podcast"], episode_folder, TOP5_CLIP_NUM, status,
            error=err_msg[:500],
            sha256=clip_meta.get("sha256", ""), file_size=clip_meta.get("file_size", 0),
        )
        if accept_json:
            return jsonify(error=err_msg, status=status, invalid_grant=is_invalid_grant), 502
        flash(err_msg, "error")
        return redirect(url_for("admin_top5"))


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
    if existing and existing.get("status") == "uploading":
        flash("Upload klip ini sedang berjalan. Tunggu statusnya berubah sebelum retry.", "error")
        return redirect(url_for("admin_page"))
    clip_path, caption = clip
    meta = deployed_clip_metadata(podcast, episode, clip_num, caption=caption, clip_path=clip_path) or {}
    claimed, current = claim_youtube_upload(
        podcast, episode, clip_num,
        sha256=meta.get("sha256", ""), file_size=meta.get("file_size", 0),
    )
    if not claimed:
        flash(
            f"Upload tidak dimulai karena status saat ini: {(current or {}).get('status', 'uploading')}.",
            "error",
        )
        return redirect(url_for("admin_page"))
    tags = ["shorts", "podcast", "indonesia", podcast, "tempo", "berita", "politik", "viral", "fyp", "news", "video", "opini", "analisis", "terkini", "podcastindonesia"]
    try:
        from scripts.youtube_upload import upload_clip
        result = upload_clip(clip_path, caption.get("title", f"Clip {clip_num}"), caption.get("caption", "").strip(), tags=tags)
        if "error" in result:
            message = result["error"]
            is_invalid_grant = bool(result.get("invalid_grant")) or "expired or dicabut" in message or "invalid_grant" in message
            partial_success = bool(result.get("partial_success")) and bool(result.get("video_id"))
            if partial_success:
                # Video SUCCESSFULLY uploaded (we have video_id), token or metadata step had an error.
                # Record as UPLOADED — don't mark a live video as "failed".
                record_youtube_upload(podcast, episode, clip_num, "uploaded",
                                      video_id=result.get("video_id"),
                                      sha256=meta.get("sha256", ""),
                                      file_size=meta.get("file_size", 0),
                                      error=message)
                if is_invalid_grant:
                    try:
                        if os.path.exists(YOUTUBE_TOKEN_FILE):
                            os.remove(YOUTUBE_TOKEN_FILE)
                    except OSError:
                        pass
                    flash(
                        f"⚠️ Upload BERHASIL ({result.get('video_id','')}) tapi token YouTube KADALUARSA/DICABUT. "
                        f"Klik HUBUNGKAN YOUTUBE di 'Koneksi akun upload' untuk reconnect sebelum upload berikutnya.",
                        "error",
                    )
                else:
                    flash(f"Upload BERHASIL ({result.get('url', result.get('video_id',''))}) tapi ada masalah metadata: {message}",
                          "error")
            elif is_invalid_grant:
                # Token invalid_grant dihapus sama upload_clip, kasih tahu user reconnect via button
                try:
                    if os.path.exists(YOUTUBE_TOKEN_FILE):
                        os.remove(YOUTUBE_TOKEN_FILE)
                except OSError:
                    pass
                status = "failed"
                flash(
                    "⚠️ Token YouTube KADALUARSA atau DICABUT. Klik HUBUNGKAN YOUTUBE di bagian 'Koneksi akun upload' untuk reconnect dulu — baru upload ulang.",
                    "error",
                )
                record_youtube_upload(podcast, episode, clip_num, status, error=message,
                                      sha256=meta.get("sha256", ""), file_size=meta.get("file_size", 0))
            elif "uploadLimitExceeded" in message:
                status = "quota"
                flash("Kuota upload harian habis — retry besok.", "error")
                record_youtube_upload(podcast, episode, clip_num, status, error=message,
                                      sha256=meta.get("sha256", ""), file_size=meta.get("file_size", 0))
            else:
                status = "failed"
                flash(f"Upload gagal: {message}", "error")
                record_youtube_upload(podcast, episode, clip_num, status, error=message,
                                      sha256=meta.get("sha256", ""), file_size=meta.get("file_size", 0))
        else:
            # Clean success, possibly with a metadata warning.
            record_youtube_upload(podcast, episode, clip_num, "uploaded", video_id=result.get("video_id"),
                                  sha256=meta.get("sha256", ""), file_size=meta.get("file_size", 0))
            warning_msg = result.get("warning")
            if warning_msg:
                flash(f"Upload berhasil: {result.get('url', result.get('video_id', ''))} (catatan: {warning_msg})", "error")
            else:
                flash(f"Upload berhasil: {result.get('url', result.get('video_id', ''))}", "success")
    except Exception as exc:
        message = str(exc)
        is_invalid_grant = ("invalid_grant" in message) or ("expired or revoked" in message.lower()) or ("expired or dicabut" in message)
        if is_invalid_grant:
            try:
                if os.path.exists(YOUTUBE_TOKEN_FILE):
                    os.remove(YOUTUBE_TOKEN_FILE)
            except OSError:
                pass
            flash(
                "⚠️ Token YouTube KADALUARSA atau DICABUT. Klik HUBUNGKAN YOUTUBE di 'Koneksi akun upload' untuk reconnect dulu — baru upload ulang.",
                "error",
            )
            status = "failed"
        else:
            status = "quota" if "uploadLimitExceeded" in message else "failed"
            flash("Kuota harian habis — retry besok." if status == "quota" else f"Upload gagal: {message}", "error")
        record_youtube_upload(podcast, episode, clip_num, status, error=message,
                              sha256=meta.get("sha256", ""), file_size=meta.get("file_size", 0))
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
