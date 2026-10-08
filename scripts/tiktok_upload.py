#!/usr/bin/env python3
"""Upload clip to TikTok via Content Posting API v2."""
import hashlib, json, os, sys, requests, time
from pathlib import Path
from episode_manifest import write_manifest

REPO_DIR = Path(__file__).resolve().parents[1]
WORK_DIR_VALUE = os.environ.get("PODCAST_WORK_DIR")
WORK_DIR = Path(WORK_DIR_VALUE) if WORK_DIR_VALUE else None
TOKEN_FILE = Path(os.environ.get("TIKTOK_TOKEN_FILE", REPO_DIR / "app" / "tiktok_token.json"))
CLIPS_DIR = WORK_DIR / "clips" if WORK_DIR else None
CAPTIONS_FILE = WORK_DIR / "captions.json" if WORK_DIR else None

API_BASE = "https://open.tiktokapis.com/v2"


def _manifest_clip(filename):
    if WORK_DIR is None:
        return None
    try:
        data = json.loads((WORK_DIR / "episode_manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("status") != "ready_for_review":
        return None
    clips = data.get("clips")
    if not isinstance(clips, list):
        return None
    return next((clip for clip in clips if isinstance(clip, dict) and clip.get("filename") == filename), None)


def _manifest_matches_file(clip_entry, video_path):
    if not isinstance(clip_entry, dict) or not isinstance(clip_entry.get("sha256"), str):
        return False
    try:
        digest = hashlib.sha256(Path(video_path).read_bytes()).hexdigest()
    except OSError:
        return False
    return digest == clip_entry["sha256"]


def _record_tiktok_state(filename, publish_id, status):
    if WORK_DIR is None:
        return False
    manifest_path = WORK_DIR / "episode_manifest.json"
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    for clip_entry in data.get("clips", []):
        if isinstance(clip_entry, dict) and clip_entry.get("filename") == filename:
            platform_ids = clip_entry.setdefault("platform_upload_ids", {})
            platform_ids["tiktok"] = {"publish_id": publish_id, "status": status}
            try:
                write_manifest(WORK_DIR, data)
            except OSError:
                return False
            return True
    return False

def get_token():
    if not os.path.exists(TOKEN_FILE):
        return None, None
    data = json.load(open(TOKEN_FILE))
    return data.get("access_token"), data.get("open_id")

def upload_clip(video_path, caption, idx):
    video_path = Path(video_path)
    manifest_clip = _manifest_clip(video_path.name)
    if not _manifest_matches_file(manifest_clip, video_path):
        return {"error": "Clip is not present in the ready-for-review manifest or has changed."}
    existing_tiktok = manifest_clip.get("platform_upload_ids", {}).get("tiktok", {})
    if existing_tiktok.get("publish_id"):
        return {"error": f"TikTok inbox submission already recorded ({existing_tiktok['publish_id']}, status={existing_tiktok.get('status', 'unknown')}); not resubmitting."}
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
    r = requests.post(init_url, json=init_body, headers=headers, timeout=30)
    if r.status_code != 200:
        return {"error": f"Init failed: {r.status_code} {r.text}"}
    init_data = r.json()
    upload_url = init_data.get("data", {}).get("upload_url")
    publish_id = init_data.get("data", {}).get("publish_id")
    if not upload_url:
        return {"error": f"No upload_url: {init_data}"}
    if publish_id and not _record_tiktok_state(video_path.name, publish_id, "upload_in_progress"):
        return {"error": "Could not record TikTok publish ID; stop and reconcile before retrying."}

    # 2. Upload file
    upload_headers = {
        "Content-Type": "video/mp4",
        "Content-Range": f"bytes 0-{video_size-1}/{video_size}"
    }
    with open(video_path, "rb") as video_file:
        r = requests.put(
            upload_url, data=video_file, headers=upload_headers, timeout=(10, 300)
        )
    if r.status_code not in (200, 201, 206):
        return {"error": f"Upload failed: {r.status_code} {r.text}"}

    # 3. Post to inbox
    post_url = f"{API_BASE}/post/publish/inbox/video/post/"
    
    # Truncate caption safely (respect UTF-8 boundaries)
    cap_bytes = caption.encode('utf-8')[:2200]
    caption_truncated = cap_bytes.decode('utf-8', errors='ignore')
    
    post_body = {
        "publish_id": publish_id,
        "post_mode": "PUBLISH_TO_INBOX",  # user reviews & posts
        "caption": caption_truncated,
        "disable_duet": False,
        "disable_comment": False,
        "disable_stitch": False,
        "brand_content_toggle": False,
        "brand_organic_toggle": False,
    }
    r = requests.post(post_url, json=post_body, headers=headers, timeout=30)
    if r.status_code != 200:
        return {"error": f"Post failed: {r.status_code} {r.text}"}

    if not _record_tiktok_state(video_path.name, publish_id, "inbox"):
        return {"error": f"TikTok accepted publish ID {publish_id}, but manifest update failed; reconcile before retrying."}
    
    result = r.json()
    return {
        "status": "inbox",
        "publish_id": publish_id,
        "message": "Video masuk inbox TikTok. Buka app TikTok > Inbox untuk review & posting."
    }

def load_captions():
    if CAPTIONS_FILE is None or not CAPTIONS_FILE.is_file():
        expected = CAPTIONS_FILE or "PODCAST_WORK_DIR/captions.json"
        raise FileNotFoundError(f"Captions file not found: {expected}")
    try:
        with CAPTIONS_FILE.open(encoding="utf-8") as captions_file:
            captions = json.load(captions_file)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid captions JSON: {exc}") from exc
    if not isinstance(captions, dict):
        raise ValueError("Captions JSON must contain an object keyed by clip number.")
    for idx, item in captions.items():
        if not isinstance(item, dict) or not isinstance(item.get("clip"), str):
            raise ValueError(f"Invalid caption entry for clip {idx}.")
        if not isinstance(item.get("caption"), str):
            raise ValueError(f"Caption text is missing for clip {idx}.")
        clip_path = CLIPS_DIR / item["clip"]
        if not clip_path.is_file():
            raise FileNotFoundError(f"Clip file for {idx} not found: {clip_path}")
    return captions

def main(args=None):
    if WORK_DIR is None:
        print("ERROR: Set PODCAST_WORK_DIR, e.g. PODCAST_WORK_DIR=/tmp/podcast-clips/episode-id")
        return 1
    args = sys.argv[1:] if args is None else args
    if not args:
        print("Usage: python tiktok_upload.py <clip_num>")
        print("       python tiktok_upload.py all")
        return 1
    try:
        caps = load_captions()
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1

    if args[0] == 'all':
        failed = False
        for idx in sorted(caps.keys(), key=int):
            clip_file = CLIPS_DIR / caps[idx]['clip']
            cap_text = caps[idx]['caption'].strip()
            manifest_clip = _manifest_clip(clip_file.name)
            if not _manifest_matches_file(manifest_clip, clip_file):
                failed = True
                print(f"  ❌ {idx}. Clip not in review manifest or changed after manifest creation")
                continue
            tiktok_state = manifest_clip.get("platform_upload_ids", {}).get("tiktok", {})
            if tiktok_state.get("status") == "inbox":
                print(f"  ℹ️ {idx}. Already in TikTok inbox: {tiktok_state.get('publish_id')}")
                continue
            if tiktok_state.get("publish_id"):
                failed = True
                print(f"  ❌ {idx}. Existing TikTok publish ID needs reconciliation: {tiktok_state['publish_id']}")
                continue
            r = upload_clip(clip_file, cap_text, idx)
            if 'error' in r:
                failed = True
                print(f"  ❌ {idx}. {r['error']}")
            else:
                print(f"  ✅ {idx}. {caps[idx]['title']} — {r['status']}")
        return 1 if failed else 0
    else:
        idx = args[0]
        if idx not in caps:
            print(f"Clip {idx} not found")
            return 1
        cap_text = caps[idx]['caption'].strip()
        clip_file = CLIPS_DIR / caps[idx]['clip']
        manifest_clip = _manifest_clip(clip_file.name)
        if not _manifest_matches_file(manifest_clip, clip_file):
            print(f"Clip {idx} is not in the review manifest or has changed")
            return 1
        tiktok_state = manifest_clip.get("platform_upload_ids", {}).get("tiktok", {})
        if tiktok_state.get("status") == "inbox":
            print(f"Already in TikTok inbox: {tiktok_state.get('publish_id')}")
            return 0
        if tiktok_state.get("publish_id"):
            print(f"Existing TikTok publish ID needs reconciliation: {tiktok_state['publish_id']}")
            return 1
        r = upload_clip(clip_file, cap_text, idx)
        if 'error' in r:
            print(f"❌ {r['error']}")
            return 1
        else:
            print(f"✅ {r['message']}")
        return 0

if __name__ == '__main__':
    raise SystemExit(main())
