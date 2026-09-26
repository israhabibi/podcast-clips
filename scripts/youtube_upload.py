#!/usr/bin/env python3
"""Upload clip to YouTube Shorts."""
import json, os, re, sys
from pathlib import Path
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

REPO_DIR = Path(__file__).resolve().parents[1]
WORK_DIR_VALUE = os.environ.get("PODCAST_WORK_DIR")
WORK_DIR = Path(WORK_DIR_VALUE) if WORK_DIR_VALUE else None
TOKEN_FILE = Path(os.environ.get("YOUTUBE_TOKEN_FILE", REPO_DIR / "app" / "youtube_token.json"))
CLIPS_DIR = WORK_DIR / "clips" if WORK_DIR else None
CAPTIONS_FILE = os.path.join(CLIPS_DIR, "captions.json") if CLIPS_DIR else None

def upload_clip(video_path, title, description, tags=None):
    if not os.path.exists(TOKEN_FILE):
        return {"error": "Belum OAuth. Buka https://clips.gcp.my.id/auth dulu."}
    
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, 
        ['https://www.googleapis.com/auth/youtube'])
    
    youtube = build('youtube', 'v3', credentials=creds)
    
    # Strip leading newlines from caption (caption.py prefixes them)
    clean_desc = description.strip()
    
    body = {
        'snippet': {
            'title': (title + ' #Shorts')[:100],
            'description': clean_desc,
            'tags': tags or ['shorts', 'podcast', 'tempo', 'indonesia'],
            'categoryId': '25'  # News & Politics
        },
        'status': {
            'privacyStatus': 'public',
            'selfDeclaredMadeForKids': False
        }
    }
    
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
    
    request = youtube.videos().insert(
        part='snippet,status',
        body=body,
        media_body=media
    )
    
    response = request.execute()
    video_id = response['id']
    
    # MANDATORY: update description with full caption (includes news links)
    # insert() does not reliably embed all metadata
    youtube.videos().update(
        part='snippet',
        body={
            'id': video_id,
            'snippet': {
                'description': clean_desc,
            }
        }
    ).execute()
    
    return {
        "video_id": video_id,
        "url": f"https://youtube.com/watch?v={response['id']}",
        "title": response['snippet']['title']
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
    
    caps = json.load(open(CAPTIONS_FILE))
    
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
