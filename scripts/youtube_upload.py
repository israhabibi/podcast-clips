#!/usr/bin/env python3
"""Upload clip to YouTube Shorts."""
import hashlib, json, os, re, secrets, sys
from datetime import datetime, timezone
from pathlib import Path
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import requests as _requests
from episode_manifest import write_manifest

REPO_DIR = Path(__file__).resolve().parents[1]
WORK_DIR_VALUE = os.environ.get("PODCAST_WORK_DIR")
WORK_DIR = Path(WORK_DIR_VALUE) if WORK_DIR_VALUE else None
TOKEN_FILE = Path(os.environ.get("YOUTUBE_TOKEN_FILE", REPO_DIR / "app" / "youtube_token.json"))
CLIPS_DIR = WORK_DIR / "clips" if WORK_DIR else None
CAPTIONS_FILE = os.path.join(WORK_DIR, "captions.json") if WORK_DIR else None


_INVALID_GRANT_SIGNATURES = ("invalid_grant", "Token has been expired or revoked.")

DEFAULT_YOUTUBE_TAGS = (
    'shorts', 'podcast', 'tempo', 'indonesia', 'berita', 'politik', 'viral',
    'fyp', 'news', 'video', 'opini', 'analisis', 'terkini', 'podcastindonesia',
    'clipspodcast',
)


def _clean_metadata(title, description, tags, category_id):
    title = ' '.join(re.sub(r'#shorts\b', '', str(title), flags=re.IGNORECASE).split())
    if not title:
        raise ValueError('YouTube title is required.')
    clean_title = title[:92].rstrip() + ' #Shorts'
    clean_tags = []
    for tag in tags if isinstance(tags, list) and tags else DEFAULT_YOUTUBE_TAGS:
        if isinstance(tag, str) and tag.strip() and tag.strip().lower() not in {t.lower() for t in clean_tags}:
            clean_tags.append(tag.strip())
    return clean_title, _youtube_description(description)[:5000], clean_tags, str(category_id)


def _youtube_description(description):
    """Remove web-only news URLs from captions before public YouTube upload."""
    lines = [re.sub(r"https?://\S+", "", line, flags=re.IGNORECASE).rstrip() for line in str(description or "").splitlines()]
    return "\n".join(line for line in lines if line.strip()).strip()


