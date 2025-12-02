#!/usr/bin/env python3
"""Gather every components.json under sanitize root into one merged file."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "output" / "sanitize"
DEFAULT_OUT = Path(__file__).resolve().parents[1] / "logs" / "components_total.json"


def load_components(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def append_with_source(dest: list[dict], items: list[dict]) -> None:
    for item in items or []:
        entry = dict(item)
        entry.pop("doc_folder", None)
        dest.append(entry)


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge all components.json files into one.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="components.json 루트 (기본: output/sanitize)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="출력 경로 (기본: logs/components_total.json)")
    args = parser.parse_args()

    tables: list[dict] = []
    images_summary: list[dict] = []
    images_translation: list[dict] = []

    for comp_path in sorted(args.root.rglob("components.json")):
        if not comp_path.is_file():
            continue
        data = load_components(comp_path)
        append_with_source(tables, data.get("tables", []))
        append_with_source(images_summary, data.get("images_summary", []))
        append_with_source(images_translation, data.get("images_translation", []))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    merged = {
        "tables": tables,
        "images_summary": images_summary,
        "images_translation": images_translation,
    }
    args.out.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[INFO] merged {len(tables)} tables, {len(images_summary)} image_summary, {len(images_translation)} image_translation into {args.out}")


if __name__ == "__main__":
    main()
