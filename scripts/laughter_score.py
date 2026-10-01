#!/usr/bin/env python3
"""Detect laughter/applause bursts via loudness analysis (Clips Kitty-style audio signal, CPU-only).
Writes <workdir>/laughter.json: {"bursts": [[start, end, peak_db], ...], "baseline": x, "std": y}
curate.py reads it and tells the LLM which transcript ranges had the room exploding.
Usage: python laughter_score.py <workdir>
"""
import json, re, statistics, subprocess, sys
from pathlib import Path

W = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
SRC = W / "source.mp4"
OUT = W / "laughter.json"

# 1. momentary loudness timeline (0.1s resolution)
subprocess.run([
    "/usr/bin/ffmpeg", "-y", "-i", str(SRC),
    "-af", "ebur128=metadata=1,ametadata=print:key=lavfi.r128.M:file=/tmp/laughter_loud.txt",
    "-f", "null", "-"
], capture_output=True)

lines = open("/tmp/laughter_loud.txt").read().splitlines()
vals = []
for i in range(len(lines) - 1):
    tm = re.match(r"frame:\d+\s+pts:\d+\s+pts_time:([\d.]+)", lines[i])
    lm = re.match(r"lavfi\.r128\.M=(-?[\d.]+)", lines[i + 1])
    if tm and lm:
        vals.append((float(tm.group(1)), float(lm.group(1))))

per_sec = {}
for tsec, lufs in vals:
    per_sec.setdefault(int(tsec), []).append(lufs)
sec = {s: statistics.mean(v) for s, v in per_sec.items() if v}
louds = sorted(sec.items())

baseline = statistics.median([v for _, v in louds])
std = statistics.pstdev([v for _, v in louds])

# 2. burst detection: >=2s sustained above baseline+0.6*std
bursts = []
start = None
for s, v in louds:
    loud = v > baseline + 0.6 * std
    if loud and start is None:
        start = s
    elif not loud and start is not None:
        if s - start >= 2:
            peak = max(sec[i] for i in range(start, s) if i in sec)
            bursts.append([start, s, round(peak - baseline, 1)])
        start = None
if start is not None:
    bursts.append([start, louds[-1][0], round(max(sec[i] for i in range(start, louds[-1][0]) if i in sec) - baseline, 1)])

OUT.write_text(json.dumps({"bursts": bursts, "baseline": round(baseline, 1), "std": round(std, 1)}))
print(f"laughter.json: {len(bursts)} bursts (baseline {baseline:.1f}dB, std {std:.1f})")
