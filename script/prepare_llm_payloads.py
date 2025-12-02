#!/usr/bin/env python3
"""Prepare JSON payloads for table/image LLM tasks without executing any model."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bs4 import BeautifulSoup
DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "output" / "sanitize"

TABLE_ANALYSIS_INSTRUCTIONS = """당신은 제철/제선/공정 운영 분야의 전문 테이블 분석가입니다.

아래 테이블 이미지를 기반으로 테이블 자체의 의미를 해석한 요약을 생성하세요.
- 테이블 구조/헤더/HTML/Markdown/JSON 형식은 출력하지 마십시오.
- 숫자, 조건, 임계값, 단계 구분을 그대로 반영하여 운영 기준/의사결정 규칙/경향성을 설명하십시오.
- 표 안 그래프/아이콘이 있으면 추세·비교·경향을 텍스트로 표현하십시오.

출력 형식:
{
  "table_summary": [
      "첫 번째 핵심 요약 문장",
      "두 번째 핵심 요약 문장",
      ...
  ]
}

제한:
- summary 문장은 4~8개.
- 각 문장은 테이블 의미 해석에 집중하고, 통계·조건·수치를 생략하지 말 것.
- 헤더 설명이나 “이 테이블은 ~~이다” 같은 메타 설명 금지.
- 같은 문장 반복 금지."""
# Alt-specific instructions
IMAGE_SUMMARY_INSTRUCTIONS = (
    "너는 고로 조업 전문가이다. 주어진 섹션 제목과 문맥 HTML, 이미지 파일을 참고하여 이미지 내용을 전문적인 시각에서 한국어로 요약하라. "
    "원문에 고유명사/기술용어가 영어로 표기된 경우, 한국어 설명과 함께 원 영문 표기도 병기하라(예: '풍량(Blast Volume)'). " 
    "핵심 요약은 1~5문장 정도로 충분히 작성하고, 한국어/영어 키워드를 5~15개 제시하라."
)

COMPLEX_BLOCK_INSTRUCTIONS = (
    "너는 고로 조업 전문가이다. 주어진 문맥과 이미지를 참고하여 복잡한 공정/설비 블록도가 설명하는 대상과 목적을 간결하게 요약하라. "
    "각 단계나 구성 요소가 무엇을 의미하는지 1~5문장으로 정리하고, 한국어/영어 키워드를 5~15개 제시하라."
)

EQUATION_BLOCK_INSTRUCTIONS = (
    "너는 고로 조업 전문가이다. 주어진 문맥과 이미지를 참고하여 수식/기호가 의미하는 물리량과 단위에 유의하여 사람이 보기 쉬운 수식으로 표현해라. "
    "수식에 등장하는 기호와 주석('주)', 혹은 그 아래 문장들)이 일치할 경우 그 관계를 수식 뒤에 추가하여라. 키워드는 단위와 사용된 용어를 제시해라."
)

IMAGE_TRANSLATION_INSTRUCTIONS = (
    "너는 고로 조업 전문가이다. 이미지 alt 설명을 한국어로 요약하되, 고유 명사·설비·공정 등은 영어 원문을 그대로 유지하거나 병기하라. "
    "가능하면 원문 영문 표현을 최대한 지키고, 필요시 한국어 설명 뒤 괄호로 영어를 덧붙여라. "
    "한국어/영어 키워드를 5~15개 제시해라."
)


def parse_table_general(html: str) -> dict | None:
    """Best-effort span 확장 테이블 파싱. 테이블이 없으면 None."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return None

    # 1) raw_rows: tag, value, rowspan, colspan
    raw_rows = []
    for tr in table.find_all("tr"):
        row = []
        for cell in tr.find_all(["th", "td"]):
            # img alt는 별도로 추출해 한국어 요약 시 참고하도록 포함
            img_alts = []
            for img in cell.find_all("img"):
                alt = img.get("alt", "").strip()
                if alt:
                    img_alts.append(alt)
                img.decompose()
            cell_text = cell.get_text(strip=True)
            if img_alts:
                alt_text = " / ".join(img_alts)
                cell_text = f"{cell_text} (이미지: {alt_text})" if cell_text else f"(이미지: {alt_text})"
            row.append(
                {
                    "tag": cell.name,
                    "value": cell_text,
                    "rowspan": int(cell.get("rowspan", 1)),
                    "colspan": int(cell.get("colspan", 1)),
                }
            )
        raw_rows.append(row)

    if not raw_rows:
        return None

    # 2) max column count after expanding colspans
    max_cols = max(sum(cell["colspan"] for cell in row) for row in raw_rows)
    max_row_reach = max((r + cell["rowspan"]) for r, row in enumerate(raw_rows) for cell in row)
    n_rows = max(len(raw_rows), max_row_reach)
    if max_cols <= 0 or n_rows <= 0:
        return None

    cells = [[""] * max_cols for _ in range(n_rows)]
    is_header = [[False] * max_cols for _ in range(n_rows)]
    filled = [[False] * max_cols for _ in range(n_rows)]

    # 3) span 전개
    for r, row in enumerate(raw_rows):
        c = 0
        for cell in row:
            while c < max_cols and filled[r][c]:
                c += 1
            for rr in range(r, r + cell["rowspan"]):
                for cc in range(c, c + cell["colspan"]):
                    if rr >= n_rows or cc >= max_cols:
                        continue
                    cells[rr][cc] = cell["value"]
                    is_header[rr][cc] = cell["tag"] == "th"
                    filled[rr][cc] = True
            c += cell["colspan"]

    # 4) 최소 메타: 헤더 플래그와 헤더 후보
    return {
        "n_rows": n_rows,
        "n_cols": max_cols,
        "cells": cells,
        "is_header": is_header,
        "first_row_is_col_header": any(is_header[0]) if is_header else False,
        "first_col_is_row_header": any(is_header[r][0] for r in range(n_rows)) if is_header else False,
    }


