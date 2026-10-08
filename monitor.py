#!/usr/bin/env python3
"""Monitor Tempo podcast playlists for new episodes with explicit lifecycle state.

Lifecycle states: discovered -> in_progress -> completed (or failed -> in_progress for retry).
Backward-compatible: still prints NEW:<show>:<video_id>:<title> for existing consumers.
Existing tempo_podcast_processed.txt entries are migrated as legacy "completed" records.

Usage:
  python monitor.py                    # discover + print NEW lines for unprocessed
  python monitor.py --mark-completed <VIDEO_ID>
  python monitor.py --mark-failed <VIDEO_ID> [--error "message"]
  python monitor.py --retry-failed     # sets failed entries back to discovered
"""
import argparse
import json
import os
import sys
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


PLAYLISTS = {
    "jelasin-dong": "PLkiSnq8pdz9TC0rHIiguTsgelilKeR3pw",
    "bocor-alus": "PLkiSnq8pdz9T3QQVbczz4XVgsHjjJK3vd",
    "tukang-kupas": "PLkiSnq8pdz9SBWNzd65VKlI4uzLv4_p41",
}

LEGACY_STATE_FILE = Path(os.path.expanduser("~/.hermes/cache/scratch/tempo_podcast_processed.txt"))
STATE_DIR = Path(os.path.expanduser("~/.hermes/cache/scratch/tempo_podcast_state"))
STATE_FILE = STATE_DIR / "episodes.json"
LOCK_FILE = STATE_DIR / "lock"
LOCK_TIMEOUT = 30

ALLOWED_STATES = ("discovered", "in_progress", "completed", "failed")
ALLOWED_TRANSITIONS = {
    "discovered": {"in_progress"},
    "in_progress": {"completed", "failed"},
    "failed": {"in_progress", "discovered"},
    "completed": set(),
}


