"""Final JSON 생성만 실행하는 헬퍼."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    print("[finalize] running finalize_jsons.py")
    subprocess.run([sys.executable, "core/finalize/finalize_jsons.py"], cwd=REPO_ROOT, check=True)
    print("[finalize] done")


if __name__ == "__main__":
    main()
