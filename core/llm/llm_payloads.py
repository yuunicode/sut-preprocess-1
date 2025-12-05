#!/usr/bin/env python3
"""Build LLM 입력용 payloads from aggregated components (split JSONs)."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, List, Optional
from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parents[2]
EXTRACT_DIR = REPO_ROOT / "output" / "extract"
LLM_DIR = REPO_ROOT / "output" / "llm"
DEFAULT_WINDOW = 15
DEFAULT_DIGIT_ONLY_RATIO_THRESHOLD = 0.3
DIGIT_HEAVY_LOG = REPO_ROOT / "logs" / "digit_heavy_tables.log"

TABLE_STR_INSTRUCTIONS = """
너는 제선·제철 공정 문서에서 제공되는 테이블 데이터를 해석해 핵심 의미를 압축해 전달하는 기술 요약 담당자이다. 
아래 입력 변수(row_flatten, filename, image_link)를 참고해 테이블의 기술적 의미를 정확하게 정리하라.

- 참고: row_flatten={row_flatten}, filename={filename}, image_link={image_link}
- 테이블은 각 행·셀에 담긴 수치, 조건, 정의, 조치(Action)를 함께 설명하라.
- 표 구조나 헤더를 나열하지 말고, 조업 기준·조건·임계값·경향성을 4~8문장으로 요약한다.
- 테이블에 포함된 수치·단위·조건을 가능한 한 그대로 유지한다.

출력 형식:
{
  "table_summary": [
    "문장1",
    "문장2",
    ...
  ]
}
"""

TABLE_UNSTR_INSTRUCTIONS = """
너는 제선·제철 공정 문서의 비정형 테이블을 해석해 핵심 의미를 전달하는 기술 요약 담당자이다. 
아래 입력 변수(section_path, filename, image_link)를 참고하여 테이블 이미지에서 중요 수치/단위/고로별 작업방향을 요약해라.

- 참고: section_path={section_path}, filename={filename}, image_link={image_link}
- 테이블의 각 행·셀에 담긴 수치, 조건, 정의, 조치(Action)를 함께 설명하라.
- H스트·숫자·단위·이미지를 기반으로 4~8문장으로 의미를 재구성한다.
- 테이블의 수치·단위·조건·기준은 가능한 한 원문 그대로 유지한다.

출력 형식:
{
  "table_summary": [
    "문장1",
    "문장2",
    ...
  ]
}
"""

IMAGE_TRANS_INSTRUCTIONS = """
너는 제선·제철 공정의 시각 자료(도표·그래프·다이어그램)를 해석하여 핵심 정보를 명확히 전달하는 이미지 분석 담당자이다.
아래 입력 변수(description, image_link, section_path)를 참고하되, 최종 판단은 이미지 자체의 내용 기반으로 수행하라.

- 참고: description={description}, image_link={image_link}, section_path={section_path}
- 첫 문장은 반드시 '이 도표는', '이 그림은', 또는 '이 그래프는'으로 시작한다.
- 그래프·차트는 추세, 비교, 증감, 변수 간 관계를 명확히 설명한다.
- 다이어그램·공정도는 흐름, 구조, 장치 간 상호작용을 명확히 설명한다.
- 기본 서술은 한국어로 작성하되, 공정 전문 용어는 영어/한국어 병기 가능하다.
- 주어진 description을 한국어로 풀어 설명하고, 이미지에서 읽히는 추가 정보(축/눈금/흐름 등)가 있으면 보완해 적어라.
- 단위(Unit)는 반드시 정확하게 보존한다.
- 4~5문장으로 요약하고, 핵심 개념·변수·전문 용어·단위를 한국어/영어 키워드 5~15개로 정리한다.

출력 형식:
{
  "image_summary": "요약 문단",
  "image_keyword": ["키워드1", "키워드2", ...]
}
"""

IMAGE_SUM_INSTRUCTIONS = """
너는 제선·제철 공정의 시각 자료를 직접 분석하여 의미를 추론하는 이미지 분석 담당자이다.
description이 없으므로 이미지 자체의 구조와 주변 context를 기반으로 요약하라.

- 참고: image_link={image_link}, context_before={context_before}, context_after={context_after}
- context_before/after에 '<그림 ...>'처럼 이미지를 설명하는 문장이 있으면 필요한 범위에서 참고해 의미를 보완한다.
- 첫 문장은 반드시 '이 도표는', '이 그림은', 또는 '이 그래프는'으로 시작한다.
- 그래프/차트는 x축·y축 이름/눈금·단위, 비교 그룹, 추세(증감/극값/비교)를 구체적으로 설명한다.
- 다이어그램/공정도/플로우는 단계별 흐름과 전환 조건을 빠짐없이 기술한다.
- 기본 서술은 한국어를 사용하되, 공정 전문 용어는 영어/한국어 병기 가능하다.
- 단위(Unit)는 반드시 정확하게 유지한다.
- 4~5문장 요약과 함께 핵심 개념·변수·전문 용어·단위를 한국어/영어 키워드 5~15개로 제시한다.

