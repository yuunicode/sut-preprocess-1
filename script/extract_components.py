#!/usr/bin/env python3
"""Extract table/image components from Markdown and replace them with placeholders."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Iterator, Sequence
import re

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

DEFAULT_SOURCE = Path(__file__).resolve().parents[1] / "output" / "sanitize"
MD_HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.*)")
PARA_SPLIT_RE = re.compile(r"(\n\s*\n)")
PAGE_INFO_RE = re.compile(r"<!--\s*페이지번호:\s*(\d+),\s*파일명:\s*(.*?)\s*-->")
PAGE_TEXT_RE = re.compile(r"페이지번호:\s*(\d+),\s*파일명:\s*(.+)")


def serialize_nodes(nodes):
    return "".join(str(node) for node in nodes)


def iter_markdown_files(root: Path) -> Iterator[Path]:
    for md_path in sorted(root.rglob("*.md")):
        name = md_path.name
        if not name.endswith("_math_heading_sanitized.md"):
            continue
        if name.endswith(("_cleaned.md", "_final.md")):
            continue
        yield md_path


def current_section(headings: dict[int, str]) -> str:
    return headings.get(3) or headings.get(2) or headings.get(1) or ""


def strip_html_tags(text: str) -> str:
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


def section_with_parent(headings: dict[int, str]) -> tuple[str, str]:
    current = ""
    parent = ""
    current_level = 0
    for level in sorted(headings.keys()):
        if level > 3:
            continue
        current_level = level
    if current_level:
        current = headings.get(current_level, "")
        for lvl in range(current_level - 1, 0, -1):
            if lvl in headings:
                parent = headings.get(lvl, "")
                break
    return current, parent


def update_markdown_headings(text: str, headings: dict[int, str]) -> None:
    for line in text.splitlines():
        match = MD_HEADING_RE.match(line)
        if not match:
            continue
        level = len(match.group(1))
        if level > 3:
            continue
        headings[level] = match.group(2).strip()
        for lvl in list(headings.keys()):
            if lvl > level:
                headings.pop(lvl, None)


def create_placeholder(prefix: str, counter: int) -> str:
    return f"{{{{{prefix}_{counter:03d}}}}}"


def replace_chunk(text: str, target: str, placeholder: str, keep_trailing_newline: bool = True) -> tuple[str, bool]:
    if not target:
        return text, False
    idx = text.find(target)
    if idx == -1:
        return text, False
    replacement = placeholder
    if keep_trailing_newline and target.endswith("\n"):
        replacement += "\n"
    return text[:idx] + replacement + text[idx + len(target) :], True


def count_tokens(html: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ", strip=True)
    return len(text.split())


def gather_context(
    blocks: Sequence[str],
    split_index: int,
    tokens_before: int = 100,
    tokens_after: int = 100,
    stop_at_heading: bool = False,
) -> tuple[str, str]:
    before_tokens = 0
    before_chunks: list[str] = []
    i = split_index - 1
    while i >= 0 and before_tokens < tokens_before:
        chunk = blocks[i]
        before_tokens += count_tokens(chunk)
        before_chunks.append(chunk)
        if stop_at_heading and chunk.lstrip().startswith("#"):
            break
        i -= 1
    before_html = "".join(reversed(before_chunks))

    after_tokens = 0
    after_chunks: list[str] = []
    i = split_index
    while i < len(blocks) and after_tokens < tokens_after:
        chunk = blocks[i]
        after_tokens += count_tokens(chunk)
        after_chunks.append(chunk)
        i += 1
    after_html = "".join(after_chunks)
    return before_html, after_html


def extract_image_blocks(text: str) -> tuple[list[dict], list[dict]]:
    parts = text.splitlines(keepends=True)
    summary_components: list[dict] = []
    translation_components: list[dict] = []
    current_section = ""
    current_parent = ""
    current_level = 0
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
                heading_levels[level] = stripped.lstrip("#").strip()
                for lvl in list(heading_levels.keys()):
                    if lvl > level:
                        heading_levels.pop(lvl, None)
                current_level = level
                current_section = heading_levels.get(level, "")
                current_parent = ""
                for parent_level in range(level - 1, 0, -1):
                    if parent_level in heading_levels:
                        current_parent = heading_levels[parent_level]
                        break
                update_section_stack(section_stack, level, current_section)
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
                heading_levels[level] = heading_tag.get_text(strip=True)
                for lvl in list(heading_levels.keys()):
                    if lvl > level:
                        heading_levels.pop(lvl, None)
                current_level = level
                current_section = heading_levels.get(level, "")
                current_parent = ""
                for parent_level in range(level - 1, 0, -1):
                    if parent_level in heading_levels:
                        current_parent = heading_levels[parent_level]
                        break
                update_section_stack(section_stack, level, current_section)
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

        block_without_img = BeautifulSoup(block, "html.parser")
        for img in block_without_img.find_all("img"):
            img.decompose()
        has_text = block_without_img.get_text(strip=True) != ""

        alt_text = imgs[0].get("alt", "") or ""
        alt_lower = alt_text.strip().lower()
        allowed_summary = alt_lower in {
            "figure snippet",
            "image snippet",
            "complex-block snippet",
            "equation-block snippet",
        }
        allowed_image = True

        block_html = block
        if not has_text and context_buffer:
            block_html = "".join(context_buffer) + block

        if alt_lower in {"equation-block snippet", "complex-block snippet"}:
            snippet_context_entries.append(
                {
                    "component_index": len(summary_components),
                    "page": current_page,
                    "context_index": len(page_blocks[current_page]),
                    "mode": alt_lower,
                }
            )

        if (has_text or block_html != stripped) and allowed_summary:
            img_sum_idx += 1
            placeholder = create_placeholder("IMG_SUM", img_sum_idx)
            summary_components.append(
                {
                    "id": placeholder.strip("{}"),
                    "section": current_section,
                    "parent_section": current_parent,
                    "section_path": format_section_path(section_stack),
                    "block_html": block_html,
                    "image": imgs[0].get("src", ""),
                    "alt": alt_text,
                    "page": current_page,
                    "filename": current_filename,
                    "raw_html": block_html,
                }
            )
        elif allowed_image:
            img_tr_idx += 1
            placeholder = create_placeholder("IMG_TR", img_tr_idx)
            translation_components.append(
                {
                    "id": placeholder.strip("{}"),
                    "section": current_section,
                    "parent_section": current_parent,
                    "section_path": format_section_path(section_stack),
                    "alt": alt_text,
                    "image": imgs[0].get("src", ""),
                    "page": current_page,
                    "filename": current_filename,
                    "raw_html": str(imgs[0]),
                }
            )

        context_buffer.clear()

    for entry in snippet_context_entries:
        comp_idx = entry["component_index"]
        page_no = entry["page"]
        ctx_idx = entry["context_index"]
        component = summary_components[comp_idx]
        blocks = page_blocks.get(page_no, [])
        if entry["mode"] == "complex-block snippet":
            before_html, after_html = gather_context(
                blocks,
                ctx_idx,
                tokens_before=100,
                tokens_after=100,
            )
            component["block_html"] = before_html + component["block_html"] + after_html
        else:
            before_html, after_html = gather_context(
                blocks,
                ctx_idx,
                tokens_before=1000,
                tokens_after=100,
                stop_at_heading=True,
            )
            component["block_html"] = before_html + component["block_html"] + after_html

    return summary_components, translation_components


def make_context_window(max_tokens: int = 200):
    window: list[Tuple[str, int]] = []
    total = 0

    def add(fragment: str) -> None:
        nonlocal total
        if not fragment or not fragment.strip():
            return
        tokens = count_tokens(fragment)
        if tokens == 0:
            return
        window.append((fragment, tokens))
        total += tokens
        while total > max_tokens and window:
            _, removed = window.pop(0)
            total -= removed

    def snapshot(tokens: int = 100) -> str:
        acc = 0
        pieces: list[str] = []
        for frag, tok in reversed(window):
            pieces.append(frag)
            acc += tok
            if acc >= tokens:
                break
        return "".join(reversed(pieces))

    def reset() -> None:
        nonlocal total
        window.clear()
        total = 0

    return add, snapshot, reset


def apply_component_placeholders(
    text: str,
    tables: list[dict],
    images_summary: list[dict],
    images_translation: list[dict],
) -> str:
    result = text
    for table in tables:
        raw_html = table.get("full_html") or (
            table.get("table_html", "")
            + table.get("notes_html", "")
            + table.get("snapshot_html", "")
        )
        placeholder = f"{{{{{table['id']}}}}}"
        result, _ = replace_chunk(result, raw_html, placeholder)

    for item in images_summary:
        raw_html = item.get("raw_html") or item.get("block_html", "")
        placeholder = f"{{{{{item['id']}}}}}"
        result, _ = replace_chunk(result, raw_html, placeholder)

    for item in images_translation:
        raw_html = item.get("raw_html") or item.get("img_html", "")
        placeholder = f"{{{{{item['id']}}}}}"
        result, _ = replace_chunk(result, raw_html, placeholder, keep_trailing_newline=False)

    return result


def parse_page_info(text: str) -> tuple[str | None, str | None]:
    match = PAGE_INFO_RE.search(text)
    if not match:
        match = PAGE_TEXT_RE.search(text)
    if not match:
        return None, None
    return match.group(1), match.group(2)


def process_file(md_path: Path, overwrite: bool, dest_dir: Path | None = None) -> None:
    text = md_path.read_text(encoding="utf-8")
    soup = BeautifulSoup(f"<root>{text}</root>", "html.parser")
    root = soup.root
    if root is None:
        raise RuntimeError(f"Failed to parse {md_path}")

    headings: dict[int, str] = {}
    current_page = 1
    current_filename = ""
    tables: list[dict] = []
    add_context_fragment, get_context_fragment, reset_context_fragment = make_context_window()
    section_stack: list[str] = []

    table_idx = 0

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
            reset_context_fragment()
            idx += 1
            continue

        if isinstance(node, NavigableString):
            text_value = str(node)
            update_markdown_headings(text_value, headings)
            add_context_fragment(text_value)
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
            headings[level] = node.get_text(strip=True)
            # remove deeper levels
            for lvl in list(headings.keys()):
                if lvl > level:
                    headings.pop(lvl, None)
            add_context_fragment(str(node))
            update_section_stack(section_stack, level, node.get_text(strip=True))
            idx += 1
            continue

        if name == "table":
            snapshot_node: Tag | None = None
            notes: list[Tag | NavigableString] = []
            j = idx + 1
            while j < len(children):
                nxt = children[j]
                if isinstance(nxt, NavigableString):
                    if nxt.strip():
                        break
                    notes.append(nxt)
                    j += 1
                    continue
                if isinstance(nxt, Tag) and nxt.name == "table":
                    notes.append(nxt)
                    j += 1
                    continue
                if isinstance(nxt, Tag) and nxt.name == "p":
                    link = nxt.find("a")
                    if link and "table snapshot" in link.get_text(strip=True).lower():
                        snapshot_node = nxt
                        break
                    notes.append(nxt)
                    j += 1
                    continue
                break
            if snapshot_node is None:
                idx += 1
                continue

            table_idx += 1
            placeholder = create_placeholder("TABLE", table_idx)
            table_html = str(node)
            snapshot_html = str(snapshot_node)
            image_href = ""
            if snapshot_node:
                link_tag = snapshot_node.find("a")
                if link_tag and link_tag.get("href"):
                    image_href = link_tag["href"]
            notes_html = serialize_nodes(notes) if notes else ""
            context_html = get_context_fragment()
            section_name, parent_section = section_with_parent(headings)
            tables.append(
                {
                    "id": placeholder.strip("{}"),
                    "table_html": table_html,
                    "snapshot_html": snapshot_html,
                    "table_image_path": image_href,
                    "notes_html": notes_html,
                    "context_html": context_html,
                    "section": section_name,
                    "parent_section": parent_section,
                    "section_path": format_section_path(section_stack),
                    "page": current_page,
                    "filename": current_filename,
                    "full_html": table_html + notes_html + snapshot_html,
                }
            )
            idx = j + 1
            continue

        add_context_fragment(str(node))
        idx += 1

    images_summary, images_translation = extract_image_blocks(text)
    cleaned_text = apply_component_placeholders(text, tables, images_summary, images_translation)

    target_parent = dest_dir or md_path.parent
    target_parent.mkdir(parents=True, exist_ok=True)

    clean_path = target_parent / f"{md_path.stem}_cleaned{md_path.suffix}"
    if overwrite:
        clean_path = md_path
    clean_path.write_text(cleaned_text, encoding="utf-8")

    components = {
        "source": str(md_path),
        "tables": tables,
        "images_summary": images_summary,
        "images_translation": images_translation,
    }
    (target_parent / "components.json").write_text(json.dumps(components, ensure_ascii=False, indent=2), encoding="utf-8")

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
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace original Markdown files instead of writing *_clean.md.",
    )
    args = parser.parse_args()

    targets = args.files if args.files else list(iter_markdown_files(args.root))
    if not targets:
        print("[WARN] No Markdown files found.")
        return
    for md_path in targets:
        if md_path.is_dir():
            continue
        process_file(md_path, overwrite=args.overwrite)


if __name__ == "__main__":
    main()
