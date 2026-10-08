#!/usr/bin/env python3
"""Cut klip dari clips.json: crop 9:16 + subtitle burn-in gaya TikTok."""
import json, subprocess, os, sys
from pathlib import Path
from subtitle_timing import subtitle_chunks
from clip_quality import probe_source_duration, validate_clip_ranges

if len(sys.argv) < 2:
    sys.exit("Usage: python cut.py <source-video>")
EP = Path(sys.argv[1])
WORK_DIR_VALUE = os.environ.get("PODCAST_WORK_DIR")
if not WORK_DIR_VALUE:
    sys.exit("PODCAST_WORK_DIR is required, e.g. /tmp/podcast-clips/episode-id")
WORK_DIR = Path(WORK_DIR_VALUE)
CLIPS_DIR = WORK_DIR / "clips"
try:
    with open(WORK_DIR / "clips.json", encoding="utf-8") as f:
        clips = json.load(f)
    with open(WORK_DIR / "transcript.json", encoding="utf-8") as f:
        segs = json.load(f)
    source_duration = probe_source_duration(EP)
    clips = validate_clip_ranges(clips, segs, source_duration)
except (OSError, json.JSONDecodeError, ValueError) as exc:
    sys.exit(f"Invalid clip input: {exc}")

CLIPS_DIR.mkdir(parents=True, exist_ok=True)

FONT = "DejaVuSans-Bold"
ok = 0
failures = []
for i, c in enumerate(clips, 1):
    start, end = c["start"], c["end"]
    dur = end - start
    # subtitle segmen dalam rentang klip
    subs = subtitle_chunks(segs, start, end)
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
    out_name = str(c.get("filename")) if isinstance(c.get("filename"), str) and c.get("filename").strip() else f"clip{i:02d}.mp4"
    if Path(out_name).name != out_name:
        out_name = Path(out_name).name
    out_path = CLIPS_DIR / out_name
    tmp_out = CLIPS_DIR / f".{out_path.stem}.tmp{out_path.suffix}"
    out = str(out_path)
    vf = (f"crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920,"
          f"ass={subs_file}:fontsdir=/usr/share/fonts/truetype/dejavu")
    try:
        r = subprocess.run(["ffmpeg", "-y", "-ss", str(start), "-t", str(dur),
            "-i", str(EP), "-vf", vf, "-c:v", "libx264", "-preset", "medium",
            "-crf", "23", "-c:a", "aac", "-b:a", "128k", str(tmp_out)],
            capture_output=True, text=True)
        if r.returncode == 0 and tmp_out.is_file() and tmp_out.stat().st_size > 0:
            os.replace(tmp_out, out_path)
            ok += 1
            print(f"OK {out} ({dur:.0f}s) - {c['title']}")
        else:
            failures.append(f"Clip {i} failed: {r.stderr[-300:]}")
            print(f"FAIL {out}: {r.stderr[-300:]}")
    except OSError as exc:
        failures.append(f"Clip {i} failed to start: {exc}")
        print(f"FAIL {out}: {exc}")
    finally:
        tmp_out.unlink(missing_ok=True)
print(f"\n{ok}/{len(clips)} clips done")
if failures:
    sys.exit(1)
