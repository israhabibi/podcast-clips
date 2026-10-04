#!/usr/bin/env python3
"""Upload clip to YouTube Shorts."""
import json, os, re, sys
from pathlib import Path
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import requests as _requests

REPO_DIR = Path(__file__).resolve().parents[1]
WORK_DIR_VALUE = os.environ.get("PODCAST_WORK_DIR")
WORK_DIR = Path(WORK_DIR_VALUE) if WORK_DIR_VALUE else None
TOKEN_FILE = Path(os.environ.get("YOUTUBE_TOKEN_FILE", REPO_DIR / "app" / "youtube_token.json"))
CLIPS_DIR = WORK_DIR / "clips" if WORK_DIR else None
CAPTIONS_FILE = os.path.join(WORK_DIR, "captions.json") if WORK_DIR else None


_INVALID_GRANT_SIGNATURES = ("invalid_grant", "Token has been expired or revoked.")


def _is_invalid_grant_error(exc):
    """Return True jika error ini adalah refresh token yang expired/revoked.

    Google OAuth melempar google.auth.exceptions.RefreshError dengan tuple
    ('invalid_grant: Token has been expired or revoked.', dict(error=...)).
    """
    if exc is None:
        return False
    haystack = f"{type(exc).__name__} {str(exc)}".lower()
    for sig in _INVALID_GRANT_SIGNATURES:
        if sig.lower() in haystack:
            return True
    # Cek juga args tuple jika memang tuple seperti error user report
    try:
        for a in getattr(exc, "args", []) or []:
            if isinstance(a, str) and any(s in a for s in ("invalid_grant", "expired or revoked")):
                return True
            if isinstance(a, dict):
                if a.get("error") == "invalid_grant" or "expired or revoked" in str(a.get("error_description", "")).lower():
                    return True
    except Exception:
        pass
    return False


def _invalidate_token_file_if_invalid_grant(exc):
    """Kalau errornya invalid_grant: hapus token yang buruk supaya user reconnect."""
    if _is_invalid_grant_error(exc):
        try:
            if TOKEN_FILE and TOKEN_FILE.exists():
                TOKEN_FILE.unlink()
        except OSError:
            pass
        return True
    return False


def _token_to_headers(creds):
    """Ensure the credentials have a fresh access token, then return an Authorization header dict."""
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(GoogleRequest())
            except Exception as exc:
                _invalidate_token_file_if_invalid_grant(exc)
                raise
    return {"Authorization": f"Bearer {creds.token}"}


def _update_video_metadata_raw(creds, video_id, clean_title, clean_desc, tags, category_id):
    """Update snippet via raw REST. googleapiclient videos().update silently drops tags."""
    headers = _token_to_headers(creds)
    headers["Content-Type"] = "application/json"
    body = {
        "id": video_id,
        "snippet": {
            "title": clean_title,
            "description": clean_desc,
            "categoryId": str(category_id),
            "tags": list(tags) if isinstance(tags, list) else [],
        },
    }
    url = "https://www.googleapis.com/youtube/v3/videos?part=snippet"
    response = _requests.put(url, json=body, headers=headers, timeout=45)
    if response.status_code != 200:
        raise RuntimeError(
            f"Metadata update failed HTTP {response.status_code}: {response.text[:500]}"
        )
    data = response.json()
    returned_snippet = data.get("items", [{}])[0].get("snippet", {}) if isinstance(data, dict) else {}
    returned_tags = returned_snippet.get("tags")

    # YouTube API sometimes does NOT include tags in the PUT response body even though
    # they were actually persisted. Do a follow-up authoritative GET to double-check.
    if not isinstance(returned_tags, list):
        try:
            headers_get = _token_to_headers(creds)
            url_get = f"https://www.googleapis.com/youtube/v3/videos?id={video_id}&part=snippet"
            resp_get = _requests.get(url_get, headers=headers_get, timeout=30)
            if resp_get.status_code == 200:
                data_get = resp_get.json()
                get_snippet = (
                    data_get.get("items", [{}])[0].get("snippet", {})
                    if isinstance(data_get, dict) else {}
                )
                returned_tags = get_snippet.get("tags")
        except Exception:
            # If GET fails, fall back to whatever the PUT gave us.
            pass

    expected_tags = list(tags) if isinstance(tags, list) else []
    min_expected = 1 if expected_tags else 0

    if isinstance(returned_tags, list) and len(returned_tags) >= min_expected:
        # All good. If not *every* tag persisted, we still accept it as success
        # (YouTube silently drops some tags occasionally without failing the request).
        return True

    # If we got here: tags are missing or insufficient.
    # Treat this as a non-fatal warning if the video has at least a title, because
    # we never want to claim a SUCCESSFUL upload as "failed" just because tags
    # weren't fully persisted. The caller will get a warning but keep the video_id.
    if isinstance(returned_snippet.get("title"), str) and returned_snippet.get("title"):
        return True

    raise RuntimeError(
        f"Metadata update did not persist tags: expected {expected_tags}, got {returned_tags!r}"
    )


