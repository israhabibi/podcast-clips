#!/usr/bin/env python3
"""Cut klip dengan auto-reframe: crop 9:16 ngikutin pembicara (YuNet face detect)."""
import json, subprocess, os, sys, tempfile, math
from pathlib import Path

FFMPEG = "/usr/bin/ffmpeg"
FFPROBE = "/usr/bin/ffprobe"
import cv2
import numpy as np
from scipy.interpolate import CubicSpline
from news_overlay import build_news_overlay_event

if len(sys.argv) < 2:
    sys.exit("Usage: python cut_smart.py <source-video>")
EP = Path(sys.argv[1])
WORK_DIR_VALUE = os.environ.get("PODCAST_WORK_DIR")
if not WORK_DIR_VALUE:
    sys.exit("PODCAST_WORK_DIR is required, e.g. /tmp/podcast-clips/episode-id")
WORK_DIR = Path(WORK_DIR_VALUE)
CLIPS_DIR = WORK_DIR / "clips"
CLIPS_DIR.mkdir(parents=True, exist_ok=True)

if not EP.is_file():
    sys.exit(f"Source video not found: {EP}")

def _finite_number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)

try:
    clips = json.load(open(WORK_DIR / "clips.json"))
    segs = json.load(open(WORK_DIR / "transcript.json"))
except (OSError, json.JSONDecodeError) as exc:
    sys.exit(f"Cannot load workspace JSON: {exc}")

if not isinstance(clips, list) or not clips:
    sys.exit("clips.json has no clips")
if not isinstance(segs, list) or not segs:
    sys.exit("transcript.json has no segments")

search_results_path = WORK_DIR / "search_results.json"
search_results = json.loads(search_results_path.read_text(encoding="utf-8")) if search_results_path.exists() else {}
if not isinstance(search_results, dict):
    sys.exit("search_results.json must be an object keyed by clip number")
FONT = "DejaVuSans-Bold"

try:
    det = cv2.FaceDetectorYN.create("/tmp/face_yunet.onnx", "", (320, 320), 0.6)
except Exception as exc:
    sys.exit(f"Cannot load face detector model: {exc}")

try:
    _source_pr = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,duration", "-of", "json", str(EP)],
        capture_output=True, text=True, timeout=30,
    )
    if _source_pr.returncode != 0:
        sys.exit(f"ffprobe failed on source: {_source_pr.stderr[-200:]}")
    _source_meta = json.loads(_source_pr.stdout or "{}")
    _stream = None
    for s in _source_meta.get("streams", []) or []:
        if isinstance(s, dict):
            _stream = s
            break
    if not _stream:
        # fallback: format-level duration, unknown dims (None)
        _stream = {}
        _fmt_pr = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(EP)],
            capture_output=True, text=True, timeout=15,
        )
        if _fmt_pr.returncode == 0:
            try:
                _fmt = json.loads(_fmt_pr.stdout).get("format", {})
                if "duration" in _fmt:
                    _stream["duration"] = _fmt["duration"]
            except (OSError, json.JSONDecodeError):
                pass
    def _get_float(v):
        if v is None:
            return None
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str) and v.strip():
            try:
                return float(v)
            except ValueError:
                return None
        return None
    SOURCE_DURATION = _get_float(_stream.get("duration"))
    SOURCE_WIDTH = int(_stream["width"]) if isinstance(_stream.get("width"), int) and _stream["width"] > 0 else None
    SOURCE_HEIGHT = int(_stream["height"]) if isinstance(_stream.get("height"), int) and _stream["height"] > 0 else None
