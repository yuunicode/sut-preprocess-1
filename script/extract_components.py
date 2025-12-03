#!/usr/bin/env python3
"""Extract table/image components from placeholders-ready Markdown."""
from __future__ import annotations

import argparse
import json
import re
import html
from collections import defaultdict
from pathlib import Path
from typing import Iterator

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

DEFAULT_SOURCE = Path(__file__).resolve().parents[1] / "output" / "sanitize"
TARGET_SUFFIX = "_rule_sanitized.md"
MD_HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.*)")
PAGE_INFO_RE = re.compile(r"<!--\s*페이지번호:\s*(\d+),\s*파일명:\s*(.*?)\s*-->")
PAGE_TEXT_RE = re.compile(r"페이지번호:\s*(\d+),\s*파일명:\s*(.+)")
COMPLEX_TABLE_LOG = Path(__file__).resolve().parents[1] / "logs" / "complex_tables.log"
PATH_ERROR_LOG = Path(__file__).resolve().parents[1] / "logs" / "path_error.log"


# ---------- 기본 유틸 ----------
def iter_markdown_files(root: Path) -> Iterator[Path]:
    """sanitize 산출물 중 *_rule_sanitized.md만 순회한다."""
    if root.is_file() and root.name.endswith(TARGET_SUFFIX):
        yield root
        return
    for md_path in sorted(root.rglob(f"*{TARGET_SUFFIX}")):
        if md_path.is_file():
            yield md_path


def strip_html_tags(text: str) -> str:
    """간단히 HTML 태그를 제거한 문자열을 반환."""
    return re.sub(r"<[^>]+>", "", text or "").strip()


def update_section_stack(stack: list[str], level: int, title: str) -> None:
    title = strip_html_tags(title or "").strip()
    if not title:
        return
    level = max(1, min(level, 6))
    while len(stack) >= level:
        stack.pop()
    stack.append(title)


def format_section_path(stack: list[str]) -> str:
    return " / ".join(stack)


def parse_page_info(text: str) -> tuple[str | None, str | None]:
    match = PAGE_INFO_RE.search(text)
    if not match:
        match = PAGE_TEXT_RE.search(text)
    if not match:
        return None, None
    return match.group(1), match.group(2)


# ---------- 플레이스홀더 ----------
def make_structured_table_placeholder(counter: int) -> str:
    return f"{{{{TB_STR_{counter:03d}}}}}"


def make_unstructured_table_placeholder(counter: int) -> str:
    return f"{{{{TB_UNSTR_{counter:03d}}}}}"


def make_image_translation_placeholder(counter: int) -> str:
    return f"{{{{IMG_TR_{counter:03d}}}}}"


def make_image_summary_placeholder(counter: int) -> str:
    return f"{{{{IMG_SUM_{counter:03d}}}}}"


def strip_placeholder_braces(value: str) -> str:
    return value.strip("{}")


def replace_chunk(text: str, target: str, placeholder: str, keep_trailing_newline: bool = True) -> tuple[str, bool]:
    """원본 HTML 조각을 플레이스홀더로 치환한다."""
    if not target:
        return text, False
    idx = text.find(target)
    if idx == -1:
        return text, False
    replacement = placeholder
    if keep_trailing_newline and target.endswith("\n"):
        replacement += "\n"
    return text[:idx] + replacement + text[idx + len(target) :], True


