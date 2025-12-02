#!/usr/bin/env python3
"""Flatten sanitized Markdown and emit both plain text and JSON blocks for RAG."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterable

from bs4 import BeautifulSoup, NavigableString, Tag

DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "output" / "sanitize"
TARGET_SUFFIX = "_math_heading_sanitized_cleaned.md"
TEXT_MD_SUFFIX = "_math_heading_sanitized_cleaned_only_text.md"
TEXT_JSON_SUFFIX = "text_result.json"
SKIP_MARKERS = {"이하여백", "뒷장계속", "끝"}

PAGE_INFO_RE = re.compile(r"<!--\s*페이지번호:\s*(\d+),\s*파일명:\s*(.*?)\s*-->")
HEADING_RE = re.compile(r"^(#{1,6})\s*(.+)$")
PLACEHOLDER_RE = re.compile(r"\{\{[^{}]+\}\}")
PLACEHOLDER_TYPE_RE = re.compile(r"\{\{([^{}]+)\}\}")


def is_skip_marker(text: str) -> bool:
    normalized = re.sub(r"[^0-9A-Za-z가-힣]", "", text or "").strip()
    return normalized in SKIP_MARKERS


def iter_cleaned_files(root: Path) -> Iterable[Path]:
    if root.is_file() and root.name.endswith(TARGET_SUFFIX):
        yield root
        return
    for path in sorted(root.rglob(f"*{TARGET_SUFFIX}")):
        if path.is_file():
            yield path


def update_section_stack(stack: list[str], level: int, title: str) -> None:
    if not stack:
        adjusted = 1
    else:
        adjusted = min(level, len(stack) + 1)
    while len(stack) >= adjusted:
        stack.pop()
    stack.append(title.strip())


def format_section_path(stack: list[str]) -> str:
    parts = [part for part in stack if part]
    return " / ".join(parts) if parts else "본문"


def inline_text(tag: Tag) -> str:
    parts: list[str] = []
    for child in tag.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif isinstance(child, Tag) and child.name not in {"ul", "ol"}:
            parts.append(inline_text(child))
    text = " ".join(part.strip() for part in parts if part and part.strip())
    return re.sub(r"\s+", " ", text).strip()


def render_list(tag: Tag, output: list[str], indent: int = 0) -> None:
    children = [child for child in tag.children if isinstance(child, Tag) and child.name == "li"]
    for idx, li in enumerate(children, 1):
        prefix = "  " * indent
        marker = f"{idx}. " if tag.name == "ol" else "- "
        text = inline_text(li)
        line = prefix + marker + text if text else prefix + marker.strip()
        normalized_line = line.strip()
        if normalized_line and not is_skip_marker(normalized_line):
            output.append(normalized_line)
        # render nested lists inside li
        for sub in li.find_all(["ul", "ol"], recursive=False):
            render_list(sub, output, indent + 1)


def process_html_block(html: str) -> list[str]:
    soup = BeautifulSoup(f"<root>{html}</root>", "html.parser")
    output: list[str] = []

    def traverse(node) -> None:
        if isinstance(node, NavigableString):
            text = str(node)
            stripped = text.strip()
            if stripped and not is_skip_marker(stripped):
                output.append(stripped)
            return
        if not isinstance(node, Tag):
            return
        name = node.name.lower()
        if name in {"p", "div"}:
            text = inline_text(node)
            if text and not is_skip_marker(text):
                output.append(text)
                output.append("")
        elif name in {"ul", "ol"}:
            render_list(node, output)
            output.append("")
        elif name == "br":
            output.append("")
        else:
            for child in node.children:
                traverse(child)

    for child in soup.root.contents:
        traverse(child)
    return output


def clean_text(content: str) -> str:
    block = process_html_block(content)
    cleaned = "\n".join(line.rstrip() for line in block)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def collect_text_entries(content: str, components_dir: Path | None = None) -> list[dict]:
    section_stack: list[str] = []
    entries: list[dict] = []
    block: list[str] = []
    section_lines: list[str] = []
    current_page: int | None = None
    current_filename: str | None = None
    section_page: int | None = None
    section_filename: str | None = None
    counter = 0
    linked_tables: set[str] = set()
    linked_images: set[str] = set()

    def ensure_section_meta() -> None:
        nonlocal section_page, section_filename
        if not section_lines:
            section_page = current_page
            section_filename = current_filename

    def flush_block_into_section() -> None:
        if not block:
            return
        fragment = "\n".join(block).strip()
        block.clear()
        if not fragment:
            return
        text = clean_text(fragment)
        if not text:
            return
        ensure_section_meta()
        section_lines.append(text)

    def append_blank() -> None:
        if section_lines and section_lines[-1] != "":
            section_lines.append("")

    def append_placeholder(text: str) -> None:
        """플레이스홀더를 섹션 텍스트에 포함시킨다."""
        match = PLACEHOLDER_TYPE_RE.match(text.strip())
        label = match.group(1) if match else text.strip()
        desc = None
        if label.startswith("TABLE"):
            desc = "표"
        elif label.startswith("IMG_SUM") or label.startswith("IMG_TR"):
            desc = "그림"
        ensure_section_meta()
        section_lines.append(f"[{desc}:{{{{{label}}}}}]")

    def flush_section() -> None:
        nonlocal counter, section_page, section_filename
        flush_block_into_section()
        if not section_lines:
            section_page = current_page
            section_filename = current_filename
            return
        text_body = "\n".join(section_lines).strip()
        text_body = re.sub(r"\n{3,}", "\n\n", text_body)
        if text_body:
            counter += 1
            section_label = format_section_path(section_stack)
            file_label = section_filename or current_filename or ""
            text_body = f"[파일: {file_label}] [섹션: {section_label}] {text_body}"
            entries.append(
                {
                    "id": f"TEXT_{counter:03d}",
                    "section_path": section_label,
                    "page": section_page,
                    "filename": section_filename,
                    "linked_tables": sorted(linked_tables),
                    "linked_images": sorted(linked_images),
                    "components_dir": str(components_dir) if components_dir else "",
                    "text": text_body,
                }
            )
        section_lines.clear()
        section_page = current_page
        section_filename = current_filename
        linked_tables.clear()
        linked_images.clear()

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            flush_block_into_section()
            append_blank()
            continue
        page_match = PAGE_INFO_RE.match(stripped)
        if page_match:
            flush_block_into_section()
            current_page = int(page_match.group(1))
            current_filename = page_match.group(2)
            continue
        heading_match = HEADING_RE.match(stripped)
        if heading_match:
            flush_section()
            level = len(heading_match.group(1))
            title = heading_match.group(2)
            update_section_stack(section_stack, level, title)
            continue
        if PLACEHOLDER_RE.fullmatch(stripped):
            flush_block_into_section()
            append_placeholder(stripped)
            label = stripped.strip("{}")
            if label.startswith("TABLE"):
                linked_tables.add(label)
            elif label.startswith("IMG_SUM") or label.startswith("IMG_TR"):
                linked_images.add(label)
            continue
        block.append(line)

    flush_section()
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(description="Sanitized Markdown을 평문과 JSON(RAG용)으로 변환합니다.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="sanitize 디렉터리 (기본: output/sanitize)")
    parser.add_argument("--files", nargs="*", type=Path, help="특정 *_math_heading_sanitized_cleaned.md 파일만 처리")
    args = parser.parse_args()

    targets = args.files if args.files else list(iter_cleaned_files(args.root))
    if not targets:
        print("[WARN] No *_math_heading_sanitized_cleaned.md files found.")
        return

    for path in targets:
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8")
        entries = collect_text_entries(content, components_dir=Path("components"))

        text_blocks = [entry["text"] for entry in entries if entry.get("text")]
        text_output = "\n\n".join(text_blocks).strip()
        if text_output:
            text_output += "\n"

        md_path = path.with_name(path.name.replace(TARGET_SUFFIX, TEXT_MD_SUFFIX))
        json_path = path.parent / TEXT_JSON_SUFFIX
        md_path.write_text(text_output, encoding="utf-8")
        json_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[INFO] Wrote {md_path} and {json_path}")


if __name__ == "__main__":
    main()
