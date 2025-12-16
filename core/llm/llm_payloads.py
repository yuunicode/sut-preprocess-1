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
TABLE_CONTEXT_WINDOW = 20
DEFAULT_DIGIT_ONLY_RATIO_THRESHOLD = 0.3
DIGIT_HEAVY_LOG = REPO_ROOT / "logs" / "digit_heavy_tables.log"

TABLE_STR_INSTRUCTIONS = """
HARD RULES:
- Output MUST be Korean only.
- NEVER output Chinese, Japanese, or any other language.
- Do NOT insert meaningless spaces between Korean characters.
  (예: 통기 성 ❌ → 통기성 ✅)

- Preserve all technical terms, abbreviations, symbols, and units EXACTLY.
- Do NOT translate or explain technical terms.
- dead man, hanging, S.L, CAG, BF, RDI, CRI, CSR remain in English.
- cokes → 코크스 or cokes.

- If unclear or unreadable, keep the original English token.
- Do NOT guess or invent values.

You summarize structured blast furnace operation tables.

TABLE RULES:
- BF1, BF2, BF3, BF4 mean 1고로, 2고로, 3고로, 4고로.
- BF1(R1) → 1고로(R1).
- Describe actions and criteria per furnace separately.
- Do NOT merge or generalize actions across furnaces.

WRITING:
- Focus on operation criteria, thresholds, trends, and actions.
- Preserve all numbers and units.
- Write 4–8 Korean sentences.

OUTPUT:
{
  "table_summary": [
    "문장1",
    "문장2"
  ]
}
"""

TABLE_UNSTR_INSTRUCTIONS = """
HARD RULES:
- Output MUST be Korean only.
- NEVER output Chinese, Japanese, or any other language.
- Do NOT insert meaningless spaces between Korean characters.
  (예: 통기 성 ❌ → 통기성 ✅)

- Preserve all technical terms, abbreviations, symbols, and units EXACTLY.
- Do NOT translate or explain technical terms.
- dead man, hanging, S.L, CAG, BF, RDI, CRI, CSR remain in English.
- cokes → 코크스 or cokes.

- If unclear or unreadable, keep the original English token.
- Do NOT guess or invent values.

You summarize unstructured blast furnace table images.

TABLE IMAGE RULES:
- Use only values visible in the image.
- BF1, BF2, BF3, BF4 → 1고로, 2고로, 3고로, 4고로.
- Describe furnace-specific actions separately.
- Do NOT invent missing data.

WRITING:
- Explain numeric values, units, comparisons, and conditions.
- Write 3–6 Korean sentences.

OUTPUT:
{
  "table_summary": [
    "문장1",
    "문장2"
  ]
}
"""

IMAGE_TRANS_INSTRUCTIONS = """
HARD RULES:
- Output MUST be Korean only.
- NEVER output Chinese, Japanese, or any other language.
- Do NOT insert meaningless spaces between Korean characters.
  (예: 통기 성 ❌ → 통기성 ✅)

- Preserve all technical terms, abbreviations, symbols, and units EXACTLY.
- Do NOT translate or explain technical terms.
- dead man, hanging, S.L, CAG, BF, RDI, CRI, CSR remain in English.
- cokes → 코크스 or cokes.

- If unclear or unreadable, keep the original English token.
- Do NOT guess or invent values.

You rewrite blast furnace technical descriptions into Korean.

TRANS RULES:
- Korean output only.
- If input is already Korean, output unchanged.
- Preserve ALL technical terms, abbreviations, symbols, and units EXACTLY.
- Do NOT translate dead man, hanging, S.L, CAG, BF, RDI, CRI, CSR.
- Do NOT insert or remove spaces inside technical tokens.
- Write ONE paragraph only.

OUTPUT:
{
  "image_summary": "문장"
}
"""

