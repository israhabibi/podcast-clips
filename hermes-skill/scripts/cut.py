#!/usr/bin/env python3
"""Cut klip dari clips.json: crop 9:16 + subtitle burn-in gaya TikTok."""
import json, subprocess, os, sys
from pathlib import Path

if len(sys.argv) < 2:
    sys.exit("Usage: python cut.py <source-video>")
EP = Path(sys.argv[1])
WORK_DIR_VALUE = os.environ.get("PODCAST_WORK_DIR")
if not WORK_DIR_VALUE:
    sys.exit("PODCAST_WORK_DIR is required, e.g. /tmp/podcast-clips/episode-id")
WORK_DIR = Path(WORK_DIR_VALUE)
CLIPS_DIR = WORK_DIR / "clips"
CLIPS_DIR.mkdir(parents=True, exist_ok=True)

with open(WORK_DIR / "clips.json") as f:
    clips = json.load(f)
with open(WORK_DIR / "transcript.json") as f:
    segs = json.load(f)

FONT = "DejaVuSans-Bold"
ok = 0
for i, c in enumerate(clips, 1):
    start, end = float(c["start"]), min(float(c["end"]), float(c["start"]) + 75)
    dur = end - start
    # subtitle segmen dalam rentang klip
    subs = []
    for s in segs:
        if s["end"] <= start or s["start"] >= end:
            continue
        st = max(s["start"], start) - start
        en = min(s["end"], end) - start
        # pecah text jadi max ~4 kata per tampilan (gaya TikTok)
        words = s["text"].split()
        n_words = max(len(words), 1)
        for j in range(0, n_words, 4):
            chunk = " ".join(words[j:min(j+4, n_words)])
            if chunk.strip():
                chunk_start = st + (en - st) * j / n_words
                chunk_end = st + (en - st) * min(j + 4, n_words) / n_words
                subs.append((chunk_start, chunk_end, chunk))
    # generate ASS subtitle
    ass = ["[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "",
           "[V4+ Styles]",
           "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
           "Style: Default,DejaVu Sans,72,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,60,60,300,1", "",
           "[Events]",
           "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    def ts(t):
        hh=int(t//3600); m=int(t%3600//60); s=t%60
        return f"{hh}:{m:02d}:{s:05.2f}"
    for st, en, txt in subs:
        ass.append(f"Dialogue: 0,{ts(st)},{ts(en)},Default,,0,0,0,,{txt}")
    subs_file = WORK_DIR / "subs.ass"
    subs_file.write_text("\n".join(ass))
    out = str(CLIPS_DIR / f"clip{i:02d}.mp4")
    vf = (f"crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920,"
          f"ass={subs_file}:fontsdir=/usr/share/fonts/truetype/dejavu")
    r = subprocess.run(["ffmpeg", "-y", "-ss", str(start), "-t", str(dur),
        "-i", str(EP), "-vf", vf, "-c:v", "libx264", "-preset", "medium",
        "-crf", "23", "-c:a", "aac", "-b:a", "128k", out],
        capture_output=True, text=True)
    if r.returncode == 0:
        ok += 1
        print(f"OK {out} ({dur:.0f}s) - {c['title']}")
    else:
        print(f"FAIL {out}: {r.stderr[-300:]}")
print(f"\n{ok}/{len(clips)} clips done")
