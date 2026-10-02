#!/usr/bin/env python3
"""Cut klip dengan auto-reframe: crop 9:16 ngikutin pembicara (YuNet face detect) + subtitle burn-in."""
import json, subprocess, os, sys, tempfile, math
import cv2
import numpy as np
from news_overlay import build_news_overlay_event

EP = sys.argv[1] if len(sys.argv) > 1 else "ep1.mp4"
os.makedirs("clips", exist_ok=True)

def _finite_number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)

try:
    clips = json.load(open("clips.json"))
    segs = json.load(open("transcript.json"))
except (OSError, json.JSONDecodeError) as exc:
    sys.exit(f"Cannot load workspace JSON: {exc}")
if not isinstance(clips, list) or not clips:
    sys.exit("clips.json has no clips")
if not isinstance(segs, list) or not segs:
    sys.exit("transcript.json has no segments")
search_results = json.load(open("search_results.json")) if os.path.exists("search_results.json") else {}
if not isinstance(search_results, dict):
    sys.exit("search_results.json must be an object keyed by clip number")

if not os.path.isfile(EP):
    sys.exit(f"Source video not found: {EP}")

try:
    det = cv2.FaceDetectorYN.create("/tmp/face_yunet.onnx", "", (320, 320), 0.6)
except Exception as exc:
    sys.exit(f"Cannot load face detector model: {exc}")
# model: curl -sL -o /tmp/face_yunet.onnx https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx

SOURCE_DURATION = None
SOURCE_WIDTH = None
SOURCE_HEIGHT = None
try:
    _pr = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,duration", "-of", "json", EP],
        capture_output=True, text=True, timeout=30,
    )
    if _pr.returncode == 0:
        _meta = json.loads(_pr.stdout or "{}")
        _stream = next((s for s in _meta.get("streams", []) or [] if isinstance(s, dict)), None)
        if not _stream:
            _stream = {}
            _fmt_pr = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", EP],
                capture_output=True, text=True, timeout=15,
            )
            if _fmt_pr.returncode == 0:
                try:
                    _fmt = json.loads(_fmt_pr.stdout).get("format", {})
                    if isinstance(_fmt, dict) and "duration" in _fmt:
                        _stream["duration"] = _fmt["duration"]
                except (OSError, json.JSONDecodeError):
                    pass
        def _gfloat(v):
            if isinstance(v, bool):
                return None
            if isinstance(v, (int, float)):
                return float(v) if math.isfinite(float(v)) else None
            if isinstance(v, str) and v.strip():
                try:
                    f = float(v)
                    return f if math.isfinite(f) else None
                except ValueError:
                    return None
            return None
        SOURCE_DURATION = _gfloat(_stream.get("duration"))
        _w = _gfloat(_stream.get("width"))
        _h = _gfloat(_stream.get("height"))
        SOURCE_WIDTH = int(_w) if _w and _w > 0 else None
        SOURCE_HEIGHT = int(_h) if _h and _h > 0 else None
