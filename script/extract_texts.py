#!/usr/bin/env python3
"""Clean placeholders.md and extract plain text."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag, Comment

DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "output" / "sanitize"
TARGET_SUFFIX = "_placeholders.md"
OUTPUT_SUFFIX = "_cleaned.md"
SKIP_PATTERNS = [
    r"뒷장\s*계속",
    r"이하\s*여백",
    # 가-힇/영문/숫자에 붙은 '끝'은 살리고, 기호/공백으로만 둘러싸인 경우만 제거
    r"(?<![0-9A-Za-z가-힣])[`\-~*\s]*끝[`\-~*\s]*(?![0-9A-Za-z가-힣])",
]


def iter_target_files(root: Path):
    if root.is_file() and root.name.endswith(TARGET_SUFFIX):
        yield root
        return
    for path in sorted(root.rglob(f"*{TARGET_SUFFIX}")):
        if path.is_file():
            yield path


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def render_list(tag: Tag, output: list[str], indent: int = 0) -> None:
    """Render ul/ol as text with markers. Nested lists are indented."""
    children = [child for child in tag.children if isinstance(child, Tag) and child.name == "li"]
    list_type = (tag.get("type") or "").upper()
    for idx, li in enumerate(children, 1):
        prefix = "  " * indent
        if tag.name == "ol":
            if list_type == "A":
                marker = f"{chr(64 + idx)}. "
            else:
                marker = f"{idx}. "
        else:
            marker = "● "
        line = normalize_whitespace(li.get_text(" ", strip=True))
        if line:
            output.append(prefix + marker + line)
        for sub in li.find_all(["ul", "ol"], recursive=False):
            render_list(sub, output, indent + 1)


def clean_html_to_text(html: str) -> str:
    """Strip basic HTML tags and format lists/line breaks."""
    soup = BeautifulSoup(f"<root>{html}</root>", "html.parser")
    output: list[str] = []

    def traverse(node) -> None:
        if isinstance(node, Comment):
            # 페이지 주석 등은 그대로 보존
            output.append(str(node))
            return
        if isinstance(node, NavigableString):
            text = str(node)
            if text.strip():
                output.append(text.strip())
            return
        if not isinstance(node, Tag):
            return
        name = node.name.lower()
        if name in {"p", "div", "section"}:
            text = normalize_whitespace(node.get_text(" ", strip=True))
            if text:
                output.append(text)
                output.append("")
        elif name == "br":
            output.append("")
        elif name in {"ul", "ol"}:
            render_list(node, output)
            output.append("")
        elif name.startswith("h") and name[1:].isdigit():
            text = normalize_whitespace(node.get_text(" ", strip=True))
            if text:
                output.append(text)
                output.append("")
        elif name == "hr":
            # skip
            return
        else:
            for child in node.children:
                traverse(child)

    for child in soup.root.contents:
        traverse(child)

    text = "\n".join(line.rstrip() for line in output)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def remove_skip_markers(text: str) -> str:
    """Remove '뒷장계속', '이하여백', '끝'(앞뒤에 문자(가-힣/영문/숫자)가 없을 때만 삭제)."""
    lines = []
    pattern = re.compile("|".join(SKIP_PATTERNS), re.IGNORECASE)
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append(line)
            continue
        # 줄 전체가 제거 패턴만 포함하면 스킵
        if pattern.fullmatch(stripped):
            continue
        # 부분만 있을 때는 패턴만 삭제
        cleaned = pattern.sub("", line)
        if cleaned.strip():
            lines.append(cleaned)
    return "\n".join(lines)


def process_file(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    cleaned = clean_html_to_text(content)
    cleaned = remove_skip_markers(cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    out_path = path.with_name(path.name.replace(TARGET_SUFFIX, OUTPUT_SUFFIX))
    out_path.write_text(cleaned + ("\n" if cleaned else ""), encoding="utf-8")
    print(f"[INFO] Wrote {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract plain text from *_placeholders.md.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="sanitize 루트 (기본: output/sanitize)")
    parser.add_argument("--files", nargs="*", type=Path, help="특정 *_placeholders.md만 처리")
    args = parser.parse_args()

    targets = args.files if args.files else list(iter_target_files(args.root))
    if not targets:
        print("[WARN] No *_placeholders.md files found.")
        return
    for path in targets:
        if path.is_dir():
            continue
        process_file(path)


if __name__ == "__main__":
    main()
