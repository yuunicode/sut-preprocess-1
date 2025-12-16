#!/usr/bin/env python3
"""Finalize tables/texts/images with LLM outputs into output/final."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
EXTRACT_DIR = REPO_ROOT / "output" / "extract"
LLM_DIR = REPO_ROOT / "output" / "llm"
FINAL_DIR = REPO_ROOT / "output" / "final"
PLACEHOLDER_SPAN_RE = re.compile(r"\{\{[^{}]+\}\}")
INVALID_TRANS_RE = re.compile(r"[\u4e00-\u9fff\u0400-\u04FF\u0600-\u06FF\u0660-\u0669\u06F0-\u06F9]")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def display_filename_for_prefix(filename: str) -> str:
    """
    TP-XXX-XXX-XXX 접두어와 (Rev...) 이후는 제거해 prefix에 쓰일 문서명을 만든다.
    """
    if not filename:
        return ""
    stem = Path(filename).stem
    rev_idx = stem.find("(Rev")
    base = stem[:rev_idx].strip() if rev_idx != -1 else stem
    base = re.sub(r"\s+", " ", base).strip()
    tp_match = re.match(r"(TP-\d{3}-\d{3}-\d{3})(?:\s+(.*))?$", base)
    if tp_match:
        title = (tp_match.group(2) or "").strip()
        return title or base
    return base


def load_table_summaries() -> Dict[str, List[str]]:
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
        display_name = display_filename_for_prefix(tbl.get("filename") or "")
        prefix_parts = []
        if display_name:
            prefix_parts.append(f"[문서: {display_name}]")
        if tbl.get("section_path"):
            prefix_parts.append(f"[경로: {tbl.get('section_path')}]")
        prefix = " ".join(prefix_parts)
        original = tbl.get("row_flatten")
        base_text = (f"{prefix} {original}".strip() if prefix else original)
        base = {
            "id": tbl.get("id"),
            "placeholder": tbl.get("placeholder"),
            "component_type": tbl.get("component_type"),
            "original": original,
            "text": base_text,
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
                        "original": row,
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
                    "original": summaries[tbl.get("id")],
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
        display_name = display_filename_for_prefix(tbl.get("filename") or "")
        prefix_parts = []
        if display_name:
            prefix_parts.append(f"[문서: {display_name}]")
        if tbl.get("section_path"):
            prefix_parts.append(f"[경로: {tbl.get('section_path')}]")
        prefix = " ".join(prefix_parts)
        original = (
            summary
            if (isinstance(summary, list) and any(isinstance(s, str) and s.strip() for s in summary))
            else (tbl.get("full_html") or fallback_text)
        )
        base_text = f"{prefix} {original}".strip() if prefix else original
        entry = {
            "id": tbl.get("id"),
            "placeholder": tbl.get("placeholder"),
            "component_type": tbl.get("component_type"),
            "original": original,
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
        display_name = display_filename_for_prefix(comp.get("filename") or "")
        prefix_parts = []
        if display_name:
            prefix_parts.append(f"[문서: {display_name}]")
        if comp.get("section_path"):
            prefix_parts.append(f"[경로: {comp.get('section_path')}]")
        prefix = " ".join(prefix_parts)
        original = comp.get("description") or "No Description"
        text_val = f"{prefix} {original}".strip() if prefix else original
        finals.append(
            {
                "id": comp.get("id"),
                "placeholder": comp.get("placeholder"),
                "component_type": comp.get("component_type"),
                "original": original,
                "text": text_val,
                "image_link": comp.get("image_link"),
                "section_path": comp.get("section_path"),
                "filename": comp.get("filename"),
                "page": comp.get("page"),
            }
        )
    return finals


def _load_image_results(filename: str) -> Dict[tuple[str, str | None], Dict[str, Any]]:
    path = LLM_DIR / filename
    if not path.exists():
        return {}
    data = load_json(path)
    result_map: Dict[tuple[str, str | None], Dict[str, Any]] = {}
    for item in data:
        iid = item.get("id")
        input_payload = item.get("input") or {}
        image_link = input_payload.get("image_link")
        out = item.get("output") or {}
        if iid:
            key = (iid, image_link or None)
            result_map[key] = {
                "summary": out.get("image_summary"),
                "keyword": out.get("image_keyword") if isinstance(out.get("image_keyword"), list) else [],
            }
    return result_map


def _finalize_images_generic(
    src_path: Path, result_file: str, fallback_on_invalid: bool = False
) -> List[Dict[str, Any]]:
    if not src_path.exists():
        return []
    comps = load_json(src_path)
    result_map = _load_image_results(result_file)
    finals: List[Dict[str, Any]] = []
    for comp in comps:
        iid = comp.get("id")
        image_link = comp.get("image_link")
        res = result_map.get((iid, image_link)) or result_map.get((iid, None), {})
        summary = res.get("summary")
        keywords = res.get("keyword") or []
        if isinstance(summary, list):
            original = "".join([s for s in summary if isinstance(s, str)]).strip()
        else:
            original = summary if isinstance(summary, str) and summary.strip() else ""
        if fallback_on_invalid and INVALID_TRANS_RE.search(original or ""):
            original = comp.get("description") or ""
        if not original:
            # 내용이 없으면 적재 대상에서 제외
            continue
        display_name = display_filename_for_prefix(comp.get("filename") or "")
        prefix_parts = []
        if display_name:
            prefix_parts.append(f"[문서: {display_name}]")
        if comp.get("section_path"):
            prefix_parts.append(f"[경로: {comp.get('section_path')}]")
        prefix = " ".join(prefix_parts)
        text_val = f"{prefix} {original}".strip() if prefix else original
        finals.append(
            {
                "id": iid,
                "placeholder": comp.get("placeholder"),
                "component_type": comp.get("component_type"),
                "original": original,
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
    return _finalize_images_generic(
        EXTRACT_DIR / "components_images_trans.json",
        "llm_images_trans_result.json",
        fallback_on_invalid=True,
    )


def _split_text_with_placeholders(text: str, chunk_size: int, chunk_overlap: int) -> List[str]:
    """Split text into chunks of roughly chunk_size, ensuring placeholders are not split."""
    if chunk_size <= 0:
        return [text]
    if chunk_overlap < 0:
        chunk_overlap = 0

    spans = [(m.start(), m.end()) for m in PLACEHOLDER_SPAN_RE.finditer(text)]
    chunks: List[str] = []
    n = len(text)
    start = 0

    def adjust_to_placeholder(pos: int) -> int:
        for s, e in spans:
            if s < pos < e:
                return s
        return pos

    while start < n:
        end = start + chunk_size
        if end < n:
            for s, e in spans:
                if s < end < e:
                    end = e
        end = min(end, n)
        if end <= start:
            end = min(start + chunk_size, n)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        next_start = end - chunk_overlap
        if next_start <= start:
            next_start = end
        start = adjust_to_placeholder(next_start)
    return chunks


def finalize_texts(chunk_size: int = 0, chunk_overlap: int = 0) -> List[Dict[str, Any]]:
    src_path = EXTRACT_DIR / "components_texts.json"
    if not src_path.exists():
        return []
    texts = load_json(src_path)
    finals: List[Dict[str, Any]] = []
    for item in texts:
        filename = item.get("filename") or ""
        display_name = display_filename_for_prefix(filename)
        section_path = item.get("section_path") or ""
        prefix_parts = []
        if display_name:
            prefix_parts.append(f"[문서: {display_name}]")
        if section_path:
            prefix_parts.append(f"[경로: {section_path}]")
        prefix = " ".join(prefix_parts)
        text_body = item.get("text") or ""
        combined = f"{prefix} {text_body}".strip() if prefix else str(text_body)
        chunks = _split_text_with_placeholders(
            combined,
            chunk_size=chunk_size if chunk_size and chunk_size > 0 else 0,
            chunk_overlap=chunk_overlap if chunk_overlap and chunk_overlap > 0 else 0,
        )
        if len(chunks) <= 1:
            new_item = dict(item)
            new_item["text"] = combined
            finals.append(new_item)
        else:
            for cidx, chunk in enumerate(chunks, 1):
                new_item = dict(item)
                new_item["id"] = f"{item.get('id')}#c{cidx}"
                new_item["text"] = chunk
                finals.append(new_item)
    return finals


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Finalize JSON outputs into output/final.")
    parser.add_argument("--chunk-size", type=int, default=0, help="텍스트 청크 크기 (0이면 청크 분할 없음)")
    parser.add_argument("--chunk-overlap", type=int, default=0, help="청크 간 겹치는 문자 수")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    FINAL_DIR.mkdir(parents=True, exist_ok=True)

    texts = finalize_texts(chunk_size=args.chunk_size, chunk_overlap=args.chunk_overlap)
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
        f"[INFO] final outputs written: texts={len(texts)}, tables_str={len(tables_str)}, tables_unstr={len(tables_unstr)}, "
        f"images_formula={len(images_formula)}, images_sum={len(images_sum)}, images_trans={len(images_trans)}"
    )


if __name__ == "__main__":
    main()
