#!/usr/bin/env python3
"""Run a config-driven TOP 5 build as an admin background worker."""

import json
import subprocess
import sys
from pathlib import Path


def main():
    if len(sys.argv) != 5:
        raise SystemExit("Usage: build_compilation.py <workdir> <output> <config> <log>")
    workdir, output, config, log_path = map(Path, sys.argv[1:])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(Path(__file__).with_name("top5_compilation.py")),
        str(workdir),
        str(output),
        "--config",
        str(config),
    ]
    with log_path.open("a", encoding="utf-8") as log:
        log.write("=== compilation ===\n")
        log.write("$ " + " ".join(command) + "\n")
        result = subprocess.run(command, cwd=str(workdir.parent), stdout=log, stderr=log, text=True)
        log.write(f"EXIT_CODE={result.returncode}\n")
    if result.returncode:
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
