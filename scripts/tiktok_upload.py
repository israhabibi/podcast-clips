#!/usr/bin/env python3
"""Upload clip to TikTok via Content Posting API v2."""
import json, os, sys, requests, time
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]
WORK_DIR_VALUE = os.environ.get("PODCAST_WORK_DIR")
WORK_DIR = Path(WORK_DIR_VALUE) if WORK_DIR_VALUE else None
TOKEN_FILE = Path(os.environ.get("TIKTOK_TOKEN_FILE", REPO_DIR / "app" / "tiktok_token.json"))
CLIPS_DIR = WORK_DIR / "clips" if WORK_DIR else None
CAPTIONS_FILE = os.path.join(CLIPS_DIR, "captions.json") if CLIPS_DIR else None

API_BASE = "https://open.tiktokapis.com/v2"

def get_token():
    if not os.path.exists(TOKEN_FILE):
        return None, None
    data = json.load(open(TOKEN_FILE))
    return data.get("access_token"), data.get("open_id")

def upload_clip(video_path, caption, idx):
    token, open_id = get_token()
    if not token:
        return {"error": "Belum OAuth. Buka https://clips.gcp.my.id/tiktok-auth dulu."}

    headers = {"Authorization": f"Bearer {token}"}
    video_size = os.path.getsize(video_path)

    # 1. Initialize upload
    init_url = f"{API_BASE}/post/publish/inbox/video/init/"
    init_body = {
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": video_size,
            "total_chunk_count": 1
        }
    }
    r = requests.post(init_url, json=init_body, headers=headers)
    if r.status_code != 200:
        return {"error": f"Init failed: {r.status_code} {r.text}"}
    init_data = r.json()
    upload_url = init_data.get("data", {}).get("upload_url")
    publish_id = init_data.get("data", {}).get("publish_id")
    if not upload_url:
        return {"error": f"No upload_url: {init_data}"}

    # 2. Upload file
    with open(video_path, "rb") as f:
        video_data = f.read()
    upload_headers = {
        "Content-Type": "video/mp4",
        "Content-Range": f"bytes 0-{video_size-1}/{video_size}"
    }
    r = requests.put(upload_url, data=video_data, headers=upload_headers)
    if r.status_code not in (200, 201, 206):
        return {"error": f"Upload failed: {r.status_code} {r.text}"}

    # 3. Post to inbox
    post_url = f"{API_BASE}/post/publish/inbox/video/post/"
    post_body = {
        "publish_id": publish_id,
        "post_mode": "PUBLISH_TO_INBOX",  # user reviews & posts
        "caption": caption[:2200],
        "disable_duet": False,
        "disable_comment": False,
        "disable_stitch": False,
        "brand_content_toggle": False,
        "brand_organic_toggle": False,
    }
    r = requests.post(post_url, json=post_body, headers=headers)
    if r.status_code != 200:
        return {"error": f"Post failed: {r.status_code} {r.text}"}
    
    result = r.json()
    return {
        "status": "inbox",
        "publish_id": publish_id,
        "message": "Video masuk inbox TikTok. Buka app TikTok > Inbox untuk review & posting."
    }

if __name__ == '__main__':
    if WORK_DIR is None:
        print("ERROR: Set PODCAST_WORK_DIR, e.g. PODCAST_WORK_DIR=/tmp/podcast-clips/episode-id")
        sys.exit(1)
    if len(sys.argv) < 2:
        print("Usage: python tiktok_upload.py <clip_num>")
        print("       python tiktok_upload.py all")
        sys.exit(1)

    caps = json.load(open(CAPTIONS_FILE))

    if sys.argv[1] == 'all':
        for idx in sorted(caps.keys(), key=int):
            clip_file = os.path.join(CLIPS_DIR, caps[idx]['clip'])
            if os.path.exists(clip_file):
                cap_text = caps[idx]['caption'].strip()
                r = upload_clip(clip_file, cap_text, idx)
                if 'error' in r:
                    print(f"  ❌ {idx}. {r['error']}")
                else:
                    print(f"  ✅ {idx}. {caps[idx]['title']} — {r['status']}")
    else:
        idx = sys.argv[1]
        if idx not in caps:
            print(f"Clip {idx} not found")
            sys.exit(1)
        cap_text = caps[idx]['caption'].strip()
        r = upload_clip(os.path.join(CLIPS_DIR, caps[idx]['clip']), cap_text, idx)
        if 'error' in r:
            print(f"❌ {r['error']}")
        else:
            print(f"✅ {r['message']}")
