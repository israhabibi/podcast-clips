#!/usr/bin/env python3
"""Trigger a single-episode workflow from a YouTube URL."""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parent

YOUTUBE_ID_RE = re.compile(r"(?:v=|be/)([A-Za-z0-9_-]{11})")
MEDIA_SUFFIXES = frozenset((".mp4", ".mkv", ".webm", ".mov"))


def parse_video_id(url: str):
    url = (url or '').strip()
    if not url:
        raise ValueError('YouTube URL is required')
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower().rstrip('.')
    video_id = ''
    if host in ('youtu.be', 'www.youtu.be'):
        video_id = parsed.path.strip('/').split('/')[0]
    elif host in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
        if parsed.path == '/watch':
            values = parse_qs(parsed.query).get('v', [])
            video_id = values[0] if len(values) == 1 else ''
        else:
            match = YOUTUBE_ID_RE.search(url)
            video_id = match.group(1) if match else ''
    if re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        return video_id
    raise ValueError(f'Not a valid YouTube URL: {url}')


def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    return path


def source_candidates(episode_dir: Path):
    """Return completed media files only; ignore yt-dlp sidecars and partial downloads."""
    return sorted(
        path for path in episode_dir.glob("source.*")
        if path.is_file() and path.suffix.lower() in MEDIA_SUFFIXES
    )


def load_hermes_key():
    key = os.environ.get('HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY', '')
    if key:
        return key
    hermes_env = Path.home() / '.hermes' / '.env'
    if hermes_env.exists():
        for line in hermes_env.read_text().splitlines():
            if line.startswith('HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY='):
                return line.split('=', 1)[1].strip().strip('"')
    return ''


def step(name, cmd, env):
    print(f"\n=== {name} ===", file=sys.stderr)
    print(f"$ {' '.join(cmd)}", file=sys.stderr)
    result = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, text=True)
    if result.stdout:
        print(result.stdout, file=sys.stderr)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"{name} failed with exit code {result.returncode}")


def transcribe_with_faster_whisper(source_path: Path, output_path: Path):
    from faster_whisper import WhisperModel

    model = WhisperModel('small', device='cpu', compute_type='int8')
    segments, _ = model.transcribe(str(source_path), language='id', vad_filter=True)
    out = []
    for seg in segments:
        text = (seg.text or '').strip()
        if not text:
            continue
        out.append({
            'start': float(seg.start),
            'end': float(seg.end),
            'text': text,
        })
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"transcript segments: {len(out)}", file=sys.stderr)


def main():
    if len(sys.argv) < 2:
        print('Usage: python run_one_episode.py <youtube_url> [podcast_slug] [episode_title]', file=sys.stderr)
        sys.exit(1)

    preflight = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_setup.py"), "--media"],
        cwd=str(REPO_ROOT),
        check=False,
    )
    if preflight.returncode:
        raise RuntimeError("Media dependency preflight failed; fix the reported setup errors first")

    url = sys.argv[1]
    video_id = parse_video_id(url)
    podcast_slug = sys.argv[2] if len(sys.argv) > 2 else 'jelasin-dong'
    episode_title = (sys.argv[3].strip() if len(sys.argv) > 3 else '') or f'YouTube episode {video_id}'
    timestamp = int(time.time())
    # Reuse an existing workdir for this video if it already has source.mp4 (resume-friendly;
    # never re-download — YouTube 403 rate-limits repeated downloads of the same video).
    episode_dir = None
    for d in sorted(Path('/tmp/podcast-clips').glob(f"{video_id}-*")):
        if d.is_dir() and source_candidates(d):
            episode_dir = d
            break
    if episode_dir is None:
        episode_dir = ensure_dir(Path('/tmp/podcast-clips') / f"{video_id}-{timestamp}")
    print(f"EPISODE_DIR={episode_dir}")
    print(f"VIDEO_URL={url}")

    env = os.environ.copy()
    env['PODCAST_WORK_DIR'] = str(episode_dir)
    env['PODCAST_TRANSCRIPT_FILE'] = str(episode_dir / 'transcript.json')
    api_key = load_hermes_key()
    if api_key:
        env['HERMES_CUSTOM_AI_SUMOPOD_COM_API_KEY'] = api_key

    yt_dlp = shutil.which('yt-dlp')
    if not yt_dlp:
        raise RuntimeError('yt-dlp not found. Install it with: pip install yt-dlp')

    source = episode_dir / 'source.mp4'
    if not source.exists():
        # 403 Forbidden is transient YouTube rate limiting — retry with backoff (3x)
        for attempt in range(1, 4):
            try:
                step('download', [yt_dlp, '-f', 'bv*[height<=720]+ba/b[height<=720]', '--merge-output-format', 'mp4', '--retries', '3', '-o', str(episode_dir / 'source.%(ext)s'), url], env)
            except RuntimeError as exc:
                if attempt == 3:
                    raise
                wait = 60 * attempt
                print(f"[download] attempt {attempt} failed ({exc}); retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
            if source_candidates(episode_dir):
                break
    candidates = source_candidates(episode_dir)
    if not source.exists() and candidates:
        source = candidates[0]

    if not source.exists():
        raise RuntimeError(f'Video download did not produce {source}')

    transcript_path = episode_dir / 'transcript.json'
    if not transcript_path.exists():
        transcribe_with_faster_whisper(source, transcript_path)

    if not transcript_path.exists() or transcript_path.stat().st_size < 20:
        raise RuntimeError('Transcript generation produced no usable data')

    # laughter/audio-burst signal for curate (optional — curate skips if laughter.json missing)
    try:
        step('laughter', [sys.executable, 'scripts/laughter_score.py', str(episode_dir)], env)
    except RuntimeError as exc:
        print(f"[laughter] skipped: {exc}", file=sys.stderr)
    step('curate', [sys.executable, 'curate.py', 'MiniMax-M2.7-highspeed', podcast_slug, episode_title], env)
    step('cut', [sys.executable, 'cut_smart.py', str(source)], env)
    # Minimal search_results placeholder to let caption.py run without failing.
    try:
        search_results = json.loads((episode_dir / 'search_results.json').read_text()) if (episode_dir / 'search_results.json').exists() else {}
    except Exception:
        search_results = {}
    if not search_results:
        (episode_dir / 'search_results.json').write_text('{}', encoding='utf-8')
    step('caption', [sys.executable, 'caption.py'], env)

    print(f"SUCCESS: pipeline complete for {episode_title}")
    print(f"WORK_DIR={episode_dir}")
    print(f"CLIPS={len(list((episode_dir / 'clips').glob('clip*.mp4')))}")
    sys.exit(0)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:  # pragma: no cover
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
