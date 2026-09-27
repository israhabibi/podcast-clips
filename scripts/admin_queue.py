#!/usr/bin/env python3
"""Read and update manual YouTube submissions from the local pipeline."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.admin_store import list_submissions, set_submission_status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    listing = subcommands.add_parser("list", help="Print submissions as JSON")
    listing.add_argument("--status", choices=("pending", "in_progress", "completed", "failed"))
    update = subcommands.add_parser("set-status", help="Update a submission after pipeline work")
    update.add_argument("video_id")
    update.add_argument("status", choices=("pending", "in_progress", "completed", "failed"))
    args = parser.parse_args()
    if args.command == "list":
        print(json.dumps(list_submissions(status=args.status), ensure_ascii=False, indent=2))
    else:
        try:
            set_submission_status(args.video_id, args.status)
        except ValueError as exc:
            parser.error(str(exc))


if __name__ == "__main__":
    main()