def _acquire_lock():
    """Simple file-based lock to prevent overlapping cron runs corrupting state."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + LOCK_TIMEOUT
    while time.time() < deadline:
        try:
            fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                age = time.time() - LOCK_FILE.stat().st_mtime
                if age > LOCK_TIMEOUT * 2:
                    LOCK_FILE.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            time.sleep(0.5)
    return False


def _release_lock():
    LOCK_FILE.unlink(missing_ok=True)


def _migrate_legacy_ids():
    """Read old flat processed.txt and promote those IDs to completed records in JSON state."""
    migrated = 0
    if not LEGACY_STATE_FILE.is_file():
        return migrated
    try:
        with LEGACY_STATE_FILE.open("r", encoding="utf-8") as fh:
            legacy_ids = [line.strip() for line in fh if line.strip()]
    except OSError:
        return migrated
    if not legacy_ids:
        return migrated
    state = _load_state_raw()
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for vid in legacy_ids:
        if vid not in state:
            state[vid] = {
                "video_id": vid,
                "podcast": "unknown",
                "published": "",
                "status": "completed",
                "attempts": 1,
                "last_error": "",
                "updated_at": ts,
                "legacy": True,
            }
            migrated += 1
    if migrated:
        _write_state_raw(state)
    return migrated


def _load_state_raw():
    if not STATE_FILE.is_file():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _write_state_raw(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    descriptor, tmp_path = tempfile.mkstemp(prefix=".state-", dir=str(STATE_DIR))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(state, output, indent=2, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(tmp_path, STATE_FILE)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass


def _fetch_playlist(podcast, playlist_id):
    ns = {
        "yt": "http://www.youtube.com/xml/schemas/2015",
        "media": "http://search.yahoo.com/mrss/",
        "atom": "http://www.w3.org/2005/Atom",
    }
    try:
        with urllib.request.urlopen(
            f"https://www.youtube.com/feeds/videos.xml?playlist_id={playlist_id}", timeout=15
        ) as resp:
            tree = ET.parse(resp)
    except Exception as exc:
        print(f"ERROR:{podcast}:{exc}", file=sys.stderr)
        return []
    entries = []
    for entry in tree.findall("atom:entry", ns):
        vid_el = entry.find("yt:videoId", ns)
        title_el = entry.find("atom:title", ns)
        pub_el = entry.find("atom:published", ns)
        if vid_el is None or not vid_el.text:
            continue
        entries.append({
            "video_id": vid_el.text,
            "title": (title_el.text or "").strip() if title_el is not None else "",
            "published": (pub_el.text or "").strip() if pub_el is not None else "",
            "podcast": podcast,
        })
    return entries


def discover():
    """Scan playlists, record newly seen entries as discovered, and print NEW lines."""
    if not _acquire_lock():
        print("ERROR: could not acquire state lock within timeout", file=sys.stderr)
        sys.exit(2)
    try:
        _migrate_legacy_ids()
        state = _load_state_raw()
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        feed_entries = []
        for podcast, playlist_id in PLAYLISTS.items():
            feed_entries.extend(_fetch_playlist(podcast, playlist_id))
        to_report = []
        for entry in feed_entries:
            vid = entry["video_id"]
            existing = state.get(vid)
            if existing and existing.get("status") in ("completed", "in_progress"):
                continue
            if not existing:
                record = {
                    "video_id": vid,
                    "podcast": entry["podcast"],
                    "published": entry["published"],
                    "title": entry["title"],
                    "status": "discovered",
                    "attempts": 0,
                    "last_error": "",
                    "updated_at": ts,
                }
                state[vid] = record
            elif existing.get("status") == "failed":
                existing["updated_at"] = ts
                existing.setdefault("published", entry["published"])
                existing.setdefault("title", entry["title"])
                existing.setdefault("podcast", entry["podcast"])
            to_report.append((
                entry["published"] or "",
                vid,
                entry["title"],
                entry["podcast"],
            ))
        _write_state_raw(state)
        if to_report:
            to_report.sort(reverse=True)
            for pub, vid, title, show in to_report:
                print(f"NEW:{show}:{vid}:{title[:80]}")
        else:
            print("NO_NEW")
    finally:
        _release_lock()


def _set_status(video_id, status, error=""):
    if status not in ALLOWED_STATES:
        print(f"ERROR: invalid status {status}", file=sys.stderr)
        sys.exit(2)
    if not _acquire_lock():
        print("ERROR: could not acquire state lock within timeout", file=sys.stderr)
        sys.exit(2)
    try:
        _migrate_legacy_ids()
        state = _load_state_raw()
        record = state.get(video_id)
        if record is None:
            print(f"ERROR: video_id {video_id} not in state; run discover first or provide full data via a pipeline.", file=sys.stderr)
            sys.exit(1)
        current = record.get("status")
        if status not in ALLOWED_TRANSITIONS.get(current, set()):
            print(f"ERROR: invalid lifecycle transition {current} -> {status} for {video_id}", file=sys.stderr)
            sys.exit(1)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if status == "in_progress":
            record["attempts"] = int(record.get("attempts", 0)) + 1
        if error:
            record["last_error"] = str(error)[:500]
        elif status == "completed":
            record["last_error"] = ""
        record["status"] = status
        record["updated_at"] = ts
        _write_state_raw(state)
        print(f"OK:{video_id}:{status}")
    finally:
        _release_lock()


def _retry_failed():
    if not _acquire_lock():
        print("ERROR: could not acquire state lock within timeout", file=sys.stderr)
        sys.exit(2)
    try:
        _migrate_legacy_ids()
        state = _load_state_raw()
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        count = 0
        for record in state.values():
            if record.get("status") == "failed":
                record["status"] = "discovered"
                record["updated_at"] = ts
                count += 1
        if count:
            _write_state_raw(state)
        print(f"RESET:{count}")
    finally:
        _release_lock()


def main():
    parser = argparse.ArgumentParser(description="Tempo podcast monitor with lifecycle state")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--mark-completed", metavar="VIDEO_ID", help="Mark an episode completed")
    group.add_argument("--mark-in-progress", metavar="VIDEO_ID", help="Mark an episode in_progress (increments attempts)")
    group.add_argument("--mark-failed", metavar="VIDEO_ID", help="Mark an episode failed")
    group.add_argument("--retry-failed", action="store_true", help="Reset all failed entries to discovered")
    parser.add_argument("--error", default="", help="Message stored with --mark-failed")
    args = parser.parse_args()

    if args.mark_completed:
        _set_status(args.mark_completed, "completed")
    elif args.mark_in_progress:
        _set_status(args.mark_in_progress, "in_progress")
    elif args.mark_failed:
        _set_status(args.mark_failed, "failed", error=args.error)
    elif args.retry_failed:
        _retry_failed()
    else:
        discover()


if __name__ == "__main__":
    main()