IMAGE_SUM_INSTRUCTIONS = """
HARD RULES:
- Output MUST be Korean only.
- NEVER output Chinese, Japanese, or any other language.
- Do NOT insert meaningless spaces between Korean characters.
  (예: 통기 성 ❌ → 통기성 ✅)

- Preserve all technical terms, abbreviations, symbols, and units EXACTLY.
- Do NOT translate or explain technical terms.
- dead man, hanging, S.L, CAG, BF, RDI, CRI, CSR remain in English.
- cokes → 코크스 or cokes.

- If unclear or unreadable, keep the original English token.
- Do NOT guess or invent values.

You summarize blast furnace operation visuals.

IMAGE RULES:
- Start with one of: "이 그래프는", "이 차트는", "이 그림은".
- Describe axes, units, trends, comparisons, or process flow.
- Preserve all units exactly.
- Korean only.

WRITING:
- Write 4–5 Korean sentences.

OUTPUT:
{
  "image_summary": [
    "문장1",
    "문장2"
  ],
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

    def should_skip_for_terms(table_item: dict) -> bool:
        targets = ("품질영향인자", "공정영향인자")
        candidates: list[str] = []
        row_flatten = table_item.get("row_flatten")
        if isinstance(row_flatten, list):
            candidates.extend([c for c in row_flatten if isinstance(c, str)])
        elif isinstance(row_flatten, str):
            candidates.append(row_flatten)
        # 비정형 테이블 대비: table_html/full_html 문자열에서도 검색
        for key in ("table_html", "full_html"):
            val = table_item.get(key)
            if isinstance(val, str):
                candidates.append(val)
        for text in candidates:
            compact = re.sub(r"\s+", "", text)
            if any(t in compact for t in targets):
                return True
        return False

    for item in str_items:
        cleaned_path = find_cleaned_path_from_image_link(item.get("image_link") or "")
        context_before, context_after = get_context_windows(
            cleaned_path, item.get("id", ""), window=TABLE_CONTEXT_WINDOW
        )
        skip, digit_only_ratio_val, digit_only_count, total_cells = should_skip_table_for_digits(
            item, digit_only_ratio_threshold
        )
        term_skip = should_skip_for_terms(item)
        if skip or term_skip:
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
                    "skip_reason": "protected_term" if term_skip else "digit_ratio",
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
                    "context_before": context_before,
                    "context_after": context_after,
                },
                "output": {"table_summary": []},
            }
        )

    unstr_payloads: list[dict] = []
    for item in unstr_items:
        cleaned_path = find_cleaned_path_from_image_link(item.get("image_link") or "")
        context_before, context_after = get_context_windows(
            cleaned_path, item.get("id", ""), window=TABLE_CONTEXT_WINDOW
        )
        if should_skip_for_terms(item):
            continue
        unstr_payloads.append(
            {
                "id": item.get("id"),
                "instruction": TABLE_UNSTR_INSTRUCTIONS,
                "input": {
                    "section_path": item.get("section_path") or "",
                    "filename": item.get("filename"),
                    "image_link": item.get("image_link"),
                    "context_before": context_before,
                    "context_after": context_after,
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
                "output": {"image_summary": ""},
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
                "output": {"image_summary": []},
            }
        )
    return payloads


def write_digit_heavy_log(
    log_path: Path, skipped: list[dict], digit_only_ratio_threshold: float
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as f:
        f.write(
            "# table_str skipped (digit-heavy or protected terms)\n"
            f"# digit_only_ratio_threshold={digit_only_ratio_threshold:.2f}\n"
        )
        for item in skipped:
            f.write(
                f"id={item.get('id')} filename={item.get('filename')} "
                f"section_path={item.get('section_path')} page={item.get('page')} "
                f"image_link={item.get('image_link')} "
                f"digit_only_ratio={item.get('digit_only_ratio')} "
                f"digit_only_cells={item.get('digit_only_cells')} total_cells={item.get('total_cells')} "
                f"reason={item.get('skip_reason')}\n"
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
        f"(skipped={len(skipped_str)}; digit_ratio>={digit_only_ratio_threshold:.2f} or protected terms), "
        f"tables_unstr={len(unstr_payloads)}, images_trans={len(trans_payloads)}, "
        f"images_sum={len(sum_payloads)} into {args.out_dir}"
    )
    if skipped_str:
        preview = ", ".join(filter(None, [item.get("id") for item in skipped_str[:10]]))
        suffix = "..." if len(skipped_str) > 10 else ""
        print(f"[INFO] skipped table_str ids (digit-heavy/protected terms): {preview}{suffix}")


if __name__ == "__main__":
    main()