except (ValueError, subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
    SOURCE_DURATION = None

_valid_clips = []
for _idx, c in enumerate(clips, 1):
    try:
        start = _finite_number(c.get("start"), f"Clip {_idx} start")
        end = _finite_number(c.get("end"), f"Clip {_idx} end")
    except ValueError as exc:
        sys.exit(str(exc))
    if start < 0 or end <= start:
        sys.exit(f"Clip {_idx}: invalid range {start}-{end}")
    if SOURCE_DURATION is not None and end > SOURCE_DURATION + 0.5:
        sys.exit(f"Clip {_idx}: end {end:.1f}s exceeds source duration {SOURCE_DURATION:.1f}s")
    title = str(c.get("title", f"Clip {_idx}")).strip() or f"Clip {_idx}"
    if end - start > 75:
        end = start + 75
    _valid_clips.append({**c, "start": start, "end": end, "title": title})
clips = _valid_clips

def face_positions(video, start, dur, samples=24):
    """Sample frame via ffmpeg (AV1-safe), return list of (t, face_cx)."""
    import subprocess as sp
    with tempfile.TemporaryDirectory() as tmp:
        r = sp.run(["ffmpeg", "-y", "-ss", str(start), "-t", str(dur),
            "-i", video, "-vf", f"fps={samples/max(dur,0.1)},scale=320:-2",
            "-q:v", "5", os.path.join(tmp, "f%04d.jpg")],
            capture_output=True, text=True)
        if r.returncode != 0:
            return [], 0, 0
        pr = sp.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "json", video],
            capture_output=True, text=True)
        try:
            meta = json.loads(pr.stdout or "{}")
            st = next((s for s in meta.get("streams", []) or [] if isinstance(s, dict)), {})
            w = int(st.get("width", 0) or 0)
            h = int(st.get("height", 0) or 0)
        except (json.JSONDecodeError, ValueError, TypeError):
            w = h = 0
        if not w or not h:
            return [], 0, 0
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
                f = max(faces, key=lambda f: f[2] * f[3])  # wajah terbesar
                scale = w / img.shape[1]
                pts.append((dur * i / n, f[0] * scale + f[2] * scale / 2))
        return pts, w, h

