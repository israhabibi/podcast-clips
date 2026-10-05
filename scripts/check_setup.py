#!/usr/bin/env python3
"""Check Python and system dependencies before serving or processing episodes."""

import argparse
import importlib.util
import os
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
DEFAULT_MODEL = Path("/tmp/face_yunet.onnx")

WEB_MODULES = {
    "flask": "Flask",
    "gunicorn": "Gunicorn",
    "requests": "requests",
    "google_auth_oauthlib": "google-auth-oauthlib",
    "googleapiclient": "google-api-python-client",
}
MEDIA_MODULES = {
    "cv2": "opencv-python-headless",
    "numpy": "numpy",
    "scipy": "scipy",
    "faster_whisper": "faster-whisper",
}


def check_setup(*, web=False, media=False, compilation=False):
    failures = []
    module_groups = []
    if web or not (web or media or compilation):
        module_groups.append(WEB_MODULES)
    if media or not (web or media or compilation):
        module_groups.append(MEDIA_MODULES)
    checked = set()
    for modules in module_groups:
        for module, package in modules.items():
            if module in checked:
                continue
            checked.add(module)
            if importlib.util.find_spec(module) is None:
                failures.append(f"Python package missing: {package} (install requirements.txt)")

    needs_ffmpeg = media or compilation or not (web or media or compilation)
    if needs_ffmpeg:
        commands = ["ffmpeg", "ffprobe"]
        if media or not (web or media or compilation):
            commands.append("yt-dlp")
        for command in commands:
            if shutil.which(command) is None:
                failures.append(f"Executable missing from PATH: {command}")

    needs_font = media or compilation or not (web or media or compilation)
    if needs_font:
        font_file = Path(os.environ.get("PODCAST_FONT_FILE", "")) if os.environ.get("PODCAST_FONT_FILE") else None
        font_dir = Path(os.environ.get("PODCAST_FONT_DIR", str(DEFAULT_FONT_DIR)))
        resolved_font = font_file or (font_dir / "DejaVuSans-Bold.ttf")
        if not resolved_font.is_file():
            failures.append(
                f"Font missing: {resolved_font} (set PODCAST_FONT_FILE or PODCAST_FONT_DIR)"
            )

    needs_model = media or not (web or media or compilation)
    if needs_model:
        model_path = Path(os.environ.get("FACE_YUNET_MODEL", str(DEFAULT_MODEL))).expanduser()
        if not model_path.is_file():
            failures.append(
                f"YuNet model missing: {model_path} (set FACE_YUNET_MODEL to its ONNX path)"
            )
        elif model_path.stat().st_size == 0:
            failures.append(f"YuNet model is empty: {model_path}")

    if compilation:
        for asset in (
            Path("/usr/share/sounds/sound-icons/pisk-up.wav"),
            Path("/usr/share/sounds/sound-icons/cembalo-12.wav"),
        ):
            if not asset.is_file():
                failures.append(f"Compilation sound effect missing: {asset}")

    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--web", action="store_true", help="Check the Gunicorn web service dependencies")
    modes.add_argument("--media", action="store_true", help="Check FFmpeg, YuNet, font, and media Python packages")
    modes.add_argument("--compilation", action="store_true", help="Check Top 5 build dependencies, including sound effects")
    args = parser.parse_args(argv)
    failures = check_setup(web=args.web, media=args.media, compilation=args.compilation)
    if failures:
        for failure in failures:
            print(f"ERROR: {failure}", file=sys.stderr)
        return 1
    print("Setup check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
