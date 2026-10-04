#!/usr/bin/env python3
"""Run a config-driven TOP 5 build as an admin background worker."""

import json
import os
import subprocess
import sys
from pathlib import Path


def main():
    if len(sys.argv) != 7:
        raise SystemExit(
            "Usage: build_compilation.py <workdir> <job-output> <final-output> <config> <log> <lock>"
        )
    workdir, output, final_output, config, log_path, lock_path = map(Path, sys.argv[1:])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(Path(__file__).with_name("top5_compilation.py")),
        str(workdir),
        str(output),
        "--config",
        str(config),
    ]
    exit_code = 1
    try:
        with log_path.open("a", encoding="utf-8") as log:
            log.write("=== compilation ===\n")
            log.write("$ " + " ".join(command) + "\n")
            try:
                result = subprocess.run(
                    command, cwd=str(workdir.parent), stdout=log, stderr=log, text=True
                )
                exit_code = result.returncode
                if exit_code == 0:
                    final_output.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(output, final_output)
            except OSError as exc:
                log.write(f"WORKER_ERROR={exc}\n")
                exit_code = 1
            log.write(f"EXIT_CODE={exit_code}\n")
            log.flush()
            os.fsync(log.fileno())
    finally:
        if exit_code != 0:
            output.unlink(missing_ok=True)
        lock_path.unlink(missing_ok=True)
    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
