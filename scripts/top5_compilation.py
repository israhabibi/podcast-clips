#!/usr/bin/env python3
"""TOP 5 v3: persistent 2-line header 'TOP 5 MOMEN BOCOR ALUS / PRABOWO GIBRAN' + numbered list sidebar,
active item highlighted yellow, per-segment subtitles."""
import argparse
import json, subprocess, os, re

DEFAULT_WORKDIR = "/tmp/podcast-clips/bocor-alus-jokow-prabowo-2029-scenarios"
DEFAULT_OUT = "clips/top5_compilation_v3.mp4"

parser = argparse.ArgumentParser()
parser.add_argument("workdir", nargs="?", default=DEFAULT_WORKDIR)
parser.add_argument("output", nargs="?", default=None)
parser.add_argument("--config", type=str, default=None)
args = parser.parse_args()
D = os.path.abspath(args.workdir)
SRC = os.path.join(D, "source.mp4")
OUT = os.path.abspath(args.output or os.path.join(D, DEFAULT_OUT))
FONT = os.environ.get(
    "PODCAST_FONT_FILE",
    os.path.join(os.environ.get("PODCAST_FONT_DIR", "/usr/share/fonts/truetype/dejavu"), "DejaVuSans-Bold.ttf"),
)
WHOOSH = "/usr/share/sounds/sound-icons/pisk-up.wav"
DING = "/usr/share/sounds/sound-icons/cembalo-12.wav"
job_part = re.sub(r"[^A-Za-z0-9_.-]+", "-", os.path.basename(OUT))[:120]
TMP = os.path.join(D, f".top5v3-{job_part}")
os.makedirs(TMP, exist_ok=True)
os.makedirs(os.path.dirname(OUT), exist_ok=True)

transcript = json.load(open(f"{D}/transcript.json"))

HEADER_L1 = "TOP 5 MOMEN"
HEADER_L2 = "BOCOR ALUS"
SUBHEADER = "PRABOWO vs GIBRAN"
ITEMS = [
    "Ibarat Dual Machine",
    "Makan Siang Hilirisasi",
    "Dua Prinsip di Tempo",
    "Ngobok-Ngomok Konstitusi",
    "Bergunjing Sampai 8 Poin",
]
# segments: (start, end, item_index 0-4)
SEGS = [
    (4, 12, 0),
    (107, 130, 1),
    (130, 132, 1),
    (226, 234, 2),
    (261, 282, 3),
    (282, 292, 4),
]

if args.config:
    with open(args.config, encoding="utf-8") as config_file:
        config = json.load(config_file)
    HEADER_L1 = str(config.get("header_l1", HEADER_L1))
    HEADER_L2 = str(config.get("header_l2", HEADER_L2))
    SUBHEADER = str(config.get("subheader", SUBHEADER))
    ITEMS = [item.get("label", "") if isinstance(item, dict) else str(item) for item in config["items"]]
    SEGS = [tuple(segment) for segment in config["segs"]]
    if len(ITEMS) != 5 or any(len(segment) != 3 for segment in SEGS):
        raise ValueError("TOP 5 config requires exactly five items and [start, end, item_index] segments")

def ts(t):
    h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"

def esc(s):
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\\\'")

def build_ass(idx, start, end):
    lines = []
    for s in transcript:
        if s["end"] > start + 0.3 and s["start"] < end - 0.1:
            a = max(s["start"] - start, 0)
            b = min(s["end"] - start, end - start)
            if b - a > 0.15:
                lines.append(f"Dialogue: 0,{ts(a)},{ts(b)},Sub,,0,0,0,,{s['text'].strip()}")
    ass = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,DejaVu Sans,44,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,50,50,170,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(lines) + "\n"
    p = f"{TMP}/subs{idx:02d}.ass"
    open(p, "w").write(ass)
    return p

