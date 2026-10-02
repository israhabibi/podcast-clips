#!/usr/bin/env python3
"""Upload clip ke YouTube dengan anti-duplikat (cek judul via API dulu) + catat DB wajib.

Usage: python3 yt_upload_safe.py <workdir> [--dry-run]

Environment overrides:
  YOUTUBE_TOKEN_FILE  - path to OAuth token (default: /home/isra/podcast-clips/app/youtube_token.json)
  ADMIN_DB_PATH       - path to admin SQLite    (default: /home/isra/podcast-clips/app/data/admin.sqlite3)
"""
import json, os, sys, time, re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import requests as _requests
import sqlite3

REPO_ROOT = Path(__file__).resolve().parents[1]
TOKEN_FILE = Path(os.environ.get("YOUTUBE_TOKEN_FILE", REPO_ROOT / "app" / "youtube_token.json"))
DB_PATH = Path(os.environ.get("ADMIN_DB_PATH", REPO_ROOT / "app" / "data" / "admin.sqlite3"))

KNOWN_PODCAST_SLUGS = ("jelasin-dong", "bocor-alus", "tukang-kupas")

def norm(t):
    t = t.lower()
    t = re.sub(r'#\S+', '', t)
    t = re.sub(r'[^a-z0-9\s]', '', t)
    return ' '.join(t.split())


def get_existing_titles(yt):
    titles = set()
    chan = yt.channels().list(part='contentDetails', mine=True).execute()
    up = chan['items'][0]['contentDetails']['relatedPlaylists']['uploads']
    page = None
    while True:
        kw = {'playlistId': up, 'maxResults': 50}
        if page:
            kw['pageToken'] = page
        pl = yt.playlistItems().list(part='snippet', **kw).execute()
        for it in pl['items']:
            titles.add(norm(it['snippet']['title']))
        page = pl.get('nextPageToken')
        if not page:
            break
    return titles


def _refresh_headers(creds):
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleRequest())
    return {"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"}


