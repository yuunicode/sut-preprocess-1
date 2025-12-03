#!/usr/bin/env python3
"""Gather every components.json under sanitize root into merged files (total + split)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "output" / "sanitize"
EXTRACT_DIR = Path(__file__).resolve().parents[1] / "output" / "extract"
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
    parser = argparse.ArgumentParser(description="Merge all components.json files into total and split outputs.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="components.json 루트 (기본: output/sanitize)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="총합 출력 경로 (기본: logs/components_total.json)")
    args = parser.parse_args()

    tables: list[dict] = []
    images_summary: list[dict] = []
    images_translation: list[dict] = []
    texts: list[dict] = []

    for comp_path in sorted(args.root.rglob("components.json")):
        if not comp_path.is_file():
            continue
        data = load_components(comp_path)
        append_with_source(tables, data.get("tables", []))
        append_with_source(images_summary, data.get("images_summary", []))
        append_with_source(images_translation, data.get("images_translation", []))
        append_with_source(texts, data.get("texts", []))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    merged = {
        "tables": tables,
        "images_summary": images_summary,
        "images_translation": images_translation,
        "texts": texts,
    }
    args.out.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    acc = {
        "tables_str": [],
        "tables_unstr": [],
        "images_sum": [],
        "images_trans": [],
        "images_formula": [],
        "texts": texts,
    }
    for t in tables:
        ctype = t.get("component_type") or ""
        if ctype == "table_unstructured" or t.get("id", "").startswith("TB_UNSTR"):
            acc["tables_unstr"].append(t)
        else:
            acc["tables_str"].append(t)
    for img in images_summary:
        ctype = img.get("component_type") or ""
        if ctype == "image_formula":
            acc["images_formula"].append(img)
        else:
            acc["images_sum"].append(img)
    for img in images_translation:
        acc["images_trans"].append(img)

    split_files = {
        "components_tables_str.json": acc["tables_str"],
        "components_tables_unstr.json": acc["tables_unstr"],
        "components_images_sum.json": acc["images_sum"],
        "components_images_trans.json": acc["images_trans"],
        "components_images_formula.json": acc["images_formula"],
        "components_texts.json": acc["texts"],
    }
    # output/extract에 total을 제외한 분리본을 저장
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
    for name, data in split_files.items():
        (EXTRACT_DIR / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"[INFO] merged {len(tables)} tables, {len(images_summary)} image_summary, {len(images_translation)} image_translation, {len(texts)} texts "
        f"into {args.out} and split JSONs under {EXTRACT_DIR}"
    )


if __name__ == "__main__":
    main()