def vf_chain(idx, seg_start, seg_end):
    active = SEGS[idx][2]
    filters = [
        "crop=ih*9/16:ih:(iw-ih*9/16)/2:0",
        "scale=720:1280",
    ]
    if idx > 0:
        filters.append("fade=t=in:st=0:d=0.25:color=white")
    # persistent 2-line header
    filters.append(f"drawbox=x=0:y=0:w=720:h=190:color=black@0.55:t=fill")
    filters.append(f"drawtext=fontfile={FONT}:text='{esc(HEADER_L1)}':fontsize=58:fontcolor=yellow:borderw=5:bordercolor=black:x=(w-text_w)/2:y=22")
    filters.append(f"drawtext=fontfile={FONT}:text='{esc(HEADER_L2)}':fontsize=58:fontcolor=yellow:borderw=5:bordercolor=black:x=(w-text_w)/2:y=92")
    filters.append(f"drawtext=fontfile={FONT}:text='{esc(SUBHEADER)}':fontsize=30:fontcolor=white:borderw=3:bordercolor=red:x=(w-text_w)/2:y=158")
    # numbered list sidebar (left), active item yellow+larger, others dimmed
    y0 = 260
    for i, item in enumerate(ITEMS):
        act = (i == active)
        fs = 38 if act else 28
        col = "yellow" if act else "white@0.55"
        bw = 4 if act else 2
        text = f"{i+1}. {esc(item)}"
        filters.append(
            f"drawtext=fontfile={FONT}:text='{text}':fontsize={fs}:fontcolor={col}:borderw={bw}:bordercolor=black:x=30:y={y0 + i*62}"
        )
    filters.append(f"subtitles='{build_ass(idx, seg_start, seg_end)}'")
    return ",".join(filters)

for idx, (s, e, _) in enumerate(SEGS):
    dur = e - s
    p = f"{TMP}/part{idx:02d}.mp4"
    cmd = ["/usr/bin/ffmpeg", "-y", "-ss", str(s), "-t", str(dur), "-i", SRC,
           "-vf", vf_chain(idx, s, e),
           "-c:v", "libx264", "-preset", "fast", "-crf", "21",
           "-c:a", "aac", "-b:a", "128k", "-ar", "44100", p]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("FAIL part", idx, r.stderr[-400:]); raise SystemExit(1)
    print(f"part{idx:02d} ok (item {SEGS[idx][2]+1})")

open(f"{TMP}/list.txt", "w").write("".join(f"file '{TMP}/part{i:02d}.mp4'\n" for i in range(len(SEGS))))
subprocess.run(["/usr/bin/ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", f"{TMP}/list.txt",
                "-c", "copy", f"{TMP}/no_sfx.mp4"], check=True, capture_output=True)

offsets = []; acc = 0
for s, e, _ in SEGS:
    offsets.append(acc); acc += (e - s)

inputs = ["-i", f"{TMP}/no_sfx.mp4"]
af = ""
for i, off in enumerate(offsets):
    inputs += ["-i", DING if i == 0 else WHOOSH]
    dm = int(max(off - 0.25, 0) * 1000)
    af += f"[{i+1}:a]adelay={dm}|{dm}[s{i}];"
amix = "".join(f"[s{i}]" for i in range(len(offsets)))
af += f"{amix}amix=inputs={len(offsets)}:normalize=0[fx];[0:a][fx]amix=inputs=2:normalize=0[aout]"
cmd = ["/usr/bin/ffmpeg", "-y"] + inputs + [
    "-filter_complex", af, "-map", "0:v", "-map", "[aout]",
    "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", OUT]
r = subprocess.run(cmd, capture_output=True, text=True)
if r.returncode != 0:
    print("FAIL mix:", r.stderr[-500:]); raise SystemExit(1)
print("final:", OUT)
print(subprocess.run(["/usr/bin/ffprobe", "-v", "quiet", "-show_entries", "format=duration,size", "-of", "csv=p=0", OUT], capture_output=True, text=True).stdout)