def apply_component_placeholders(
    text: str,
    tables: list[dict],
    images_summary: list[dict],
    images_translation: list[dict],
) -> str:
    """테이블/이미지 컴포넌트의 full_html을 플레이스홀더로 치환."""
    result = text

    def replace_with_variants(target: str, placeholder: str, keep_newline: bool = True) -> None:
        nonlocal result
        if not target:
            return
        candidates = [target]
        unescaped = html.unescape(target)
        if unescaped != target:
            candidates.append(unescaped)
        for cand in candidates:
            result, ok = replace_chunk(result, cand, placeholder, keep_trailing_newline=keep_newline)
            if ok:
                break

    for table in tables:
        raw_html = table.get("full_html") or (table.get("table_html", "") + table.get("snapshot_html", ""))
        placeholder = table.get("placeholder") or f"{{{{{table.get('id','')}}}}}"
        replace_with_variants(raw_html, placeholder, keep_newline=True)

    for item in images_summary:
        raw_html = item.get("full_html") or item.get("raw_html") or item.get("block_html", "")
        placeholder = item.get("placeholder") or f"{{{{{item.get('id','')}}}}}"
        replace_with_variants(raw_html, placeholder, keep_newline=True)

    for item in images_translation:
        raw_html = item.get("full_html") or item.get("raw_html") or item.get("img_html", "")
        placeholder = item.get("placeholder") or f"{{{{{item.get('id','')}}}}}"
        replace_with_variants(raw_html, placeholder, keep_newline=False)

    return result


def replace_special_tables(text: str, specials: list[dict]) -> tuple[str, list[dict]]:
    """Replace snapshot-less tables with COMPLEX_TABLE placeholders. Returns (text, logs)."""
    result = text
    logs: list[dict] = []

    def replace_with_variants(target: str, placeholder: str) -> None:
        nonlocal result
        if not target:
            return
        candidates = [target]
        unescaped = html.unescape(target)
        if unescaped != target:
            candidates.append(unescaped)
        for cand in candidates:
            result, ok = replace_chunk(result, cand, placeholder, keep_trailing_newline=True)
            if ok:
                break

    for item in specials:
        placeholder = item.get("placeholder", "{{COMPLEX_TABLE}}")
        raw_html = item.get("full_html", "")
        replace_with_variants(raw_html, placeholder)
        logs.append(
            {
                "placeholder": placeholder.strip("{}"),
                "image_src": item.get("image_src", ""),
                "filename": item.get("filename", ""),
                "page": item.get("page"),
            }
        )

    return result, logs


# ---------- 테이블 ----------
def is_structured_table_html(table_html: str, filename: str | None = None) -> bool:
    """colspan 합이 행마다 동일한지 검사해 구조 붕괴 여부를 판단."""
    if not table_html:
        return True
    soup = BeautifulSoup(table_html, "html.parser")
    table = soup.find("table")
    if not table:
        return True
    # 특정 문서(030-030-100)만 테이블 내 이미지가 있으면 unstructured 처리
    if filename and "030-030-100" in filename.replace(" ", "") and table.find("img"):
        return False
    col_sums: list[int] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        col_sums.append(sum(int(cell.get("colspan", 1)) for cell in cells))
    return len(set(col_sums)) <= 1


