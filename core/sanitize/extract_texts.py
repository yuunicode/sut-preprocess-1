#!/usr/bin/env python3
"""Clean placeholders.md and extract plain text."""
from __future__ import annotations

import argparse
import re
import json
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag, Comment

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = REPO_ROOT / "output" / "sanitize"
TARGET_SUFFIX = "_placeholders.md"
OUTPUT_SUFFIX = "_cleaned.md"
SKIP_PATTERNS = [
    r"뒷장\s*계속",
    r"이하\s*여백",
    # 가-힇/영문/숫자에 붙은 '끝'은 살리고, 기호/공백으로만 둘러싸인 경우만 제거
    r"(?<![0-9A-Za-z가-힣])[`\-~*\s]*끝[`\-~*\s]*(?![0-9A-Za-z가-힣])",
]
PLACEHOLDER_RE = re.compile(r"\{\{([^{}]+)\}\}")


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
            # 페이지 주석 등은 그대로 보존 (구분을 위해 공백 줄 추가)
            comment_text = str(node).strip()
            if comment_text:
                output.append(f"<!-- {comment_text} -->")
                output.append("")
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
    for line in text.splitlines():
        normalized = re.sub(r"[^0-9A-Za-z가-힣]", "", line)
        if normalized in {"뒷장계속", "이하여백", "끝"}:
            continue
        lines.append(line)
    return "\n".join(lines)


def remove_sections_with_phrase(text: str, phrase: str = "해당사항 없음") -> str:
    """
    헤딩(line starts with #) 내용에 phrase(공백 무시)가 포함되면
    해당 헤딩과 다음 헤딩 전까지의 본문을 제거한다.
    페이지 주석은 스킵 구간에서도 보존한다.
    """
    target = re.sub(r"\s+", "", phrase)
    heading_re = re.compile(r"^(#{1,6})\s+(.*)")
    page_comment_re = re.compile(r"<!--\s*페이지번호:.*-->")

    lines = text.splitlines()
    out: list[str] = []
    skip = False
    for line in lines:
        stripped = line.strip()
        hmatch = heading_re.match(stripped)
        if hmatch:
            title = re.sub(r"\s+", "", hmatch.group(2))
            if target and target in title:
                skip = True
                continue
            skip = False
            out.append(line)
            continue
        if skip:
            if page_comment_re.match(stripped):
                out.append(line)
            continue
        out.append(line)
    return "\n".join(out)


# ---------- 텍스트 컴포넌트 생성 ----------
def build_component_map(comp_data: dict) -> dict:
    """placeholder id -> image/table url 매핑 생성."""
    mapping = {}
    for table in comp_data.get("tables", []):
        tid = table.get("id")
        url = table.get("image_link") or table.get("table_image_path") or ""
        if tid:
            mapping[tid] = url
    for img in comp_data.get("images_summary", []):
        iid = img.get("id")
        url = img.get("image_link") or img.get("image") or ""
        if iid:
            mapping[iid] = url
    for img in comp_data.get("images_translation", []):
        iid = img.get("id")
        url = img.get("image_link") or img.get("image") or ""
        if iid and iid not in mapping:
            mapping[iid] = url
    return mapping


def chunk_by_numeric_heading(text: str) -> list[dict]:
    """숫자 헤딩(예: 4., 4.5, 6.7.2) 등장 시점을 경계로 청크를 나눈다."""
    chunks: list[dict] = []
    current: list[str] = []
    section_stack: list[str] = []
    current_page = None
    current_filename = None

    def push_chunk():
        if not current:
            return
        body = "\n".join(current).strip()
        if not body:
            current.clear()
            return
        chunks.append(
            {
                "section_path": " / ".join(section_stack) if section_stack else "",
                "page": current_page,
                "filename": current_filename,
                "text": body,
            }
        )
        current.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("<!-- 페이지번호:"):
            push_chunk()
            m = re.search(r"페이지번호:\s*(\d+),\s*파일명:\s*(.*?)\s*-->", stripped)
            if m:
                current_page = int(m.group(1))
                current_filename = m.group(2)
            continue
        hmatch = re.match(r"^(#{1,6})\s+(.*)", stripped)
        if hmatch:
            level = len(hmatch.group(1))
            title = hmatch.group(2).strip()
            # 숫자 헤딩(예: 1., 4.5, 6.7.2)일 때만 새 청크 시작
            if re.match(r"^\d+(?:\.\d+)*", title):
                push_chunk()
            while len(section_stack) >= level:
                section_stack.pop()
            section_stack.append(title)
            current.append(line)
            continue
        current.append(line)
    push_chunk()
    return chunks


def build_text_component(chunk: dict, idx: int, component_map: dict) -> dict:
    placeholders = {}
    for m in PLACEHOLDER_RE.finditer(chunk.get("text", "")):
        pid = m.group(1)
        placeholders[pid] = component_map.get(pid, "")
    return {
        "id": f"TEXT_{idx:03d}",
        "component_type": "text",
        "text": chunk.get("text", ""),
        "placeholders": placeholders,
        "section_path": chunk.get("section_path") or "",
        "filename": chunk.get("filename"),
        "page": chunk.get("page"),
    }


def append_text_components(clean_path: Path) -> None:
    comp_path = clean_path.parent / "components.json"
    if not comp_path.exists():
        comp_data = {}
    else:
        comp_data = json.loads(comp_path.read_text(encoding="utf-8"))

    component_map = build_component_map(comp_data)
    text = clean_path.read_text(encoding="utf-8")
    chunks = chunk_by_numeric_heading(text)
    texts: list[dict] = []
    for idx, chunk in enumerate(chunks, 1):
        texts.append(build_text_component(chunk, idx, component_map=component_map))

    comp_data["texts"] = texts
    comp_path.write_text(json.dumps(comp_data, ensure_ascii=False, indent=2), encoding="utf-8")


def process_file(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    cleaned = clean_html_to_text(content)
    cleaned = remove_skip_markers(cleaned)
    cleaned = remove_sections_with_phrase(cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    out_path = path.with_name(path.name.replace(TARGET_SUFFIX, OUTPUT_SUFFIX))
    out_path.write_text(cleaned + ("\n" if cleaned else ""), encoding="utf-8")
    append_text_components(out_path)
    print(f"[INFO] Wrote {out_path} and updated components.json")


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
