#!/usr/bin/env python3
"""Advance queued TOP 5 video posts to Meta Threads."""

import fcntl
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_DIR))

from app.admin_store import database_path
from app.threads_store import claim_post
from app.threads_publishing import process_post


def process_due(limit=20, processor=None):
    processor = processor or process_post
    processed = 0
    while processed < limit:
        item = claim_post()
        if item is None:
            break
        processor(item)
        processed += 1
    return processed


def main():
    lock_path = database_path().with_suffix(".threads.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        print(f"Processed {process_due()} Threads TOP 5 item(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
