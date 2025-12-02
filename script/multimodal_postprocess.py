#!/usr/bin/env python3
"""Build compact image result files with context text for downstream RAG."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "output" / "sanitize"
TARGET_FILES = ("images_summary_result.json", "images_translation_result.json")
OUTPUT_NAME = "image_final_result.json"
TABLE_INPUT = "tables_result.json"
TABLE_OUTPUT = "table_final_result.json"
TEXT_INPUT = "text_result.json"
TEXT_OUTPUT = "text_final_result.json"


def load_json(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_json(path: Path, data: Iterable[dict]) -> None:
    path.write_text(json.dumps(list(data), ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_summary(value) -> str:
    if isinstance(value, list):
        return " ".join(str(v) for v in value if str(v).strip()).strip()
    return str(value).strip() if value is not None else ""


def chunk_paragraphs(text: str, chunk_size: int | None, overlap: int) -> list[str]:
    """Split text into paragraph chunks, then window-split if chunk_size is set. Placeholders are kept as tokens."""
    if not text:
        return []
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if chunk_size is None or chunk_size <= 0:
        return paragraphs

    chunks: list[str] = []
    for para in paragraphs:
        words = para.split()
        curr: list[str] = []
        curr_len = 0
        i = 0
        while i < len(words):
            w = words[i]
            w_len = len(w) + (1 if curr else 0)
            if curr_len + w_len <= chunk_size:
                curr.append(w)
                curr_len += w_len
                i += 1
            else:
                if curr:
                    chunk_text = " ".join(curr).strip()
                    if chunk_text:
                        chunks.append(chunk_text)
                    # overlap: take last tokens until length >= overlap
                    if overlap > 0:
                        rev = []
                        total = 0
                        for token in reversed(curr):
                            total += len(token) + 1
                            rev.append(token)
                            if total >= overlap:
                                break
                        curr = list(reversed(rev))
                        curr_len = sum(len(t) + 1 for t in curr) - 1 if curr else 0
                    else:
                        curr = []
                        curr_len = 0
                else:
                    # single long token; force add
                    chunks.append(w)
                    i += 1
                    curr = []
                    curr_len = 0
        if curr:
            chunk_text = " ".join(curr).strip()
            if chunk_text:
                chunks.append(chunk_text)
    return chunks


def build_text(entry: dict) -> str:
    section = entry.get("section_path") or entry.get("section") or "미지정"
    filename = entry.get("filename") or ""
    page = entry.get("page")
    summary = normalize_summary(entry.get("summary"))
    prefix = f"[파일: {filename}" if filename else "[파일: 미지정"
    prefix += "]"
    prefix += f" [섹션: {section}]"
    return f"{prefix} {summary}".strip()


def normalize_path(path_str: str) -> str:
    if not path_str:
        return ""
    p = Path(path_str)
    if p.is_absolute():
        return f"components/{p.name}"
    return str(p)


def process_files(dir_path: Path) -> None:
    print(f"[DEBUG] scanning {dir_path}")
    combined: list[dict] = []
    for filename in TARGET_FILES:
        path = dir_path / filename
        records = load_json(path)
        print(f"[DEBUG] {path.name} records={len(records)}")
        for entry in records:
            text = build_text(entry)
            if not text or not normalize_summary(entry.get("summary")):
                continue
            combined.append(
                {
                    "id": entry.get("id", ""),
                    "text": text,
                    "summary": normalize_summary(entry.get("summary")),
                    "keyword": entry.get("keyword", []),
                    "section_path": entry.get("section_path") or entry.get("section"),
                    "page": entry.get("page"),
                    "filename": entry.get("filename"),
                    "image_path": normalize_path(entry.get("image_path")),
                    "doc_folder": entry.get("doc_folder"),
                    "component_map": entry.get("component_map", {}),
                    "components_dir": entry.get("components_dir", "components"),
                }
            )
    if combined:
        out_path = dir_path / OUTPUT_NAME
        print(f"[DEBUG] wrote {out_path} ({len(combined)} items)")
        save_json(out_path, combined)
    else:
        print("[DEBUG] no combined entries, skipping write")


def process_tables(dir_path: Path) -> None:
    path = dir_path / TABLE_INPUT
    records = load_json(path)
    print(f"[DEBUG] {path.name} records={len(records)}")
    combined: list[dict] = []
    for entry in records:
        summary = normalize_summary(entry.get("summary"))
        if not summary:
            continue
        section = entry.get("section_path") or "미지정"
        filename = entry.get("filename") or ""
        prefix = f"[파일: {filename}" if filename else "[파일: 미지정"
        prefix += "]"
        prefix += f" [섹션: {section}]"
        text = f"{prefix} {summary}".strip()
        combined.append(
            {
                "id": entry.get("id", ""),
                "text": text,
                "summary": summary,
                "section_path": section,
                "filename": filename,
                "image_path": normalize_path(entry.get("image_path")),
                "table_image_path": normalize_path(entry.get("table_image_path")),
                "doc_folder": entry.get("doc_folder"),
                "component_map": entry.get("component_map", {}),
            }
        )
    if combined:
        out_path = dir_path / TABLE_OUTPUT
        print(f"[DEBUG] wrote {out_path} ({len(combined)} items)")
        save_json(out_path, combined)
    else:
        print(f"[DEBUG] no table entries in {path}, skipping write")


def process_text(dir_path: Path, chunk_size: int | None, overlap: int) -> None:
    path = dir_path / TEXT_INPUT
    records = load_json(path)
    print(f"[DEBUG] {path.name} records={len(records)}")
    combined: list[dict] = []
    for entry in records:
        text = entry.get("text", "")
        if not text:
            continue
        chunks = chunk_paragraphs(text, chunk_size, overlap)
        for idx, chunk in enumerate(chunks, start=1):
            combined.append(
                {
                    "id": f"{entry.get('id', '')}#chunk{idx}",
                    "text": chunk,
                    "record_type": "text",
                    "source_id": entry.get("id"),
                    "section_path": entry.get("section_path") or entry.get("section"),
                    "filename": entry.get("filename"),
                    "doc_folder": entry.get("doc_folder"),
                }
            )
    if combined:
        out_path = dir_path / TEXT_OUTPUT
        print(f"[DEBUG] wrote {out_path} ({len(combined)} items)")
        save_json(out_path, combined)
    else:
        print(f"[DEBUG] no text entries in {path}, skipping write")


def process_dir(dir_path: Path, chunk_size: int | None, overlap: int) -> None:
    process_files(dir_path)
    process_tables(dir_path)
    process_text(dir_path, chunk_size, overlap)




def main() -> None:
    parser = argparse.ArgumentParser(description="Add context_text to multimodal result JSONs.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="sanitize 루트 (default: output/sanitize)")
    parser.add_argument("--dirs", nargs="*", type=Path, help="특정 디렉터리만 처리")
    parser.add_argument("--text-chunk-size", type=int, default=None, help="텍스트 청크 최대 길이 (문자). 미지정이면 문단 단위 유지.")
    parser.add_argument("--text-chunk-overlap", type=int, default=0, help="텍스트 청크 겹치는 길이 (문자).")
    args = parser.parse_args()

    targets = args.dirs or [p for p in args.root.iterdir() if p.is_dir()]
    for d in targets:
        process_dir(d, args.text_chunk_size, args.text_chunk_overlap)


if __name__ == "__main__":
    main()
