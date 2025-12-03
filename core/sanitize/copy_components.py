#!/usr/bin/env python3
"""Copy components directories from output/chandra to output/sanitize."""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHANDRA_ROOT = REPO_ROOT / "output" / "chandra"
SANITIZE_ROOT = REPO_ROOT / "output" / "sanitize"


def copy_components_for(pdf_dir: Path, destination_root: Path) -> None:
    chandra_dir = pdf_dir / "components"
    if not chandra_dir.exists():
        return
    dest_dir = destination_root / pdf_dir.relative_to(CHANDRA_ROOT) / "components"
    dest_dir.parent.mkdir(parents=True, exist_ok=True)
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    shutil.copytree(chandra_dir, dest_dir)
    print(f"[INFO] Copied {chandra_dir} -> {dest_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy components directories after rule_math_cleanup.")
    parser.add_argument("--source", type=Path, default=CHANDRA_ROOT, help="원본(output/chandra) 경로")
    parser.add_argument("--dest", type=Path, default=SANITIZE_ROOT, help="대상(output/sanitize) 경로")
    parser.add_argument("--dirs", nargs="*", type=Path, help="특정 PDF 디렉터리만 복사")
    args = parser.parse_args()

    source_root = args.source.resolve()
    dest_root = args.dest.resolve()

    if args.dirs:
        targets = [source_root / Path(d) if not d.is_absolute() else d for d in args.dirs]
    else:
        targets = [p for p in source_root.iterdir() if p.is_dir()]

    for pdf_dir in targets:
        if not pdf_dir.exists() or not pdf_dir.is_dir():
            continue
        copy_components_for(pdf_dir, dest_root)


if __name__ == "__main__":
    main()
