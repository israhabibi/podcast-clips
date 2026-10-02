#!/usr/bin/env python3
"""Hermes-skill shim: forwards execution to the single maintained copy in the podcast-clips repo.

Runtime contract (safe for Hermes chat sessions):
- CWD preserved unchanged (Hermes typically sets this to a /tmp/podcast-clips/<episode-id> workdir).
- PYTHONPATH prepended with the repo root so imports like `from scripts.youtube_upload import upload_clip` resolve.
- PODCAST_WORK_DIR defaulted to CWD if not already set OR set to empty string (but never overrides a real value).
- Python interpreter chosen: `${REPO_ROOT}/.venv/bin/python > PODCAST_CLIPS_PYTHON env > current sys.executable`.
- If repo cannot be found at the default location, print a clear error instead of silently failing.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    override = os.environ.get("PODCAST_CLIPS_REPO")
    if override:
        return Path(override).expanduser().resolve()
    default = Path("~/podcast-clips").expanduser().resolve()
    if default.is_dir():
        return default
    raise SystemExit(
        "hermes-skill shim could not find the podcast-clips repository at: "
        + str(default)
        + "\nEither set PODCAST_CLIPS_REPO=/absolute/path/to/podcast-clips env var, or "
        + "clone it to the default location ~/podcast-clips."
    )


def _python(repo_root: Path) -> str:
    override = os.environ.get("PODCAST_CLIPS_PYTHON")
    if override:
        return override
    venv_python = repo_root / ".venv" / "bin" / "python"
    if venv_python.is_file():
        return str(venv_python)
    return sys.executable


def main() -> int:
    try:
        repo_root = _repo_root()
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2
    target = (repo_root / "cut.py").resolve()
    if not target.is_file():
        print(
            f"hermes-skill shim: target script missing: {target}\n"
            f"(repo_root={repo_root})",
            file=sys.stderr,
        )
        return 2

    env = os.environ.copy()
    existing_pp = env.get("PYTHONPATH", "").strip()
    env["PYTHONPATH"] = (
        f"{repo_root}{os.pathsep}{existing_pp}" if existing_pp else str(repo_root)
    )
    env.setdefault("PODCAST_CLIPS_REPO", str(repo_root))
    if "PODCAST_WORK_DIR" not in env or env["PODCAST_WORK_DIR"] in (None, ""):
        # Hermes typically runs the skill from inside the episode workdir, so cwd == workdir.
        env["PODCAST_WORK_DIR"] = str(Path.cwd())

    cmd = [_python(repo_root), str(target), *sys.argv[1:]]
    proc = subprocess.run(cmd, cwd=Path.cwd(), env=env, check=False)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
