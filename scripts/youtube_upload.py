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


def _token_to_headers(creds):
    """Ensure the credentials have a fresh access token, then return an Authorization header dict."""
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleRequest())
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
    if not isinstance(returned_tags, list) or len(returned_tags) < max(1, min(len(tags), 2)):
        raise RuntimeError(
            f"Metadata update did not persist tags: expected {tags}, got {returned_tags!r}"
        )
    return True


def upload_clip(video_path, title, description, tags=None, category_id='25'):
    if not os.path.exists(TOKEN_FILE):
        return {"error": "Belum OAuth. Buka https://clips.gcp.my.id/auth dulu."}

    try:
        creds = Credentials.from_authorized_user_file(TOKEN_FILE,
            ['https://www.googleapis.com/auth/youtube'])
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
        message = str(exc)
        try:
            details = getattr(exc, "error_details", None) or getattr(exc, "content", None)
            if details:
                message = f"{message} | {str(details)[:500]}"
        except Exception:
            pass
        return {"error": message}

    video_id = response.get('id')
    if not video_id:
        return {"error": f"Insert response missing video id: {response}"}

    try:
        _update_video_metadata_raw(creds, video_id, clean_title, clean_desc, deduped_tags, category_id)
    except Exception as exc:
        return {"error": f"Upload OK ({video_id}) but metadata update failed: {exc}",
                "video_id": video_id,
                "url": f"https://youtube.com/watch?v={video_id}",
                "title": clean_title}

    return {
        "video_id": video_id,
        "url": f"https://youtube.com/watch?v={video_id}",
        "title": clean_title,
    }

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