def parse_table_general(html: str) -> dict | None:
    """row/colspan을 전개해 2차원 배열로 파싱한다 (table_structured/tb_flatten용)."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return None

    raw_rows = []
    for tr in table.find_all("tr"):
        row = []
        for cell in tr.find_all(["th", "td"]):
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

    max_cols = max(sum(cell["colspan"] for cell in row) for row in raw_rows)
    max_row_reach = max((r + cell["rowspan"]) for r, row in enumerate(raw_rows) for cell in row)
    n_rows = max(len(raw_rows), max_row_reach)
    cells = [[""] * max_cols for _ in range(n_rows)]
    is_header = [[False] * max_cols for _ in range(n_rows)]
    filled = [[False] * max_cols for _ in range(n_rows)]

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

    return {
        "n_rows": n_rows,
        "n_cols": max_cols,
        "cells": cells,
        "is_header": is_header,
    }


def normalize_row_strings(parsed: dict | None) -> list[str]:
    """헤더를 이용해 row를 key-value 문자열로 평탄화한다 (TB_STR row_flatten)."""
    if not parsed:
        return []
    cells = parsed.get("cells") or []
    is_header = parsed.get("is_header") or []
    if not cells:
        return []
    header_rows = [cells[idx] for idx, row in enumerate(is_header) if any(row)]
    row_strings: list[str] = []
    for ridx, row in enumerate(cells):
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


def build_tb_str_unstr_component(
    table_html: str,
    snapshot_html: str,
    image_href: str,
    section_path: str,
    filename: str,
    page: int,
    counters: dict[str, int],
    doc_folder: str,
) -> tuple[dict, bool]:
    """TB_STR/TB_UNSTR 분류와 row_flatten까지 채워 components.json에 넣을 dict를 만든다."""
    structured = is_structured_table_html(table_html, filename=filename)
    parsed_table = parse_table_general(table_html) if structured else None
    row_flatten = normalize_row_strings(parsed_table) if structured else []
    if structured:
        counters["tb_str"] += 1
        placeholder = make_structured_table_placeholder(counters["tb_str"])
        component_type = "table_structured"
    else:
        counters["tb_unstr"] += 1
        placeholder = make_unstructured_table_placeholder(counters["tb_unstr"])
        component_type = "table_unstructured"

    component = {
        "id": strip_placeholder_braces(placeholder),
        "placeholder": placeholder,
        "component_type": component_type,
        "row_flatten": row_flatten,
        "table_html": table_html,
        "snapshot_html": snapshot_html,
        "full_html": table_html + snapshot_html,
        "image_link": str(Path("output") / "sanitize" / doc_folder / (image_href or "")) if image_href else "",
        "section_path": section_path,
        "filename": filename,
        "page": page,
    }
    return component, structured


# ---------- 이미지 ----------
def extract_math_description(html: str) -> str:
    """equation-block snippet의 block math를 정제해 추출한다."""
    soup = BeautifulSoup(html or "", "html.parser")
    for math in soup.find_all("math"):
        math.attrs.pop("display", None)
        math.unwrap()
    text = soup.get_text(" ", strip=True)
    return text


def normalize_description_html(html: str) -> str:
    """이미지 앞 설명을 LLM 친화적으로 정제: <p> 제거, <ul>/<li> 텍스트 정리."""
    soup = BeautifulSoup(html or "", "html.parser")
    for p in soup.find_all("p"):
        p.unwrap()
    for li in soup.find_all("li"):
        li.string = li.get_text(" ", strip=True)
    for ul in soup.find_all(["ul", "ol"]):
        ul.attrs = {}
    return str(soup).strip()


def build_image_formula_component(
    placeholder: str,
    description_html: str,
    full_html: str,
    image_link: str,
    section_path: str,
    filename: str,
    page: int,
    doc_folder: str,
) -> dict:
    return {
        "id": strip_placeholder_braces(placeholder),
        "placeholder": placeholder,
        "component_type": "image_formula",
        "description": extract_math_description(description_html),
        "context_html": "",
        "full_html": full_html,
        "image_link": str(Path("output") / "sanitize" / doc_folder / (image_link or "")) if image_link else "",
        "section_path": section_path,
        "filename": filename,
        "page": page,
    }


def build_image_summary_component(
    placeholder: str,
    description_html: str,
    full_html: str,
    image_link: str,
    section_path: str,
    filename: str,
    page: int,
    doc_folder: str,
) -> dict:
    return {
        "id": strip_placeholder_braces(placeholder),
        "placeholder": placeholder,
        "component_type": "image_summary",
        "description": normalize_description_html(description_html),
        "context_html": "",
        "full_html": full_html,
        "image_link": str(Path("output") / "sanitize" / doc_folder / (image_link or "")) if image_link else "",
        "section_path": section_path,
        "filename": filename,
        "page": page,
    }


def build_image_trans_component(
    placeholder: str,
    alt_text: str,
    full_html: str,
    image_link: str,
    section_path: str,
    filename: str,
    page: int,
    doc_folder: str,
) -> dict:
    return {
        "id": strip_placeholder_braces(placeholder),
        "placeholder": placeholder,
        "component_type": "image_trans",
        "description": alt_text,
        "context_html": "",
        "full_html": full_html,
        "image_link": str(Path("output") / "sanitize" / doc_folder / (image_link or "")) if image_link else "",
        "section_path": section_path,
        "filename": filename,
        "page": page,
    }


def extract_image_blocks(text: str, doc_folder: str) -> tuple[list[dict], list[dict]]:
    """IMG_SUM/IMG_TR 컴포넌트를 추출하고 placeholder 치환 정보를 만든다."""
    parts = text.splitlines(keepends=True)
    summary_components: list[dict] = []
    translation_components: list[dict] = []
    heading_levels: dict[int, str] = {}
    current_page = 1
    img_sum_idx = 0
    img_tr_idx = 0
    context_buffer: list[str] = []
    current_filename = ""
    page_blocks: dict[int, list[str]] = defaultdict(list)
    snippet_context_entries: list[dict] = []
    section_stack: list[str] = []

    for block in parts:
        stripped = block.strip()
        if not stripped:
            context_buffer.append(block)
            continue

        if "페이지번호" in stripped:
            page_val, filename_val = parse_page_info(stripped)
            if page_val:
                try:
                    current_page = int(page_val)
                except ValueError:
                    pass
            if filename_val:
                current_filename = filename_val.strip()
            context_buffer.clear()
            continue

        if stripped.startswith("#"):
            level = stripped.count("#")
            if level <= 3:
                heading_text = stripped.lstrip("#").strip()
                heading_levels[level] = heading_text
                for lvl in list(heading_levels.keys()):
                    if lvl > level:
                        heading_levels.pop(lvl, None)
                update_section_stack(section_stack, level, heading_text)
                context_buffer.clear()
                continue

        soup_block = BeautifulSoup(block, "html.parser")
        heading_tag = soup_block.find(["h1", "h2", "h3"])
        if heading_tag:
            try:
                level = int(heading_tag.name[1])
            except (TypeError, ValueError):
                level = 4
            if level <= 3:
                heading_text = heading_tag.get_text(strip=True)
                heading_levels[level] = heading_text
                for lvl in list(heading_levels.keys()):
                    if lvl > level:
                        heading_levels.pop(lvl, None)
                update_section_stack(section_stack, level, heading_text)
                context_buffer.clear()
                continue

        skip_imgs = []
        for table_tag in soup_block.find_all("table"):
            skip_imgs.extend(table_tag.find_all("img"))
        for skip_img in skip_imgs:
            skip_img.extract()
        imgs = soup_block.find_all("img")
        if not imgs:
            context_buffer.append(block)
            if "<math" not in block.lower():
                page_blocks[current_page].append(block)
            continue

        block_html = block if block.strip() else "".join(context_buffer) + block
        alt_text = imgs[0].get("alt", "") or ""
        alt_lower = alt_text.strip().lower()
        image_src = imgs[0].get("src", "")

        # 수식 블록이 앞줄에 있고 현재 줄에 </math>만 있는 경우, 앞 컨텍스트를 합쳐 블록 전체를 치환
        if alt_lower == "equation-block snippet" and "<math" not in block_html and "</math>" in block_html and context_buffer:
            prev_html = "".join(context_buffer)
            if "<math" in prev_html:
                block_html = prev_html + block_html

        block_without_img = BeautifulSoup(block_html, "html.parser")
        for img in block_without_img.find_all("img"):
            img.decompose()
        description_source_html = str(block_without_img)

        context_idx = len(page_blocks[current_page])

        if alt_lower == "equation-block snippet":
            img_sum_idx += 1
            placeholder = make_image_summary_placeholder(img_sum_idx)
            summary_components.append(
                build_image_formula_component(
                    placeholder=placeholder,
                    description_html=description_source_html,
                    full_html=block_html,
                    image_link=image_src,
                    section_path=format_section_path(section_stack),
                    filename=current_filename,
                    page=current_page,
                    doc_folder=doc_folder,
                )
            )
        elif alt_lower in {"figure snippet", "image snippet", "complex-block snippet"}:
            img_sum_idx += 1
            placeholder = make_image_summary_placeholder(img_sum_idx)
            summary_components.append(
                build_image_summary_component(
                    placeholder=placeholder,
                    description_html=description_source_html,
                    full_html=block_html,
                    image_link=image_src,
                    section_path=format_section_path(section_stack),
                    filename=current_filename,
                    page=current_page,
                    doc_folder=doc_folder,
                )
            )
            snippet_context_entries.append(
                {
                    "target": "summary",
                    "index": len(summary_components) - 1,
                    "page": current_page,
                    "context_index": context_idx,
                }
            )
        else:
            img_tr_idx += 1
            placeholder = make_image_translation_placeholder(img_tr_idx)
            translation_components.append(
                build_image_trans_component(
                    placeholder=placeholder,
                    alt_text=alt_text,
                    full_html=block_html,
                    image_link=image_src,
                    section_path=format_section_path(section_stack),
                    filename=current_filename,
                    page=current_page,
                    doc_folder=doc_folder,
                )
            )

        context_buffer.clear()

    # context_html는 후처리 단계에서 채우도록 기본은 빈 문자열 유지
    for entry in snippet_context_entries:
        if entry["target"] == "summary":
            component = summary_components[entry["index"]]
        else:
            component = translation_components[entry["index"]]
        component["context_html"] = ""

    return summary_components, translation_components


# ---------- 메인 처리 ----------
def process_file(md_path: Path, dest_dir: Path | None = None) -> None:
    text = md_path.read_text(encoding="utf-8")
    soup = BeautifulSoup(f"<root>{text}</root>", "html.parser")
    root = soup.root
    if root is None:
        raise RuntimeError(f"Failed to parse {md_path}")

    headings: dict[int, str] = {}
    current_page = 1
    current_filename = ""
    doc_code = md_path.stem.replace(" ", "")
    try:
        doc_folder = str(md_path.parent.relative_to(DEFAULT_SOURCE))
    except ValueError as exc:  # DEFAULT_SOURCE 밖에서 실행된 경우는 오류 로그만 남기고 중단
        PATH_ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
        with PATH_ERROR_LOG.open("a", encoding="utf-8") as f:
            f.write(f"{md_path}: DEFAULT_SOURCE={DEFAULT_SOURCE} - relative_to failed: {exc}\n")
        raise
    tables: list[dict] = []
    special_tables: list[dict] = []
    section_stack: list[str] = []
    table_counters = {"tb_str": 0, "tb_unstr": 0}

    children = list(root.contents)
    idx = 0
    while idx < len(children):
        node = children[idx]

        if isinstance(node, Comment):
            text_comment = str(node)
            page_val, filename_val = parse_page_info(text_comment)
            if page_val:
                try:
                    current_page = int(page_val)
                except ValueError:
                    pass
            if filename_val:
                current_filename = filename_val.strip()
            idx += 1
            continue

        if isinstance(node, NavigableString):
            text_value = str(node)
            for line in text_value.splitlines():
                match = MD_HEADING_RE.match(line.strip())
                if match:
                    level = len(match.group(1))
                    title = match.group(2)
                    update_section_stack(section_stack, level, title)
            idx += 1
            continue

        if not isinstance(node, Tag):
            idx += 1
            continue

        name = node.name.lower()

        if name.startswith("h") and name[1:].isdigit():
            level = int(name[1])
            update_section_stack(section_stack, level, node.get_text(strip=True))
            idx += 1
            continue

        if name == "table":
            # 인접한 테이블을 모두 묶어 snapshot 전까지 하나의 청크로 처리
            snapshot_node: Tag | None = None
            table_nodes: list[str] = [str(node)]
            j = idx + 1
            while j < len(children):
                nxt = children[j]
                if isinstance(nxt, NavigableString):
                    if nxt.strip():
                        break
                    j += 1
                    continue
                if isinstance(nxt, Tag) and nxt.name == "table":
                    table_nodes.append(str(nxt))
                    j += 1
                    continue
                if isinstance(nxt, Tag) and nxt.name == "p":
                    link = nxt.find("a")
                    if link and "table snapshot" in link.get_text(strip=True).lower():
                        snapshot_node = nxt
                        break
                    j += 1
                    continue
                break
            table_html = "".join(table_nodes)
            if snapshot_node is None:
                img_tag = BeautifulSoup(table_html, "html.parser").find("img")
                # 특수 케이스: snapshot 없이 table에 img가 있고, 파일 코드가 030-030-100인 경우만 COMPLEX_TABLE로 기록/치환
                if img_tag and (
                    (current_filename and "030-030-100" in current_filename.replace(" ", ""))
                    or ("030-030-100" in doc_code)
                ):
                    placeholder = "{{COMPLEX_TABLE}}" if not special_tables else f"{{{{COMPLEX_TABLE_{len(special_tables)+1:03d}}}}}"
                    image_src = img_tag.get("src") if img_tag else ""
                    special_tables.append(
                        {
                            "placeholder": placeholder,
                            "full_html": table_html,
                            "image_src": image_src,
                            "filename": current_filename,
                            "page": current_page,
                        }
                    )
                idx = j
                continue

            snapshot_html = str(snapshot_node)
            image_href = ""
            link_tag = snapshot_node.find("a") if snapshot_node else None
            if link_tag and link_tag.get("href"):
                image_href = link_tag["href"]
            component, _ = build_tb_str_unstr_component(
                table_html=table_html,
                snapshot_html=snapshot_html,
                image_href=image_href,
                section_path=format_section_path(section_stack),
                filename=current_filename,
                page=current_page,
                counters=table_counters,
                doc_folder=doc_folder,
            )
            tables.append(component)
            idx = j + 1
            continue

        idx += 1

    images_summary, images_translation = extract_image_blocks(text, doc_folder=doc_folder)
    text_with_special, special_logs = replace_special_tables(text, special_tables)
    tables = sorted(tables, key=lambda t: 0 if str(t.get("id", "")).startswith("TB_STR") else 1)
    cleaned_text = apply_component_placeholders(text_with_special, tables, images_summary, images_translation)

    target_parent = dest_dir or md_path.parent
    target_parent.mkdir(parents=True, exist_ok=True)
    clean_path = target_parent / f"{md_path.stem.replace('_rule_sanitized', '')}_placeholders{md_path.suffix}"
    clean_path.write_text(cleaned_text, encoding="utf-8")

    components = {
        "source": str(md_path),
        "tables": tables,
        "images_summary": images_summary,
        "images_translation": images_translation,
    }
    (target_parent / "components.json").write_text(json.dumps(components, ensure_ascii=False, indent=2), encoding="utf-8")
    if special_logs:
        COMPLEX_TABLE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with COMPLEX_TABLE_LOG.open("a", encoding="utf-8") as f:
            for entry in special_logs:
                f.write(
                    f"{md_path}:placeholder={entry['placeholder']} page={entry.get('page')} "
                    f"filename={entry.get('filename')} src={entry.get('image_src','')}\n"
                )
    print(f"[INFO] Processed {md_path} -> {clean_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract table/image components from Markdown outputs.")
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_SOURCE,
        help="Directory containing Markdown files (default: output/sanitize).",
    )
    parser.add_argument(
        "--files",
        nargs="*",
        type=Path,
        help="Specific Markdown files to process. When omitted, process every *.md under --root.",
    )
    args = parser.parse_args()

    targets = args.files if args.files else list(iter_markdown_files(args.root))
    if not targets:
        print("[WARN] No Markdown files found.")
        return
    for md_path in targets:
        md_path = md_path.resolve()
        if md_path.is_dir():
            continue
        process_file(md_path)


if __name__ == "__main__":
    main()