출력 형식:
{
  "image_summary": "요약 문단",
  "image_keyword": ["키워드1", "키워드2", ...]
}
"""


def load_json(path: Path) -> list | dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def find_cleaned_path_from_image_link(image_link: str) -> Optional[Path]:
    """image_link에서 문서 폴더를 추론해 _cleaned.md 경로를 만든다."""
    if not image_link:
        return None
    p = Path(image_link)
    try:
        idx = p.parts.index("components")
    except ValueError:
        return None
    doc_dir = Path(*p.parts[:idx])
    doc_name = doc_dir.name
    return doc_dir / f"{doc_name}_cleaned.md"


def get_context_windows(cleaned_path: Path, placeholder_id: str, window: int = DEFAULT_WINDOW) -> tuple[str, str]:
    """cleaned md에서 플레이스홀더 주변 앞/뒤 토큰(window) 반환. HTML 주석은 제외."""
    if not cleaned_path or not cleaned_path.exists():
        return "", ""
    text = cleaned_path.read_text(encoding="utf-8")
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    target = f"{{{{{placeholder_id}}}}}"
    tokens = re.findall(r"\S+", text)
    try:
        idx = tokens.index(target)
    except ValueError:
        return "", ""
    before = " ".join(tokens[max(0, idx - window) : idx]).strip()
    after = " ".join(tokens[idx + 1 : idx + 1 + window]).strip()
    return before, after


def normalize_ratio_threshold(value: float) -> float:
    """Allow percent(0~100) or ratio(0~1) inputs, clamped to 0~1."""
    if value > 1:
        value = value / 100.0
    return max(0.0, min(1.0, value))


def cell_is_numeric_only(text: str) -> bool:
    """Return True if the cell contains only numeric tokens (허용: 숫자, ., ,, +, -, /, %)."""
    if not text:
        return False
    normalized = re.sub(r"[\s,]", "", text)
    if not normalized:
        return False
    return bool(re.fullmatch(r"[0-9.+/%-]+", normalized))


def iter_table_cell_texts(table_html: str) -> list[str]:
    if not table_html:
        return []
    soup = BeautifulSoup(table_html, "html.parser")
    table = soup.find("table")
    if not table:
        return []
    texts: list[str] = []
    for cell in table.find_all(["th", "td"]):
        img_alts = []
        for img in cell.find_all("img"):
            alt = img.get("alt", "").strip()
            if alt:
                img_alts.append(alt)
            img.decompose()
        text = cell.get_text(" ", strip=True)
        if img_alts:
            alt_text = " / ".join(img_alts)
            text = f"{text} (이미지: {alt_text})" if text else f"(이미지: {alt_text})"
        if text:
            texts.append(text)
    return texts


def should_skip_table_for_digits(
    table_item: dict, digit_only_ratio_threshold: float
) -> tuple[bool, float, int, int]:
    table_html = table_item.get("table_html") or ""
    cells = iter_table_cell_texts(table_html)
    total_cells = len(cells)
    digit_only_count = sum(1 for c in cells if cell_is_numeric_only(c))
    digit_only_ratio_val = (digit_only_count / total_cells) if total_cells else 0.0
    if total_cells and digit_only_ratio_val >= digit_only_ratio_threshold:
        return True, digit_only_ratio_val, digit_only_count, total_cells
    return False, digit_only_ratio_val, digit_only_count, total_cells


def build_table_payloads(
    str_items: list[dict],
    unstr_items: list[dict],
    digit_only_ratio_threshold: float,
) -> tuple[list[dict], list[dict], list[dict]]:
    str_payloads: list[dict] = []
    skipped_str: list[dict] = []
    for item in str_items:
        skip, digit_only_ratio_val, digit_only_count, total_cells = should_skip_table_for_digits(
            item, digit_only_ratio_threshold
        )
        if skip:
            skipped_str.append(
                {
                    "id": item.get("id"),
                    "filename": item.get("filename"),
                    "section_path": item.get("section_path"),
                    "page": item.get("page"),
                    "image_link": item.get("image_link"),
                    "digit_only_ratio": round(digit_only_ratio_val, 4),
                    "digit_only_cells": digit_only_count,
                    "total_cells": total_cells,
                }
            )
            continue
        str_payloads.append(
            {
                "id": item.get("id"),
                "instruction": TABLE_STR_INSTRUCTIONS,
                "input": {
                    "row_flatten": item.get("row_flatten") or [],
                    "filename": item.get("filename"),
                    "image_link": item.get("image_link"),
                },
                "output": {"table_summary": []},
            }
        )

    unstr_payloads: list[dict] = []
    for item in unstr_items:
        unstr_payloads.append(
            {
                "id": item.get("id"),
                "instruction": TABLE_UNSTR_INSTRUCTIONS,
                "input": {
                    "section_path": item.get("section_path") or "",
                    "filename": item.get("filename"),
                    "image_link": item.get("image_link"),
                },
                "output": {"table_summary": []},
            }
        )
    return str_payloads, unstr_payloads, skipped_str


def build_image_translation_payloads(items: list[dict]) -> list[dict]:
    payloads: list[dict] = []
    for item in items:
        image_link = item.get("image_link") or ""
        payloads.append(
            {
                "id": item.get("id"),
                "instruction": IMAGE_TRANS_INSTRUCTIONS,
                "input": {
                    "description": item.get("description") or "",
                    "image_link": image_link,
                    "section_path": item.get("section_path") or "",
                },
                "output": {"image_summary": "", "image_keyword": []},
            }
        )
    return payloads


def build_image_summary_payloads(items: list[dict]) -> list[dict]:
    payloads: list[dict] = []
    for item in items:
        # image_formula는 제외 (image_sum 파일에는 없어야 하지만 안전 차단)
        if item.get("component_type") == "image_formula":
            continue
        image_link = item.get("image_link") or ""
        cleaned_path = find_cleaned_path_from_image_link(image_link)
        context_before, context_after = get_context_windows(cleaned_path, item.get("id", ""))
        payloads.append(
            {
                "id": item.get("id"),
                "instruction": IMAGE_SUM_INSTRUCTIONS,
                "input": {
                    "image_link": image_link,
                    "context_before": context_before,
                    "context_after": context_after,
                },
                "output": {"image_summary": "", "image_keyword": []},
            }
        )
    return payloads


def write_digit_heavy_log(
    log_path: Path, skipped: list[dict], digit_only_ratio_threshold: float
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as f:
        f.write(
            "# digit-heavy table_str skipped "
            f"(digit_only_ratio>={digit_only_ratio_threshold:.2f})\n"
        )
        for item in skipped:
            f.write(
                f"id={item.get('id')} filename={item.get('filename')} "
                f"section_path={item.get('section_path')} page={item.get('page')} "
                f"image_link={item.get('image_link')} "
                f"digit_only_ratio={item.get('digit_only_ratio')} "
                f"digit_only_cells={item.get('digit_only_cells')} total_cells={item.get('total_cells')}\n"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Create LLM payloads from split components JSONs.")
    parser.add_argument("--extract-dir", type=Path, default=EXTRACT_DIR, help="split components JSON 위치 (기본: output/extract)")
    parser.add_argument("--out-dir", type=Path, default=LLM_DIR, help="LLM payload 출력 위치 (기본: output/llm)")
    parser.add_argument(
        "--cell-digit-only-ratio",
        type=float,
        default=DEFAULT_DIGIT_ONLY_RATIO_THRESHOLD,
        help="테이블 셀 중 숫자만 있는 셀이 임계치 이상이면 table_str payload를 제외 (0~1 비율 또는 0~100 백분율)",
    )
    args = parser.parse_args()

    digit_only_ratio_threshold = normalize_ratio_threshold(args.cell_digit_only_ratio)

    tables_str = load_json(args.extract_dir / "components_tables_str.json")
    tables_unstr = load_json(args.extract_dir / "components_tables_unstr.json")
    images_sum = load_json(args.extract_dir / "components_images_sum.json")
    images_trans = load_json(args.extract_dir / "components_images_trans.json")

    str_payloads, unstr_payloads, skipped_str = build_table_payloads(
        tables_str, tables_unstr, digit_only_ratio_threshold
    )
    trans_payloads = build_image_translation_payloads(images_trans)
    sum_payloads = build_image_summary_payloads(images_sum)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    save_json(args.out_dir / "llm_tables_str_payload.json", str_payloads)
    save_json(args.out_dir / "llm_tables_unstr_payload.json", unstr_payloads)
    save_json(args.out_dir / "llm_images_trans_payload.json", trans_payloads)
    save_json(args.out_dir / "llm_images_sum_payload.json", sum_payloads)

    write_digit_heavy_log(DIGIT_HEAVY_LOG, skipped_str, digit_only_ratio_threshold)

    print(
        f"[INFO] LLM payloads generated: tables_str={len(str_payloads)} "
        f"(skipped={len(skipped_str)} @ digit_only_ratio>={digit_only_ratio_threshold:.2f}), "
        f"tables_unstr={len(unstr_payloads)}, images_trans={len(trans_payloads)}, "
        f"images_sum={len(sum_payloads)} into {args.out_dir}"
    )
    if skipped_str:
        preview = ", ".join(filter(None, [item.get("id") for item in skipped_str[:10]]))
        suffix = "..." if len(skipped_str) > 10 else ""
        print(f"[INFO] skipped table_str ids (digit-heavy cells): {preview}{suffix}")


if __name__ == "__main__":
    main()
