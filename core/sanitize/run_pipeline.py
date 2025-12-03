"""Sanitize 단계 전체를 한 번에 실행하는 헬퍼.

순서: rule_cleanup → copy_components → extract_components → extract_texts → aggregate_components
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def run_step(cmd: list[str]) -> None:
    print(f"[sanitize] running: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run sanitize pipeline")
    parser.add_argument("--root", default="output/sanitize", help="sanitize 대상 루트 경로")
    args = parser.parse_args()

    root_arg = ["--root", args.root]

    run_step([sys.executable, "core/sanitize/rule_cleanup.py"])
    run_step([sys.executable, "script/copy_components.py"])
    run_step([sys.executable, "core/sanitize/extract_components.py", *root_arg])
    run_step([sys.executable, "core/sanitize/extract_texts.py", *root_arg])
    run_step([sys.executable, "core/sanitize/aggregate_components.py"])
    print("[sanitize] done")


if __name__ == "__main__":
    main()
