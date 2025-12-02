#!/usr/bin/env python3
"""Finalize tables/texts with LLM outputs into output/final."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

EXTRACT_DIR = Path(__file__).resolve().parents[1] / "output" / "extract"
LLM_DIR = Path(__file__).resolve().parents[1] / "output" / "llm"
FINAL_DIR = Path(__file__).resolve().parents[1] / "output" / "final"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_table_summaries() -> Dict[str, List[str]]:
    """Load completed table summaries from llm result (filled outputs만 사용)."""
    result_path = LLM_DIR / "llm_tables_str_result.json"
    if not result_path.exists():
        return {}
    payloads = load_json(result_path)
    summaries: Dict[str, List[str]] = {}
    for item in payloads:
        tid = item.get("id")
        out = item.get("output") or {}
        summary = out.get("table_summary")
        if tid and isinstance(summary, list) and any(s.strip() for s in summary if isinstance(s, str)):
            summaries[tid] = summary
    return summaries


def finalize_tables_str() -> List[Dict[str, Any]]:
    src_path = EXTRACT_DIR / "components_tables_str.json"
    if not src_path.exists():
        return []
    tables = load_json(src_path)
    summaries = load_table_summaries()
    finals: List[Dict[str, Any]] = []

    for tbl in tables:
        base = {
            "id": tbl.get("id"),
            "placeholder": tbl.get("placeholder"),
            "component_type": tbl.get("component_type"),
            "text": tbl.get("row_flatten"),
            "image_link": tbl.get("image_link"),
            "section_path": tbl.get("section_path"),
            "filename": tbl.get("filename"),
            "page": tbl.get("page"),
        }
        finals.append(base)

        rows = tbl.get("row_flatten") or []
        if isinstance(rows, list):
            for idx, row in enumerate(rows, start=1):
                finals.append(
                    {
                        "id": f"{tbl.get('id')}#{idx}",
                        "component_type": "table_row",
                        "text": row,
                        "image_link": tbl.get("image_link"),
                        "section_path": tbl.get("section_path"),
                        "filename": tbl.get("filename"),
                        "page": tbl.get("page"),
                    }
                )

        if tbl.get("id") in summaries:
            finals.append(
                {
                    "id": f"{tbl.get('id')}#summary",
                    "component_type": "table_summary",
                    "text": summaries[tbl.get("id")],
                    "image_link": tbl.get("image_link"),
                    "section_path": tbl.get("section_path"),
                    "filename": tbl.get("filename"),
                    "page": tbl.get("page"),
                }
            )

    return finals


def finalize_tables_unstr() -> List[Dict[str, Any]]:
    src_path = EXTRACT_DIR / "components_tables_unstr.json"
    if not src_path.exists():
        return []
    tables = load_json(src_path)
    payload_path = LLM_DIR / "llm_tables_unstr_result.json"
    payloads = load_json(payload_path) if payload_path.exists() else []
    summary_map = {
        item.get("id"): (item.get("output") or {}).get("table_summary")
        for item in payloads
        if item.get("id")
    }
    finals: List[Dict[str, Any]] = []
    for tbl in tables:
        summary = summary_map.get(tbl.get("id"))
        entry = {
            "id": tbl.get("id"),
            "component_type": tbl.get("component_type"),
            "text": summary if (isinstance(summary, list) and any(isinstance(s, str) and s.strip() for s in summary)) else " ",
            "image_link": tbl.get("image_link"),
            "section_path": tbl.get("section_path"),
            "filename": tbl.get("filename"),
            "page": tbl.get("page"),
        }
        finals.append(entry)
    return finals


def finalize_texts() -> List[Dict[str, Any]]:
    src_path = EXTRACT_DIR / "components_texts.json"
    if not src_path.exists():
        return []
    return load_json(src_path)


def main() -> None:
    FINAL_DIR.mkdir(parents=True, exist_ok=True)

    texts = finalize_texts()
    save_json(FINAL_DIR / "texts_final.json", texts)

    tables_str = finalize_tables_str()
    save_json(FINAL_DIR / "tables_str_final.json", tables_str)

    tables_unstr = finalize_tables_unstr()
    save_json(FINAL_DIR / "tables_unstr_final.json", tables_unstr)

    print(
        f"[INFO] final outputs written: texts={len(texts)}, tables_str={len(tables_str)}, tables_unstr={len(tables_unstr)}"
    )


if __name__ == "__main__":
    main()
