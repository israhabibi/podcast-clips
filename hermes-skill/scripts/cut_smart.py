#!/usr/bin/env python3
"""Cut klip dengan auto-reframe: crop 9:16 ngikutin pembicara (YuNet face detect)."""
import json, subprocess, os, sys, tempfile
from pathlib import Path
import cv2
import numpy as np
from scipy.interpolate import CubicSpline

if len(sys.argv) < 2:
    sys.exit("Usage: python cut_smart.py <source-video>")
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

det = cv2.FaceDetectorYN.create("/tmp/face_yunet.onnx", "", (320, 320), 0.6)

def face_positions(video, start, dur, samples=48):
    """Sample frame via ffmpeg (AV1-safe), return list of (t, face_cx)."""
    import subprocess as sp
    with tempfile.TemporaryDirectory() as tmp:
        r = sp.run(["ffmpeg", "-y", "-ss", str(start), "-t", str(dur),
            "-i", video, "-vf", f"fps={samples/max(dur,0.1)},scale=320:-2",
            "-q:v", "5", os.path.join(tmp, "f%04d.jpg")],
            capture_output=True, text=True)
        if r.returncode != 0:
            return [], 0, 0
        # dimensi asli via ffprobe
        pr = sp.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0", video],
            capture_output=True, text=True)
        w, h = map(int, pr.stdout.strip().split(","))
        pts = []
        frames = sorted(os.listdir(tmp))
        n = len(frames)
        if not n:
            return [], w, h
        for i, fn in enumerate(frames):
            img = cv2.imread(os.path.join(tmp, fn))
            if img is None:
                continue
            det.setInputSize((img.shape[1], img.shape[0]))
            _, faces = det.detect(img)
            if faces is not None and len(faces):
                f = max(faces, key=lambda f: f[2] * f[3])
                scale = w / img.shape[1]
                pts.append((dur * i / n, f[0] * scale + f[2] * scale / 2))
        return pts, w, h

def smooth_track(pts, dur, crop_w, w):
    """Interpolasi posisi crop-x yang SMOOTH: cubic spline + kecepatan max terbatas."""
    margin = 60
    if not pts:
        return [(0, (w - crop_w) / 2)]
    ts = [p[0] for p in pts]
    cxs = [min(max(p[1], crop_w / 2 + margin), w - crop_w / 2 - margin) for p in pts]
    # Rolling median 5-point untuk outlier rejection (loncat karena false detection)
    med = []
    for i in range(len(cxs)):
        win = cxs[max(0, i-2):i+3]
        med.append(sorted(win)[len(win)//2])
    steps = 90  # interpolation grid lebih halus (90 step vs 60)
    grid = np.linspace(0, dur, steps)
    # cubic interpolation -> transisi halus tanpa patah
    from scipy.interpolate import CubicSpline as _CubicSpline
    if len(ts) >= 4:
        cs = _CubicSpline(ts, med)
        xs = cs(grid)
    else:
        xs = np.interp(grid, ts, med)
    # Batasi kecepatan gerak (px/detik) biar gak nyonot / loncat
    max_v = 180.0  # px per second — reduced from 250 for smoother motion
    for i in range(1, len(xs)):
        dt = grid[i] - grid[i-1]
        dv = xs[i] - xs[i-1]
        lim = max_v * dt
        if abs(dv) > lim:
            xs[i] = xs[i-1] + np.sign(dv) * lim
    # Double-pass smoothing: rolling average setelah velocity clamp
    k = max(steps//15, 5)
    smoothed = np.convolve(np.pad(xs, k//2, mode="edge"), np.ones(k)/k, mode="valid")
    if len(smoothed) < len(xs):
        smoothed = np.interp(grid, np.linspace(0, 1, len(smoothed)), smoothed)
    return list(zip(grid, smoothed))

ok = 0
for i, c in enumerate(clips, 1):
    start, end = float(c["start"]), min(float(c["end"]), float(c["start"]) + 75)
    dur = end - start
    pts, w, h = face_positions(EP, start, dur)
    crop_w = int(h * 9 / 16)
    track = smooth_track(pts, dur, crop_w, w)
    print(f"clip{i:02d}: {len(pts)} face samples, track {track[0][1]:.0f}->{track[-1][1]:.0f}px")
    # subtitle
    subs = []
    for s in segs:
        if s["end"] <= start or s["start"] >= end:
            continue
        st = max(s["start"], start) - start
        en = min(s["end"], end) - start
        words = s["text"].split()
        n_words = max(len(words), 1)
        for j in range(0, n_words, 4):
            chunk = " ".join(words[j:min(j+4, n_words)])
            if chunk.strip():
                chunk_start = st + (en - st) * j / n_words
                chunk_end = st + (en - st) * min(j + 4, n_words) / n_words
                subs.append((chunk_start, chunk_end, chunk))
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
    # FFmpeg needs an interpolated x value; using a constant per interval causes visible jumps.
    exprs = []
    for j, (t, x) in enumerate(track[:-1]):
        next_t, next_x = track[j + 1]
        x0 = x - crop_w / 2
        x1 = next_x - crop_w / 2
        interpolated_x = f"({x0:.3f}+({x1 - x0:.3f})*(t-{t:.3f})/{next_t - t:.3f})"
        exprs.append(f"if(between(t\\,{t:.3f}\\,{next_t:.3f})\\,{interpolated_x}\\,")
    xexpr = "".join(exprs) + f"{track[-1][1] - crop_w/2:.0f}" + ")" * len(exprs)
    vf = (f"crop={crop_w}:ih:'{xexpr}':0,scale=1080:1920,"
          f"ass={subs_file}:fontsdir=/usr/share/fonts/truetype/dejavu")
    out = str(CLIPS_DIR / f"clip{i:02d}.mp4")
    r = subprocess.run(["ffmpeg", "-y", "-ss", str(start), "-t", str(dur),
        "-i", str(EP), "-vf", vf, "-c:v", "libx264", "-preset", "medium",
        "-crf", "23", "-c:a", "aac", "-b:a", "128k", out],
        capture_output=True, text=True)
    if r.returncode == 0:
        ok += 1
        print(f"  OK {out} ({dur:.0f}s) - {c['title']}")
    else:
        print(f"  FAIL: {r.stderr[-300:]}")
print(f"\n{ok}/{len(clips)} clips done")
