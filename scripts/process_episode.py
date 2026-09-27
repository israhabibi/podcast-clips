#!/usr/bin/env python3
"""Process one authenticated admin-queue submission in a detached worker."""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.admin_store import list_submissions, set_submission_status


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: process_episode.py <youtube_video_id>")

    video_id = sys.argv[1]
    item = next((row for row in list_submissions() if row["video_id"] == video_id), None)
    if item is None:
        raise SystemExit(f"No queued submission for {video_id}")

    set_submission_status(video_id, "in_progress")
    title = item["title"] or f"YouTube episode {video_id}"
    command = [
        sys.executable,
        str(REPO_ROOT / "run_one_episode.py"),
        item["url"],
        item["podcast"],
        title,
    ]
    try:
        result = subprocess.run(command, cwd=str(REPO_ROOT), check=False)
    except OSError:
        set_submission_status(video_id, "failed")
        raise
    set_submission_status(video_id, "completed" if result.returncode == 0 else "failed")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())