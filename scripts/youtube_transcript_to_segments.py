#!/usr/bin/env python3
"""Convert YouTube timestamped transcript text to segments JSON for curate.py.

Usage: uv run python youtube_transcript_to_segments.py < transcript.txt > transcript.json

Input format (from fetch_transcript.py --timestamps --text-only --language id,en):
    0:00 Purbaya kemudian menyampaikan kepada
    0:02 Prabowo kalau eh ya dia melakukan rotasi
    0:07 tapi kemudian ee ibaratnya dihadang lah

Output: [{start, end, text}] array written to transcript.json
"""
import sys, re, json, os
from pathlib import Path

lines = sys.stdin.read().strip().split("\n")
segments = []
for line in lines:
    line = line.strip()
    if not line:
        continue
    m = re.match(r"^(\d+):(\d+)\s+(.*)", line)
    if m:
        minutes, secs, text = int(m.group(1)), int(m.group(2)), m.group(3).strip()
        if text and not (text.startswith("[") and text.endswith("]")):
            segments.append({"start": minutes * 60 + secs, "text": text})

# Merge same-timestamp lines (YouTube splits long utterances)
merged = []
for s in segments:
    if merged and merged[-1]["start"] == s["start"]:
        merged[-1]["text"] += " " + s["text"]
    else:
        merged.append(s)

# Estimate end times from next segment's start
for i, s in enumerate(merged):
    if i < len(merged) - 1:
        s["end"] = merged[i + 1]["start"]
    else:
        s["end"] = s["start"] + 3

output_file = Path(os.environ.get("PODCAST_TRANSCRIPT_FILE", "work/ep1/transcript.json"))
output_file.parent.mkdir(parents=True, exist_ok=True)
json.dump(merged, open(output_file, "w"), ensure_ascii=False, indent=2)
print(f"{len(merged)} segments written to {output_file}", file=sys.stderr)
