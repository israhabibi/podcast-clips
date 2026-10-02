#!/usr/bin/env python3
"""Batch runner: process up to 10 new Bocor Alus episodes via run_one_episode.py.
State/log: /tmp/podcast-clips/batch10_state.json + batch10.log (append per step).
Resume-friendly: skips episodes whose workdir already has clips + captions.json.
"""
import json, subprocess, sys, os, time, re, urllib.request
from pathlib import Path

REPO = Path("/home/isra/podcast-clips")
PY = str(REPO / ".venv/bin/python")
RUNNER = str(REPO / "run_one_episode.py")
STATE = Path("/tmp/podcast-clips/batch10_state.json")
LOG = Path("/tmp/podcast-clips/batch10.log")
PLAYLIST = "PLkiSnq8pdz9T3QQVbczz4XVgsHjjJK3vd"
TARGET = 10

def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")

def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"episodes": {}, "done": [], "failed": []}

def save_state(st):
    STATE.write_text(json.dumps(st, indent=2, ensure_ascii=False))

def fetch_playlist(limit=20):
    url = f"https://www.youtube.com/feeds/videos.xml?playlist_id={PLAYLIST}"
    xml = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read().decode()
    out = []
    for e in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
        vid = re.search(r"<yt:videoId>(.*?)</yt:videoId>", e).group(1)
        title = re.search(r"<title>(.*?)</title>", e).group(1)
        out.append((vid, title))
        if len(out) >= limit:
            break
    return out

# already-processed video ids (workspace dirs + admin db)
PROCESSED = {"rLfVk3ZN7S4", "Go_ZovP5Hd0", "lslu0_8OynQ", "jh-kTEMWWvY", "sib_LmWjXMU"}

def main():
    st = load_state()
    # refresh candidate list each run (new videos may appear)
    if not st.get("queue"):
        vids = []
        for vid, title in fetch_playlist():
            if vid not in PROCESSED and vid not in st["done"] and vid not in st["failed"]:
                vids.append((vid, title))
            if len(vids) >= TARGET - len(st["done"]):
                break
        st["queue"] = [{"id": v, "title": t} for v, t in vids]
        save_state(st)

    log(f"batch start: {len(st['queue'])} queued, {len(st['done'])} done, {len(st['failed'])} failed")

    for ep in list(st["queue"]):
        vid, title = ep["id"], ep["title"]
        log(f"START {vid} | {title}")
        st["episodes"][vid] = {"title": title, "status": "running", "started": time.strftime("%Y-%m-%d %H:%M:%S")}
        save_state(st)
        try:
            r = subprocess.run([PY, RUNNER, f"https://www.youtube.com/watch?v={vid}", "bocor-alus", title], cwd=str(REPO), capture_output=True, text=True, timeout=5400)
            tail = (r.stdout + r.stderr).strip().splitlines()[-5:]
            if r.returncode == 0:
                st["episodes"][vid].update(status="done", finished=time.strftime("%Y-%m-%d %H:%M:%S"))
                st["done"].append(vid)
                st["queue"].remove(ep)
                log(f"DONE {vid} | {title} | {' / '.join(tail[-2:])}")
            else:
                st["episodes"][vid].update(status="failed", error=" / ".join(tail[-2:]))
                st["failed"].append(vid)
                st["queue"].remove(ep)
                log(f"FAILED {vid} | {title} | {' / '.join(tail[-2:])}")
        except subprocess.TimeoutExpired:
            st["episodes"][vid].update(status="failed", error="timeout 90min")
            st["failed"].append(vid)
            st["queue"].remove(ep)
            log(f"FAILED {vid} | timeout")
        except Exception as exc:
            st["episodes"][vid].update(status="failed", error=str(exc)[:200])
            st["failed"].append(vid)
            st["queue"].remove(ep)
            log(f"FAILED {vid} | {exc}")
        save_state(st)

    total = len(st["done"]) + len(st["failed"])
    log(f"batch end: done={len(st['done'])} failed={len(st['failed'])} total={total}/10")
    return 0

if __name__ == "__main__":
    sys.exit(main())