def _load_manifest():
    if WORK_DIR is None:
        return None
    try:
        data = json.loads((WORK_DIR / "episode_manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_manifest_clip_update(filename, video_id, metadata_status):
    manifest = _load_manifest()
    if not manifest or not isinstance(manifest.get("clips"), list):
        return False
    for clip in manifest["clips"]:
        if isinstance(clip, dict) and clip.get("filename") == filename:
            platforms = clip.setdefault("platform_upload_ids", {})
            youtube = platforms.setdefault("youtube", {})
            youtube.update({"video_id": video_id, "metadata_status": metadata_status})
            try:
                write_manifest(WORK_DIR, manifest)
            except OSError:
                return False
            return True
    return False


def _approve_manifest_release():
    manifest = _load_manifest()
    if not manifest or manifest.get("status") != "ready_for_review" or not isinstance(manifest.get("clips"), list):
        raise ValueError("No ready-for-review episode manifest is available.")
    for clip in manifest["clips"]:
        if not isinstance(clip, dict):
            raise ValueError("Episode manifest contains invalid clip metadata.")
        filename = clip.get("filename")
        media = CLIPS_DIR / filename if isinstance(filename, str) and Path(filename).name == filename else None
        if not media or not media.is_file():
            raise ValueError(f"Review manifest media is missing: {filename}")
        digest = hashlib.sha256(media.read_bytes()).hexdigest()
        if digest != clip.get("sha256"):
            raise ValueError(f"Clip changed after review manifest creation: {filename}")
        clip["review_status"] = "approved"
    manifest["review_status"] = "approved"
    manifest["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    manifest["reviewed_by"] = os.environ.get("USER", "local-operator")[:80]
    write_manifest(WORK_DIR, manifest)


def _manifest_release_approved():
    manifest = _load_manifest()
    if not (
        manifest
        and manifest.get("status") == "ready_for_review"
        and manifest.get("review_status") == "approved"
        and isinstance(manifest.get("clips"), list)
        and manifest["clips"]
        and all(isinstance(clip, dict) and clip.get("review_status") == "approved"
                for clip in manifest["clips"])
    ):
        return False
    for clip in manifest["clips"]:
        filename = clip.get("filename")
        media = CLIPS_DIR / filename if isinstance(filename, str) and Path(filename).name == filename else None
        if not media or not media.is_file():
            return False
        digest = hashlib.sha256(media.read_bytes()).hexdigest()
        if digest != clip.get("sha256"):
            return False
    return True


def _manifest_clip(filename):
    manifest = _load_manifest()
    if not manifest or not isinstance(manifest.get("clips"), list):
        return None
    return next((clip for clip in manifest["clips"]
                 if isinstance(clip, dict) and clip.get("filename") == filename), None)


def _manifest_clip_matches_file(manifest_clip, clip_path):
    if not isinstance(manifest_clip, dict) or not isinstance(manifest_clip.get("sha256"), str):
        return False
    try:
        digest = hashlib.sha256(Path(clip_path).read_bytes()).hexdigest()
    except OSError:
        return False
    return secrets.compare_digest(digest, manifest_clip["sha256"])


def _verify_youtube_channel(youtube):
    """Fail closed unless the token belongs to the configured YouTube channel."""
    expected = os.environ.get("YOUTUBE_EXPECTED_CHANNEL_ID", "").strip()
    if not expected:
        raise ValueError("Set YOUTUBE_EXPECTED_CHANNEL_ID before uploading to YouTube.")
    response = youtube.channels().list(part="id", mine=True).execute()
    items = response.get("items") if isinstance(response, dict) else None
    actual = items[0].get("id") if isinstance(items, list) and items and isinstance(items[0], dict) else None
    if not isinstance(actual, str) or not secrets.compare_digest(actual, expected):
        raise ValueError("The authorized YouTube channel does not match YOUTUBE_EXPECTED_CHANNEL_ID.")
    return actual


def _retry_existing_video_metadata(manifest_clip, caption):
    """Repair a failed metadata update without inserting another video."""
    youtube_data = manifest_clip.get("platform_upload_ids", {}).get("youtube", {})
    video_id = youtube_data.get("video_id")
    if not video_id:
        return False
    result = upload_clip(
        CLIPS_DIR / manifest_clip['filename'], caption.get('title', 'Clip'),
        caption.get('caption', ''), tags=caption.get('tags'),
        release_approved=True, existing_video_id=video_id,
    )
    if result.get('error') or result.get('metadata_update_failed'):
        raise RuntimeError(result.get('error') or result.get('warning'))
    return True


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
    """Write the entire snippet and verify its persisted fields with videos.list."""
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
            f"Metadata update failed HTTP {response.status_code}"
        )
    verification = _requests.get(
        'https://www.googleapis.com/youtube/v3/videos',
        params={'id': video_id, 'part': 'snippet'},
        headers=_token_to_headers(creds), timeout=30,
    )
    if verification.status_code != 200:
        raise RuntimeError(f'Metadata verification failed HTTP {verification.status_code}')
    data = verification.json()
    items = data.get('items') if isinstance(data, dict) else None
    video = items[0] if isinstance(items, list) and len(items) == 1 else None
    if not isinstance(video, dict) or video.get('id') != video_id or not isinstance(video.get('snippet'), dict):
        raise RuntimeError('Metadata verification did not return the uploaded video.')
    snippet = video['snippet']
    mismatches = [key for key in ('title', 'description', 'categoryId')
                  if snippet.get(key) != body['snippet'][key]]
    returned_tags = snippet.get('tags', [])
    if (not isinstance(returned_tags, list) or not all(isinstance(tag, str) for tag in returned_tags)
            or sorted(returned_tags) != sorted(body['snippet']['tags'])):
        mismatches.append('tags')
    if mismatches:
        raise RuntimeError('Metadata verification mismatch: ' + ', '.join(mismatches))
    return True


def upload_clip(video_path, title, description, tags=None, category_id='25', *, release_approved=False,
                existing_video_id=None, on_insert=None):
    if not release_approved:
        return {"error": "Review and approve the release before public YouTube upload."}
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

    try:
        clean_title, clean_desc, deduped_tags, category_id = _clean_metadata(title, description, tags, category_id)
    except ValueError as exc:
        return {'error': str(exc)}

    try:
        youtube = build('youtube', 'v3', credentials=creds)
        _verify_youtube_channel(youtube)
    except Exception as exc:
        return {"error": f"YouTube channel verification failed: {exc}"}

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

    try:
        if existing_video_id:
            response = {'id': existing_video_id}
        else:
            media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
            response = youtube.videos().insert(
                part='snippet,status', body=body, media_body=media,
            ).execute()
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

    if not existing_video_id and on_insert is not None:
        try:
            on_insert(video_id)
        except Exception:
            return {'video_id': video_id, 'url': f'https://youtube.com/watch?v={video_id}',
                    'error': 'Video inserted, but recording its ID failed; reconcile before retrying.',
                    'partial_success': True, 'metadata_update_failed': True}
    manifest_saved = _write_manifest_clip_update(
        Path(video_path).name, video_id, metadata_status="pending",
    )

    metadata_ok = True
    metadata_warning = None
    try:
        _update_video_metadata_raw(creds, video_id, clean_title, clean_desc, deduped_tags, category_id)
        if manifest_saved:
            _write_manifest_clip_update(Path(video_path).name, video_id, metadata_status="complete")
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
                    "title": clean_title, "partial_success": True, "metadata_update_failed": True}
        metadata_warning = f"Metadata update tidak sempurna: {exc}"

    result = {
        "video_id": video_id,
        "url": f"https://youtube.com/watch?v={video_id}",
        "title": clean_title,
    }
    if not metadata_ok and metadata_warning:
        # Upload succeeded, video is live, but metadata had an issue.
        # Callers retain the video ID and retry metadata without another insert.
        result["warning"] = metadata_warning
        result["metadata_update_failed"] = True
    return result

def load_captions():
    if WORK_DIR is None:
        raise ValueError("Set PODCAST_WORK_DIR, e.g. PODCAST_WORK_DIR=/tmp/podcast-clips/episode-id")
    try:
        with open(CAPTIONS_FILE, encoding="utf-8") as captions_file:
            captions = json.load(captions_file)
    except OSError as exc:
        raise ValueError(f"Cannot read captions file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid captions JSON: {exc}") from exc
    if not isinstance(captions, dict):
        raise ValueError("Captions JSON must contain an object keyed by clip number.")
    return captions


def main(args=None):
    args = sys.argv[1:] if args is None else args
    if WORK_DIR is None:
        print("ERROR: Set PODCAST_WORK_DIR, e.g. PODCAST_WORK_DIR=/tmp/podcast-clips/episode-id")
        return 1
    if not args:
        print("Usage: python youtube_upload.py <clip_num>")
        print("       python youtube_upload.py --approve-release   # after reviewing the sample")
        print("       python youtube_upload.py <clip_num>|all      # after approval")
        return 1
    if args == ["--approve-release"]:
        try:
            _approve_manifest_release()
        except (OSError, ValueError) as exc:
            print(f"ERROR: {exc}")
            return 1
        print("Release approved for the exact clips in episode_manifest.json.")
        return 0
    if not _manifest_release_approved():
        print("ERROR: Review the sample and run youtube_upload.py --approve-release before publishing.")
        return 1
    
    if not os.path.exists(TOKEN_FILE):
        print("ERROR: Belum OAuth. Buka https://clips.gcp.my.id/auth")
        return 1

    try:
        caps = load_captions()
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1

    if args[0] == 'all':
        failed = False
        for idx in sorted(caps.keys(), key=int):
            item = caps[idx]
            if not isinstance(item, dict) or not isinstance(item.get("clip"), str):
                print(f"  ❌ {idx}. Invalid caption entry")
                failed = True
                continue
            clip_name = item["clip"]
            if Path(clip_name).name != clip_name:
                print(f"  ❌ {idx}. Invalid clip filename")
                failed = True
                continue
            clip_file = os.path.join(CLIPS_DIR, clip_name)
            if not os.path.isfile(clip_file):
                print(f"  ❌ {idx}. Clip file not found: {clip_file}")
                failed = True
                continue
            manifest_clip = _manifest_clip(clip_name)
            if not _manifest_clip_matches_file(manifest_clip, clip_file):
                print(f"  ❌ {idx}. Clip is not present in the approved review manifest or has changed")
                failed = True
                continue
            youtube_state = (manifest_clip or {}).get("platform_upload_ids", {}).get("youtube", {})
            if youtube_state.get("video_id"):
                if youtube_state.get("metadata_status") == "complete":
                    print(f"  ℹ️ {idx}. Already uploaded: https://youtube.com/watch?v={youtube_state['video_id']}")
                    continue
                try:
                    _retry_existing_video_metadata(manifest_clip, item)
                    print(f"  ✅ {idx}. Metadata repaired without another insert.")
                except Exception as exc:
                    print(f"  ❌ {idx}. Metadata retry failed for existing video {youtube_state['video_id']}: {exc}")
                    failed = True
                continue
            r = upload_clip(clip_file, str(item.get('title', f'Clip {idx}')), str(item.get('caption', '')),
                            tags=item.get('tags'), release_approved=True)
            if r.get('error') or r.get('metadata_update_failed'):
                print(f"  ❌ {r.get('error') or r.get('warning', 'Metadata verification failed')}")
                failed = True
            elif 'url' in r:
                print(f"  ✅ {r['url']}")
            else:
                print(f"  ❌ {r.get('error', 'Unknown upload error')}")
                failed = True
        return 1 if failed else 0
    else:
        idx = args[0]
        if idx not in caps:
            print(f"Clip {idx} not found")
            return 1
        item = caps[idx]
        if not isinstance(item, dict) or not isinstance(item.get("clip"), str):
            print(f"Invalid caption entry for clip {idx}")
            return 1
        clip_name = item["clip"]
        if Path(clip_name).name != clip_name:
            print(f"Invalid clip filename for clip {idx}")
            return 1
        clip_file = os.path.join(CLIPS_DIR, clip_name)
        if not os.path.isfile(clip_file):
            print(f"Clip file not found: {clip_file}")
            return 1
        manifest_clip = _manifest_clip(clip_name)
        if not _manifest_clip_matches_file(manifest_clip, clip_file):
            print(f"Clip {idx} is not present in the approved review manifest or has changed")
            return 1
        youtube_state = (manifest_clip or {}).get("platform_upload_ids", {}).get("youtube", {})
        if youtube_state.get("video_id"):
            if youtube_state.get("metadata_status") == "complete":
                print(f"Already uploaded: https://youtube.com/watch?v={youtube_state['video_id']}")
                return 0
            try:
                _retry_existing_video_metadata(manifest_clip, item)
            except Exception as exc:
                print(f"Metadata retry failed for existing video {youtube_state['video_id']}: {exc}")
                return 1
            print(f"Metadata repaired for existing video {youtube_state['video_id']}.")
            return 0
        r = upload_clip(clip_file, str(item.get('title', f'Clip {idx}')), str(item.get('caption', '')),
                        tags=item.get('tags'), release_approved=True)
        if r.get('error') or r.get('metadata_update_failed'):
            print(f"❌ {r.get('error') or r.get('warning', 'Metadata verification failed')}")
            return 1
        if 'url' in r:
            print(f"✅ {r['url']}")
            return 0
        else:
            print(f"❌ {r.get('error', 'Unknown upload error')}")
            return 1


if __name__ == '__main__':
    raise SystemExit(main())