def _update_metadata_raw(creds, video_id, title, description, tags, category_id):
    """Update snippet via raw REST (googleapiclient videos().update silently drops tags)."""
    headers = _refresh_headers(creds)
    body = {
        "id": video_id,
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": str(category_id),
            "tags": list(tags),
        },
    }
    url = "https://www.googleapis.com/youtube/v3/videos?part=snippet"
    resp = _requests.put(url, json=body, headers=headers, timeout=45)
    if resp.status_code != 200:
        raise RuntimeError(f"Metadata update HTTP {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    returned = data.get("items", [{}])[0].get("snippet", {}) if isinstance(data, dict) else {}
    returned_tags = returned.get("tags")
    if not isinstance(returned_tags, list) or len(returned_tags) < max(1, min(len(tags), 2)):
        raise RuntimeError(f"Tags not persisted after update: expected {tags!r}, got {returned_tags!r}")


def _infer_podcast(workdir_name):
    name = workdir_name.lower()
    for slug in KNOWN_PODCAST_SLUGS:
        if slug in name or slug.replace("-", "") in name:
            return slug
    return None


def upload(workdir, dry=False):
    workdir_path = Path(workdir).expanduser().resolve()
    caps_path = workdir_path / "captions.json"
    if not caps_path.is_file():
        print(f"ERROR: captions.json tidak ditemukan di {workdir_path}")
        sys.exit(1)
    caps = json.loads(caps_path.read_text(encoding="utf-8"))
    if not isinstance(caps, dict):
        print("ERROR: captions.json harus objek dengan key nomor clip")
        sys.exit(1)

    if not TOKEN_FILE.is_file():
        print(f"ERROR: token file tidak ada. Jalankan OAuth dulu: {TOKEN_FILE}")
        sys.exit(1)
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), ['https://www.googleapis.com/auth/youtube'])
    yt = build('youtube', 'v3', credentials=creds)
    existing = get_existing_titles(yt)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    _ensure_tables(con)

    ok = fail = skip = 0
    workdir_base = workdir_path.name
    inferred_podcast = _infer_podcast(workdir_base) or "unknown"

    for idx in sorted(caps, key=lambda k: int(k) if str(k).isdigit() else 0):
        c = caps[idx]
        if not isinstance(c, dict):
            print(f"SKIP {idx}: caption entry bukan dict"); skip += 1; continue
        title = (str(c.get('title', f'Clip {idx}')) + ' #Shorts')[:100]
        clip_rel = c.get('clip', '')
        if not isinstance(clip_rel, str) or not clip_rel:
            print(f"SKIP {idx}: field 'clip' kosong"); skip += 1; continue
        vf = workdir_path / "clips" / clip_rel
        if not vf.is_file():
            print(f"SKIP {idx}: file hilang {vf}"); skip += 1; continue
        if norm(title) in existing:
            print(f"SKIP {idx}: duplikat judul di channel — {title[:50]}"); skip += 1; continue
        if dry:
            print(f"WOULD UPLOAD {idx}: {title[:60]}")
            continue
        description = str(c.get('caption', '')).strip()[:4900]
        tags = ['shorts', 'podcast', 'tempo', 'indonesia', inferred_podcast, 'berita', 'politik', 'viral', 'fyp', 'news', 'video', 'opini', 'analisis', 'terkini', 'podcastindonesia']
        deduped_tags = []
        for t in tags:
            if isinstance(t, str) and t.strip() and t not in deduped_tags:
                deduped_tags.append(t.strip())
        category_id = '25'
        try:
            body = {
                'snippet': {
                    'title': title,
                    'description': description,
                    'tags': deduped_tags,
                    'categoryId': category_id,
                },
                'status': {
                    'privacyStatus': 'public',
                    'selfDeclaredMadeForKids': False,
                },
            }
            media = MediaFileUpload(str(vf), chunksize=-1, resumable=True)
            req = yt.videos().insert(part='snippet,status', body=body, media_body=media)
            resp = req.execute()
            vid = resp.get('id')
            if not vid:
                raise RuntimeError(f"insert response missing id: {resp!r}")
            _update_metadata_raw(creds, vid, title, description, deduped_tags, category_id)
            existing.add(norm(title))
            try:
                with con:
                    con.execute(
                        "INSERT INTO youtube_uploads "
                        "(podcast, episode, clip_num, video_id, status, error, created_at) "
                        "VALUES (?, ?, ?, ?, 'uploaded', '', datetime('now')) "
                        "ON CONFLICT(podcast, episode, clip_num) DO UPDATE SET "
                        "video_id=excluded.video_id, status=excluded.status, "
                        "error=excluded.error, created_at=excluded.created_at",
                        (inferred_podcast, workdir_base, str(idx), vid),
                    )
            except sqlite3.Error as db_err:
                print(f"  WARN catat DB gagal (video tetap live): {db_err}")
            ok += 1
            print(f"OK {idx}: https://youtube.com/watch?v={vid} — {title[:50]}")
            time.sleep(2)
        except Exception as e:
            msg = str(e)
            fail += 1
            print(f"FAIL {idx}: {msg[:150]}")
            if 'quotaExceeded' in msg or 'uploadLimitExceeded' in msg or 'exceeded' in msg.lower():
                print('>>> Kuota habis, stop batch.')
                break
    try:
        con.close()
    except sqlite3.Error:
        pass
    print(f"\nSELESAI {workdir_base}: ok={ok} fail={fail} skip={skip}")


def _ensure_tables(con):
    with con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS youtube_uploads (
                id INTEGER PRIMARY KEY,
                podcast TEXT NOT NULL,
                episode TEXT NOT NULL,
                clip_num TEXT NOT NULL,
                video_id TEXT,
                status TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(podcast, episode, clip_num)
            )"""
        )


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 yt_upload_safe.py <workdir> [--dry-run]")
        sys.exit(1)
    wd = sys.argv[1]
    dry = '--dry-run' in sys.argv
    upload(wd, dry)
