#!/usr/bin/env python3
"""Cut klip dengan auto-reframe: crop 9:16 ngikutin pembicara (YuNet face detect)."""
import json, subprocess, os, sys, tempfile, math, shutil
from pathlib import Path

FFMPEG = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/usr/bin/ffprobe"
import cv2
from news_overlay import build_news_overlay_event
from subtitle_timing import subtitle_chunks
from clip_quality import validate_clip_ranges
from face_tracking import smooth_track, validate_video_dimensions

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
FONT_DIR = Path(os.environ.get("PODCAST_FONT_DIR", "/usr/share/fonts/truetype/dejavu"))
FONT_FILE = Path(os.environ.get("PODCAST_FONT_FILE", str(FONT_DIR / "DejaVuSans-Bold.ttf")))
FONT = "DejaVu Sans"
MODEL_FILE = Path(os.environ.get("FACE_YUNET_MODEL", "/tmp/face_yunet.onnx")).expanduser()
if not Path(FFMPEG).is_file() or not Path(FFPROBE).is_file():
    sys.exit("ffmpeg and ffprobe must be installed and available on PATH")
if not FONT_FILE.is_file():
    sys.exit(f"Subtitle font not found: {FONT_FILE}; set PODCAST_FONT_FILE or PODCAST_FONT_DIR")
if not MODEL_FILE.is_file() or MODEL_FILE.stat().st_size == 0:
    sys.exit(f"YuNet face detector model not found or empty: {MODEL_FILE}; set FACE_YUNET_MODEL")

try:
    det = cv2.FaceDetectorYN.create(str(MODEL_FILE), "", (320, 320), 0.6)
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

try:
    validate_video_dimensions(SOURCE_WIDTH, SOURCE_HEIGHT)
except ValueError as exc:
    sys.exit(f"Invalid source video dimensions: {exc}")

if SOURCE_DURATION is None:
    sys.exit("Cannot determine source video duration with ffprobe")
try:
    clips = validate_clip_ranges(clips, segs, SOURCE_DURATION)
except ValueError as exc:
    sys.exit(f"Invalid clip input: {exc}")

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
    subs = subtitle_chunks(segs, start, end)
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
          f"ass={subs_file}:fontsdir={FONT_FILE.parent}")
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