except (ValueError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
    sys.exit(f"Cannot read source video metadata: {exc}")

validated_clips = []
for index, c in enumerate(clips, 1):
    try:
        start = _finite_number(c.get("start"), f"Clip {index} start")
        end = _finite_number(c.get("end"), f"Clip {index} end")
    except ValueError as exc:
        sys.exit(str(exc))
    if start < 0 or end <= start:
        sys.exit(f"Clip {index}: invalid range {start}-{end}")
    if SOURCE_DURATION is not None and end > SOURCE_DURATION + 0.5:
        sys.exit(f"Clip {index}: end {end:.1f}s exceeds source duration {SOURCE_DURATION:.1f}s")
    title = str(c.get("title", f"Clip {index}")).strip() or f"Clip {index}"
    if end - start > 75:
        end = start + 75
    validated_clips.append({**c, "start": start, "end": end, "title": title})
clips = validated_clips

def face_positions(video, start, dur, samples=48):
    """Sample frame via ffmpeg (AV1-safe), return list of (t, face_cx)."""
    import subprocess as sp
    with tempfile.TemporaryDirectory() as tmp:
        r = sp.run([FFMPEG, "-y", "-ss", str(start), "-t", str(dur),
            "-i", video, "-vf", f"fps={samples/max(dur,0.1)},scale=320:-2",
            "-q:v", "5", os.path.join(tmp, "f%04d.jpg")],
            capture_output=True, text=True)
        if r.returncode != 0:
            return [], 0, 0
        # dimensi asli via ffprobe JSON (urutan field tidak bergantung urutan argument)
        pr = sp.run([FFPROBE, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "json", video],
            capture_output=True, text=True)
        try:
            stream_meta = json.loads(pr.stdout or "{}")
            stream = next((s for s in stream_meta.get("streams", []) or [] if isinstance(s, dict)), {})
            w = int(stream.get("width", 0) or 0)
            h = int(stream.get("height", 0) or 0)
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
                f = max(faces, key=lambda f: f[2] * f[3])
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
    steps = 90
    grid = np.linspace(0, dur, steps)
    from scipy.interpolate import CubicSpline as _CubicSpline
    if len(ts) >= 4:
        cs = _CubicSpline(ts, med)
        xs = cs(grid)
    else:
        xs = np.interp(grid, ts, med)
    max_v = 180.0
    for i in range(1, len(xs)):
        dt = grid[i] - grid[i-1]
        dv = xs[i] - xs[i-1]
        lim = max_v * dt
        if abs(dv) > lim:
            xs[i] = xs[i-1] + np.sign(dv) * lim
    k = max(steps//15, 5)
    smoothed = np.convolve(np.pad(xs, k//2, mode="edge"), np.ones(k)/k, mode="valid")
    if len(smoothed) < len(xs):
        smoothed = np.interp(grid, np.linspace(0, 1, len(smoothed)), smoothed)
    clamped = np.clip(smoothed, min_cx, max_cx)
    return list(zip(grid, clamped))

ok = 0
for i, c in enumerate(clips, 1):
    start, end = c["start"], c["end"]
    dur = end - start
    pts, w, h = face_positions(EP, start, dur)
    if w is None or h is None:
        if SOURCE_WIDTH and SOURCE_HEIGHT:
            w, h = SOURCE_WIDTH, SOURCE_HEIGHT
        else:
            print(f"  FAIL clip{i:02d}: cannot determine source video dimensions")
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
    subs_file = WORK_DIR / "subs.ass"
    subs_file.write_text("\n".join(ass))
    min_crop_x = 0
    max_crop_x = max(0, w - crop_w)
    exprs = []
    for j, (t, x) in enumerate(track[:-1]):
        next_t, next_x = track[j + 1]
        x0 = max(min_crop_x, min(max_crop_x, x - crop_w / 2))
        x1 = max(min_crop_x, min(max_crop_x, next_x - crop_w / 2))
        interpolated_x = f"({x0:.3f}+({x1 - x0:.3f})*(t-{t:.3f})/{next_t - t:.3f})"
        exprs.append(f"if(between(t\\,{t:.3f}\\,{next_t:.3f})\\,{interpolated_x}\\,")
    last_x = max(min_crop_x, min(max_crop_x, track[-1][1] - crop_w / 2))
    xexpr = "".join(exprs) + f"{last_x:.0f}" + ")" * len(exprs)
    vf = (f"crop={crop_w}:ih:'{xexpr}':0,scale=1080:1920,"
          f"ass={subs_file}:fontsdir=/usr/share/fonts/truetype/dejavu")
    out_name = str(c.get("filename")) if isinstance(c.get("filename"), str) and c.get("filename").strip() else f"clip{i:02d}.mp4"
    if Path(out_name).name != out_name:  # just be safe: clip["filename"] must be plain basename, no path sep
        out_name = Path(out_name).name
    out = CLIPS_DIR / out_name
    tmp_out_name = "." + Path(out_name).stem + ".tmp" + Path(out_name).suffix
    tmp_out = CLIPS_DIR / tmp_out_name
    r = subprocess.run([FFMPEG, "-y", "-ss", str(start), "-t", str(dur),
        "-i", str(EP), "-vf", vf, "-c:v", "libx264", "-preset", "medium",
        "-crf", "23", "-c:a", "aac", "-b:a", "128k", str(tmp_out)],
        capture_output=True, text=True)
    if r.returncode == 0 and tmp_out.is_file():
        try:
            os.replace(str(tmp_out), str(out))
        except OSError as rename_err:
            print(f"  FAIL: cannot move output into place: {rename_err}")
            try:
                tmp_out.unlink(missing_ok=True)
            except OSError:
                pass
            continue
        ok += 1
        print(f"  OK {out} ({dur:.0f}s) - {c['title']}")
    else:
        try:
            tmp_out.unlink(missing_ok=True)
        except OSError:
            pass
        print(f"  FAIL: {r.stderr[-300:]}")
print(f"\n{ok}/{len(clips)} clips done")
if ok != len(clips):
    sys.exit(1)
