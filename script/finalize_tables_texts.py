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
        prefix_parts = []
        if tbl.get("filename"):
            prefix_parts.append(f"[문서: {tbl.get('filename')}]")
        if tbl.get("section_path"):
            prefix_parts.append(f"[경로: {tbl.get('section_path')}]")
        prefix = " ".join(prefix_parts)
        base = {
            "id": tbl.get("id"),
            "placeholder": tbl.get("placeholder"),
            "component_type": tbl.get("component_type"),
            "text": (f"{prefix} {tbl.get('row_flatten')}".strip() if prefix else tbl.get("row_flatten")),
            "image_link": tbl.get("image_link"),
            "section_path": tbl.get("section_path"),
            "filename": tbl.get("filename"),
            "page": tbl.get("page"),
        }
        finals.append(base)

        rows = tbl.get("row_flatten") or []
        if isinstance(rows, list):
            for idx, row in enumerate(rows, start=1):
                row_text = (f"{prefix} {row}".strip() if prefix else row)
                finals.append(
                    {
                        "id": f"{tbl.get('id')}#{idx}",
                        "component_type": "table_row",
                        "text": row_text,
                        "image_link": tbl.get("image_link"),
                        "section_path": tbl.get("section_path"),
                        "filename": tbl.get("filename"),
                        "page": tbl.get("page"),
                    }
                )

        if tbl.get("id") in summaries:
            sum_text = summaries[tbl.get("id")]
            sum_text = (f"{prefix} {sum_text}".strip() if prefix else sum_text)
            finals.append(
                {
                    "id": f"{tbl.get('id')}#summary",
                    "component_type": "table_summary",
                    "text": sum_text,
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
        fallback_text = "No Description"
        prefix_parts = []
        if tbl.get("filename"):
            prefix_parts.append(f"[문서: {tbl.get('filename')}]")
        if tbl.get("section_path"):
            prefix_parts.append(f"[경로: {tbl.get('section_path')}]")
        prefix = " ".join(prefix_parts)
        base_text = (
            summary
            if (isinstance(summary, list) and any(isinstance(s, str) and s.strip() for s in summary))
            else (tbl.get("full_html") or fallback_text)
        )
        if prefix:
            base_text = f"{prefix} {base_text}".strip()
        entry = {
            "id": tbl.get("id"),
            "placeholder": tbl.get("placeholder"),
            "component_type": tbl.get("component_type"),
            "text": base_text,
            "image_link": tbl.get("image_link"),
            "section_path": tbl.get("section_path"),
            "filename": tbl.get("filename"),
            "page": tbl.get("page"),
        }
        finals.append(entry)
    return finals


def finalize_images_formula() -> List[Dict[str, Any]]:
    src_path = EXTRACT_DIR / "components_images_formula.json"
    if not src_path.exists():
        return []
    comps = load_json(src_path)
    finals: List[Dict[str, Any]] = []
    for comp in comps:
        prefix_parts = []
        if comp.get("filename"):
            prefix_parts.append(f"[문서: {comp.get('filename')}]")
        if comp.get("section_path"):
            prefix_parts.append(f"[경로: {comp.get('section_path')}]")
        prefix = " ".join(prefix_parts)
        text_val = comp.get("description") or "No Description"
        if prefix:
            text_val = f"{prefix} {text_val}".strip()
        finals.append(
            {
                "id": comp.get("id"),
                "placeholder": comp.get("placeholder"),
                "component_type": comp.get("component_type"),
                "text": text_val,
                "image_link": comp.get("image_link"),
                "section_path": comp.get("section_path"),
                "filename": comp.get("filename"),
                "page": comp.get("page"),
            }
        )
    return finals


def _load_image_results(filename: str) -> Dict[str, Dict[str, Any]]:
    path = LLM_DIR / filename
    if not path.exists():
        return {}
    data = load_json(path)
    result_map: Dict[str, Dict[str, Any]] = {}
    for item in data:
        iid = item.get("id")
        out = item.get("output") or {}
        if iid:
            result_map[iid] = {
                "summary": out.get("image_summary"),
                "keyword": out.get("image_keyword") if isinstance(out.get("image_keyword"), list) else [],
            }
    return result_map


def _finalize_images_generic(src_path: Path, result_file: str) -> List[Dict[str, Any]]:
    if not src_path.exists():
        return []
    comps = load_json(src_path)
    result_map = _load_image_results(result_file)
    finals: List[Dict[str, Any]] = []
    for comp in comps:
        iid = comp.get("id")
        res = result_map.get(iid, {})
        summary = res.get("summary")
        keywords = res.get("keyword") or []
        text_val = summary if isinstance(summary, str) and summary.strip() else "No Description"
        prefix_parts = []
        if comp.get("filename"):
            prefix_parts.append(f"[문서: {comp.get('filename')}]")
        if comp.get("section_path"):
            prefix_parts.append(f"[경로: {comp.get('section_path')}]")
        prefix = " ".join(prefix_parts)
        if prefix:
            text_val = f"{prefix} {text_val}".strip()
        finals.append(
            {
                "id": iid,
                "placeholder": comp.get("placeholder"),
                "component_type": comp.get("component_type"),
                "text": text_val,
                "keyword": keywords,
                "image_link": comp.get("image_link"),
                "section_path": comp.get("section_path"),
                "filename": comp.get("filename"),
                "page": comp.get("page"),
            }
        )
    return finals


def finalize_images_sum() -> List[Dict[str, Any]]:
    return _finalize_images_generic(EXTRACT_DIR / "components_images_sum.json", "llm_images_sum_result.json")


def finalize_images_trans() -> List[Dict[str, Any]]:
    return _finalize_images_generic(EXTRACT_DIR / "components_images_trans.json", "llm_images_trans_result.json")


def finalize_texts() -> List[Dict[str, Any]]:
    src_path = EXTRACT_DIR / "components_texts.json"
    if not src_path.exists():
        return []
    texts = load_json(src_path)
    finals: List[Dict[str, Any]] = []
    for item in texts:
        filename = item.get("filename") or ""
        section_path = item.get("section_path") or ""
        prefix_parts = []
        if filename:
            prefix_parts.append(f"[문서: {filename}]")
        if section_path:
            prefix_parts.append(f"[경로: {section_path}]")
        prefix = " ".join(prefix_parts)
        text_body = item.get("text") or ""
        combined = f"{prefix} {text_body}".strip() if prefix else str(text_body)
        new_item = dict(item)
        new_item["text"] = combined
        finals.append(new_item)
    return finals


def main() -> None:
    FINAL_DIR.mkdir(parents=True, exist_ok=True)

    texts = finalize_texts()
    save_json(FINAL_DIR / "texts_final.json", texts)

    tables_str = finalize_tables_str()
    save_json(FINAL_DIR / "tables_str_final.json", tables_str)

    tables_unstr = finalize_tables_unstr()
    save_json(FINAL_DIR / "tables_unstr_final.json", tables_unstr)

    images_formula = finalize_images_formula()
    save_json(FINAL_DIR / "images_formula_final.json", images_formula)

    images_sum = finalize_images_sum()
    save_json(FINAL_DIR / "images_sum_final.json", images_sum)

    images_trans = finalize_images_trans()
    save_json(FINAL_DIR / "images_trans_final.json", images_trans)

    print(
        f"[INFO] final outputs written: texts={len(texts)}, tables_str={len(tables_str)}, "
        f"tables_unstr={len(tables_unstr)}, images_formula={len(images_formula)}, "
        f"images_sum={len(images_sum)}, images_trans={len(images_trans)}"
    )


if __name__ == "__main__":
    main()