def table_preview_text(parsed: dict | None) -> str:
    """LLM 요약 입력을 위한 간단한 전개 텍스트."""
    if not parsed:
        return ""
    lines = []
    cells = parsed.get("cells") or []
    for idx, row in enumerate(cells, start=1):
        lines.append(f"Row {idx}: " + " | ".join(row))
    return "\n".join(lines)


def is_table_structured(html: str, parsed: dict | None) -> bool:
    """row마다 colspan 합이 달라지면 무너진 테이블로 간주."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return True
    cells = table.find_all(["th", "td"])
    if not cells:
        return True
    col_sums = []
    for tr in table.find_all("tr"):
        col_sums.append(sum(int(c.get("colspan", 1)) for c in tr.find_all(["th", "td"])))
    # colspan 합이 한 행이라도 다르면 구조가 무너진 것으로 판단
    if len(set(col_sums)) > 1:
        return False
    return parsed is not None


def normalize_row_strings(parsed: dict) -> list[str]:
    """헤더를 이용해 row를 key-value 문자열로 평탄화. 다단 헤더도 누적 표기."""
    cells = parsed.get("cells") or []
    is_header = parsed.get("is_header") or []
    if not cells:
        return []
    header_rows = [cells[idx] for idx, row in enumerate(is_header) if any(row)]
    row_strings: list[str] = []
    for ridx, row in enumerate(cells):
        # 헤더 행은 건너뛰기
        if ridx < len(header_rows):
            continue
        parts = []
        for cidx, val in enumerate(row):
            header_chain = []
            for hrow in header_rows:
                if cidx < len(hrow) and hrow[cidx]:
                    header_chain.append(hrow[cidx])
            key = " > ".join(header_chain) if header_chain else f"col{cidx+1}"
            parts.append(f"{key}: {val}")
        row_strings.append(" | ".join(parts))
    return row_strings


def extract_headers(parsed: dict | None) -> tuple[list[str], list[str]]:
    """col_headers: 헤더 행의 각 열 값, row_headers: 헤더가 아닌 행의 첫 열 값."""
    if not parsed:
        return [], []
    cells = parsed.get("cells") or []
    is_header = parsed.get("is_header") or []
    col_headers: list[str] = []
    for idx, row in enumerate(cells):
        if idx < len(is_header) and any(is_header[idx]):
            if not col_headers:
                col_headers = row
    row_headers: list[str] = []
    for idx, row in enumerate(cells):
        if idx < len(is_header) and any(is_header[idx]):
            continue
        if row:
            row_headers.append(row[0])
    return col_headers, row_headers


def load_components(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_payload(path: Path, payload: list[dict]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def process_directory(dir_path: Path) -> None:
    components_path = dir_path / "components.json"
    if not components_path.exists():
        return
    components = load_components(components_path)

    tables_payload = []
    tables = components.get("tables", [])
    for table in tables:
        # TABLE_001은 별도 처리 대상: summary/flatten 생성하지 않음
        if table.get("id") == "TABLE_001":
            continue
        try:
            parsed_table = parse_table_general(table.get("table_html", ""))
        except Exception as exc:  # 안전장치: 파싱 실패 시 공백 유지
            print(f"[WARN] table parse failed for {table.get('id')}: {exc}")
            parsed_table = None
        structured = is_table_structured(table.get("table_html", ""), parsed_table)
        image_path = table.get("table_image_path") or table.get("image_path", "")
        col_headers, row_headers = extract_headers(parsed_table)
        # Structured 테이블인 경우: row 평탄화/요약용 payload 추가
        if structured and parsed_table:
            row_strings = normalize_row_strings(parsed_table)
            for idx, row_str in enumerate(row_strings, start=1):
                tables_payload.append(
                    {
                        "id": table["id"],
                        "id_sub": f"{table['id']}#{idx}",
                        "parent_id": table["id"],
                        "task": "table_flatten",
                        "ell_flatten": True,
                        "summary": [row_str],
                        "section_path": table.get("section_path", ""),
                        "page": table.get("page"),
                        "filename": table.get("filename") or default_filename,
                        "table_image_path": image_path,
                        "col_headers": col_headers,
                        "row_headers": row_headers,
                    }
                )
            tables_payload.append(
                {
                    "id": table["id"],
                    "id_sub": f"{table['id']}#summary",
                    "parent_id": table["id"],
                    "task": "table_summary",
                    "instructions": (
                        "아래 파싱된 테이블을 읽고 3~5줄의 핵심 요약을 bullet 없이 한글 문장으로 작성하라. "
                        "헤더만 나열하지 말고, 각 행/셀의 수치·조건·조치(Action)도 함께 설명하라."
                    ),
                    "parsed_table": parsed_table,
                    "parsed_table_text": table_preview_text(parsed_table),
                    "row_flatten": row_strings,
                    "section_path": table.get("section_path", ""),
                    "page": table.get("page"),
                    "filename": table.get("filename") or default_filename,
                    "table_image_path": image_path,
                    "col_headers": col_headers,
                    "row_headers": row_headers,
                }
            )
        # Unstructured 테이블은 별도 요약 task로만 처리
        if not structured:
            cell_text: list[str] = []
            cells = (parsed_table or {}).get("cells") or []
            seen = set()
            for row in cells:
                for val in row:
                    v = (val or "").strip()
                    if not v:
                        continue
                    if v in seen:
                        continue
                    seen.add(v)
                    cell_text.append(v)
            tables_payload.append(
                {
                    "id": table["id"],
                    "task": "table_unstructured",
                    "instructions": (
                        "아래 테이블은 구조가 무너져 있으며 HTML도 신뢰할 수 없습니다. "
                        "표 구조 복원은 시도하지 말고, 파싱된 JSON 행 정보를 기반으로 행 단위 조건·단계·값·조치(Action)를 빠짐없이 추출해 "
                        "줄 수 제한 없이 한 줄씩 나열하듯 summary만 작성하라. 키워드는 뽑지 말 것."
                    ),
                    "is_structured": False,
                    "original_html": table.get("table_html", ""),
                    "parsed_table": parsed_table,
                    "cells": (parsed_table or {}).get("cells", []),
                    "cell_text": cell_text,
                    "parsed_table_text": table_preview_text(parsed_table),
                    "table_image_path": image_path,
                    "context_html": table.get("context_html", ""),
                    "notes_html": table.get("notes_html", ""),
                    "section_path": table.get("section_path", ""),
                    "page": table.get("page"),
                    "filename": table.get("filename") or default_filename,
                    "col_headers": col_headers,
                    "row_headers": row_headers,
                }
            )

    images_summary_payload = []
    for item in components.get("images_summary", []):
        alt_lower = (item.get("alt") or "").strip().lower()
        if alt_lower == "complex-block snippet":
            instructions = COMPLEX_BLOCK_INSTRUCTIONS
        elif alt_lower == "equation-block snippet":
            instructions = EQUATION_BLOCK_INSTRUCTIONS
        else:
            instructions = IMAGE_SUMMARY_INSTRUCTIONS
        if alt_lower == "equation-block snippet":
            ctx_html = ""
        else:
            ctx_html = item.get("context_html") or item.get("block_html", "")
        images_summary_payload.append(
            {
                "id": item["id"],
                "task": "image_summary",
                "instructions": instructions,
                "section": item.get("section", ""),
                "parent_section": item.get("parent_section", ""),
                "section_path": item.get("section_path", ""),
                "context_html": ctx_html,
                "raw_html": item.get("raw_html", ""),
                "image": item.get("image", ""),
                "alt": item.get("alt", ""),
                "page": item.get("page"),
                "filename": item.get("filename", ""),
            }
        )

    images_translation_payload = []
    for item in components.get("images_translation", []):
        images_translation_payload.append(
            {
                "id": item["id"],
                "task": "image_translation",
                "instructions": IMAGE_TRANSLATION_INSTRUCTIONS,
                "section": item.get("section", ""),
                "parent_section": item.get("parent_section", ""),
                "section_path": item.get("section_path", ""),
                "alt": item.get("alt", ""),
                "image": item.get("image", ""),
                "raw_html": item.get("raw_html", ""),
                "context_html": item.get("context_html", ""),
                "page": item.get("page"),
                "filename": item.get("filename", ""),
            }
        )

    if tables_payload:
        write_payload(dir_path / "tables_payload.json", tables_payload)
    if images_summary_payload:
        write_payload(dir_path / "images_summary_payload.json", images_summary_payload)
    if images_translation_payload:
        write_payload(dir_path / "images_translation_payload.json", images_translation_payload)

    print(f"[INFO] Payloads prepared under {dir_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate LLM payload JSON files from components.json.")
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="Directory containing components.json files (default: output/sanitize).",
    )
    parser.add_argument(
        "--dirs",
        nargs="*",
        type=Path,
        help="Specific directories to process. Defaults to every folder under --root with components.json.",
    )
    args = parser.parse_args()

    targets = args.dirs or sorted(path.parent for path in args.root.rglob("components.json"))
    if not targets:
        print("[WARN] No components.json files found.")
        return

    for directory in targets:
        process_directory(directory)


if __name__ == "__main__":
    main()