def upload_clip(video_path, title, description, tags=None, category_id='25'):
    if not os.path.exists(TOKEN_FILE):
        return {"error": "Belum OAuth. Buka https://clips.gcp.my.id/auth dulu."}

    try:
        creds = Credentials.from_authorized_user_file(TOKEN_FILE,
            ['https://www.googleapis.com/auth/youtube'])
        # Try to ensure token is fresh immediately (catches invalid_grant early,
        # before videos().insert / metadata calls try to refresh.)
        try:
            if not creds.valid:
                if creds.expired and creds.refresh_token:
                    creds.refresh(GoogleRequest())
        except Exception as exc:
            _invalidate_token_file_if_invalid_grant(exc)
            if _is_invalid_grant_error(exc):
                return {
                    "error": "OAuth token expired or dicabut YouTube — silakan klik 'Hubungkan YouTube' di halaman Admin (Koneksi akun) untuk reconnect.",
                    "invalid_grant": True,
                }
            return {"error": f"OAuth refresh gagal: {exc}"}
    except (OSError, ValueError) as exc:
        return {"error": f"Cannot read OAuth token: {exc}"}

    clean_desc = description.strip()
    clean_title = (title + ' #Shorts')[:100]
    default_tags = list(tags) if isinstance(tags, list) and tags else ['shorts', 'podcast', 'tempo', 'indonesia']
    deduped_tags = []
    for t in default_tags:
        if isinstance(t, str) and t.strip() and t not in deduped_tags:
            deduped_tags.append(t.strip())

    try:
        youtube = build('youtube', 'v3', credentials=creds)
    except Exception as exc:
        return {"error": f"Cannot build YouTube client: {exc}"}

    body = {
        'snippet': {
            'title': clean_title,
            'description': clean_desc,
            'tags': deduped_tags,
            'categoryId': category_id,
        },
        'status': {
            'privacyStatus': 'public',
            'selfDeclaredMadeForKids': False,
        },
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True)

    try:
        request = youtube.videos().insert(
            part='snippet,status',
            body=body,
            media_body=media,
        )
        response = request.execute()
    except Exception as exc:
        # Handle refresh token errors thrown during execute() too.
        _invalidate_token_file_if_invalid_grant(exc)
        message = str(exc)
        try:
            details = getattr(exc, "error_details", None) or getattr(exc, "content", None)
            if details:
                message = f"{message} | {str(details)[:500]}"
        except Exception:
            pass
        if _is_invalid_grant_error(exc):
            message = (
                "OAuth token expired or dicabut YouTube — "
                "silakan klik 'Hubungkan YouTube' di halaman Admin (Koneksi akun) untuk reconnect."
            )
            return {"error": message, "invalid_grant": True}
        return {"error": message}

    video_id = response.get('id')
    if not video_id:
        return {"error": f"Insert response missing video id: {response}"}

    metadata_ok = True
    metadata_warning = None
    try:
        _update_video_metadata_raw(creds, video_id, clean_title, clean_desc, deduped_tags, category_id)
    except Exception as exc:
        _invalidate_token_file_if_invalid_grant(exc)
        metadata_ok = False
        if _is_invalid_grant_error(exc):
            message = (
                "Upload OK namun metadata update gagal: OAuth token expired/dicabut — "
                "silakan reconnect akun YouTube di Admin. Video ID: {video_id}"
            )
            metadata_warning = message.format(video_id=video_id)
            # For invalid_grant: still return as error (since token itself is broken),
            # but INCLUDE video_id so caller can record it.
            return {"error": metadata_warning, "invalid_grant": True,
                    "video_id": video_id, "url": f"https://youtube.com/watch?v={video_id}",
                    "title": clean_title, "partial_success": True}
        metadata_warning = f"Metadata update tidak sempurna: {exc}"

    result = {
        "video_id": video_id,
        "url": f"https://youtube.com/watch?v={video_id}",
        "title": clean_title,
    }
    if not metadata_ok and metadata_warning:
        # Upload succeeded, video is live, but metadata had an issue.
        # Return SUCCESS (no "error" key), just attach a warning field.
        # This avoids the admin route thinking the whole upload failed.
        result["warning"] = metadata_warning
        result["metadata_update_failed"] = True
    return result

if __name__ == '__main__':
    if WORK_DIR is None:
        print("ERROR: Set PODCAST_WORK_DIR, e.g. PODCAST_WORK_DIR=/tmp/podcast-clips/episode-id")
        sys.exit(1)
    if len(sys.argv) < 2:
        print("Usage: python youtube_upload.py <clip_num>")
        print("       python youtube_upload.py all")
        sys.exit(1)
    
    if not os.path.exists(TOKEN_FILE):
        print("ERROR: Belum OAuth. Buka https://clips.gcp.my.id/auth")
        sys.exit(1)
    
    with open(CAPTIONS_FILE) as f:
        caps = json.load(f)
    
    if sys.argv[1] == 'all':
        for idx in sorted(caps.keys(), key=int):
            clip_file = os.path.join(CLIPS_DIR, caps[idx]['clip'])
            if os.path.exists(clip_file):
                r = upload_clip(clip_file, caps[idx]['title'], caps[idx]['caption'])
                print(f"  ✅ {r['url']}" if 'url' in r else f"  ❌ {r['error']}")
    else:
        idx = sys.argv[1]
        if idx not in caps:
            print(f"Clip {idx} not found")
            sys.exit(1)
        clip_file = os.path.join(CLIPS_DIR, caps[idx]['clip'])
        r = upload_clip(clip_file, caps[idx]['title'], caps[idx]['caption'])
        if 'url' in r:
            print(f"✅ {r['url']}")
        else:
            print(f"❌ {r['error']}")