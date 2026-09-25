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
clips = json.load(open(WORK_DIR / "clips.json"))
segs = json.load(open(WORK_DIR / "transcript.json"))
FONT = "DejaVuSans-Bold"
ok = 0
for i, c in enumerate(clips, 1):
    start, end = float(c["start"]), min(float(c["end"]), float(c["start"]) + 75)
    dur = end - start
    # subtitle segmen dalam rentang klip
    subs = []
    t = start
    for s in segs:
        if s["end"] <= start or s["start"] >= end:
            continue
        st = max(s["start"], start) - start
        en = min(s["end"], end) - start
        # pecah text jadi max ~4 kata per tampilan (gaya TikTok)
        words = s["text"].split()
        for j in range(0, len(words), 4):
            chunk = " ".join(words[j:j+4])
            if chunk.strip():
                subs.append((st + (en-st)*j/max(len(words),1), st + (en-st)*(j+4)/max(len(words),1), chunk))
    # generate ASS subtitle
    ass = ["[Script Info]", "PlayResX: 1080", "PlayResY: 1920", "",
           "[V4 Styles]",
           "Format: Name,Fontsize,PrimaryColour,OutlineColour,BackColour,Bold,Outline,Shadow,Alignment,MarginL,MarginR,MarginV",
           "Style: Default,64,&H00FFFFFF,&H00000000,&H80000000,1,4,0,2,60,60,300", "",
           "[Events]", "Format: Layer,Start,End,Text"]
    def ts(t):
        h=int(t//3600); m=int(t%3600//60); s=t%60
        return f"{h}:{m:02d}:{s:05.2f}"
    for st, en, txt in subs:
        ass.append(f"Dialogue: 0,{ts(st)},{ts(en)},Default,,0,0,0,,{txt}")
    subs_file = WORK_DIR / "subs.ass"
    subs_file.write_text("\n".join(ass))
    out = str(CLIPS_DIR / f"clip{i:02d}.mp4")
    vf = (f"crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920,"
          f"ass=subs.ass:fontsdir=/usr/share/fonts/truetype/dejavu")
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
