"""LLM 준비/실행 전체를 한 번에 수행하는 헬퍼.

순서: llm_payloads → run_llm_payloads (Qwen2.5-VL)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def run_step(cmd: list[str]) -> None:
    print(f"[llm] running: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run LLM payload + inference")
    parser.add_argument(
        "--payload",
        action="append",
        help="특정 payload 파일만 실행 (여러 번 지정 가능). 지정 없으면 모든 payload 실행",
    )
    args = parser.parse_args()

    run_step([sys.executable, "core/llm/llm_payloads.py"])

    cmd = [sys.executable, "core/llm/run_llm_payloads.py"]
    if args.payload:
        cmd.extend(args.payload)
    run_step(cmd)

    print("[llm] done")


if __name__ == "__main__":
    main()