def smooth_track(pts, dur, crop_w, w):
    """Interpolasi posisi crop-x yang SMOOTH: cubic spline + kecepatan max terbatas."""
    margin = 60
    min_cx = crop_w / 2 + margin
    max_cx = w - crop_w / 2 - margin
    if not pts:
        center_cx = w / 2
        clamped_cx = min(max(center_cx, min_cx), max_cx)
        return [(0, clamped_cx)]
    ts = [p[0] for p in pts]
    cxs = [min(max(p[1], min_cx), max_cx) for p in pts]
    med = []
    for i in range(len(cxs)):
        win = cxs[max(0, i-2):i+3]
        med.append(sorted(win)[len(win)//2])
    steps = 60
    grid = np.linspace(0, dur, steps)
    from scipy.interpolate import CubicSpline
    if len(ts) >= 4:
        cs = CubicSpline(ts, med)
        xs = cs(grid)
    else:
        xs = np.interp(grid, ts, med)
    max_v = 250.0
    for i in range(1, len(xs)):
        dt = grid[i] - grid[i-1]
        dv = xs[i] - xs[i-1]
        lim = max_v * dt
        if abs(dv) > lim:
            xs[i] = xs[i-1] + np.sign(dv) * lim
    k = max(steps//10, 3)
    smoothed = np.convolve(np.pad(xs, k//2, mode="edge"), np.ones(k)/k, mode="valid")
    if len(smoothed) < len(xs):
        smoothed = np.interp(grid, np.linspace(0, 1, len(smoothed)), smoothed)
    clamped = np.clip(smoothed, min_cx, max_cx)
    return list(zip(grid, clamped))

ok = 0
for i, c in enumerate(clips, 1):
    start = c["start"]
    end = c["end"]
    dur = end - start
    pts, w, h = face_positions(EP, start, dur)
    if not w or not h:
        print(f"  FAIL clip{i:02d}: cannot read source dimensions")
        continue
    crop_w = int(h * 9 / 16)
    track = smooth_track(pts, dur, crop_w, w)
    print(f"clip{i:02d}: {len(pts)} face samples, track {track[0][1]:.0f}->{track[-1][1]:.0f}px")
    subs = []
    for s in segs:
        if not isinstance(s, dict):
            continue
        s_start = s.get("start")
        s_end = s.get("end")
        s_text = s.get("text")
        if not (isinstance(s_start, (int, float)) and isinstance(s_end, (int, float)) and isinstance(s_text, str)):
            continue
        if s_end <= start or s_start >= end:
            continue
        st = max(s_start, start) - start
        en = min(s_end, end) - start
        words = s_text.split()
        for j in range(0, len(words), 4):
            chunk = " ".join(words[j:j+4])
            if chunk.strip():
                subs.append((st + (en-st)*j/max(len(words),1), st + (en-st)*(j+4)/max(len(words),1), chunk))
    ass = ["[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "",
           "[V4+ Styles]",
           "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
           "Style: Default,DejaVu Sans,72,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,60,60,300,1", "",
           "Style: SourceCard,DejaVu Sans,28,&H00FFFFFF,&H000000FF,&H00000000,&H99080E12,-1,0,0,0,100,100,0,0,3,14,0,7,24,240,20,1", "",
           "[Events]",
           "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    def ts(t):
        hh=int(t//3600); m=int(t%3600//60); s=t%60
        return f"{hh}:{m:02d}:{s:05.2f}"
    for st, en, txt in subs:
        ass.append(f"Dialogue: 0,{ts(st)},{ts(en)},Default,,0,0,0,,{txt}")
    news_sources = search_results.get(str(i), search_results.get(i, []))
    source_event = build_news_overlay_event(news_sources, dur)
    if source_event:
        ass.append(source_event)
    subs_path = "subs.ass"
    try:
        with open(subs_path, "w", encoding="utf-8") as sf:
            sf.write("\n".join(ass))
    except OSError as io_err:
        print(f"  FAIL clip{i:02d}: cannot write subs.ass: {io_err}")
        continue
    min_crop_x = 0
    max_crop_x = max(0, w - crop_w)
    exprs = []
    for j, (t, x) in enumerate(track):
        next_t = track[j+1][0] if j+1 < len(track) else dur
        x0 = max(min_crop_x, min(max_crop_x, x - crop_w/2))
        x1 = x0
        if j + 1 < len(track):
            x1 = max(min_crop_x, min(max_crop_x, track[j+1][1] - crop_w/2))
        interpolated_x = f"({x0:.0f}"
        if next_t > t and (x1 - x0) != 0:
            interpolated_x = f"({x0:.3f}+({x1 - x0:.3f})*(t-{t:.3f})/{next_t - t:.3f})"
        exprs.append(f"if(between(t\\,{t:.3f}\\,{next_t:.3f})\\,{interpolated_x}\\,")
    last_x = max(min_crop_x, min(max_crop_x, track[-1][1] - crop_w/2))
    xexpr = "".join(exprs) + f"{last_x:.0f}" + ")" * len(exprs)
    vf = (f"crop={crop_w}:ih:'{xexpr}':0,scale=1080:1920,"
          f"ass=subs.ass:fontsdir=/usr/share/fonts/truetype/dejavu")
    out = f"clips/clip{i:02d}.mp4"
    tmp_out = f"clips/.clip{i:02d}.tmp.mp4"
    try:
        if os.path.isfile(tmp_out):
            os.unlink(tmp_out)
    except OSError:
        pass
    r = subprocess.run(["ffmpeg", "-y", "-ss", str(start), "-t", str(dur),
        "-i", EP, "-vf", vf, "-c:v", "libx264", "-preset", "medium",
        "-crf", "23", "-c:a", "aac", "-b:a", "128k", tmp_out],
        capture_output=True, text=True)
    if r.returncode == 0 and os.path.isfile(tmp_out):
        try:
            os.replace(tmp_out, out)
        except OSError as rename_err:
            print(f"  FAIL: cannot move output into place: {rename_err}")
            try:
                os.unlink(tmp_out)
            except OSError:
                pass
            continue
        ok += 1
        print(f"  OK {out} ({dur:.0f}s) - {c.get('title', '')}")
    else:
        try:
            if os.path.isfile(tmp_out):
                os.unlink(tmp_out)
        except OSError:
            pass
        print(f"  FAIL: {r.stderr[-300:]}")
print(f"\n{ok}/{len(clips)} clips done")
if ok != len(clips):
    sys.exit(1)
