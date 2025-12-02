#!/usr/bin/env python3
"""Convert <math>...</math> segments in Markdown to plain text via rule-based parsing."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import datetime
from typing import Iterable, Set

from check_math_tags import analyze_math_tags, find_markdown_files

DEFAULT_TARGET = Path(__file__).resolve().parents[1] / "output" / "chandra"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "output" / "sanitize"
LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
MATH_SCAN_LOG = LOG_DIR / "math_scan.log"
RULE_LOG = LOG_DIR / "rule_math.log"

MATH_BLOCK_RE = re.compile(r"<math\b[^>]*>(.*?)</math>", re.IGNORECASE | re.DOTALL)
TEXT_COMMAND_RE = re.compile(r"(?<![A-Za-z])(?:\\)?(text|ext|mathrm|operatorname)\s*\{([^{}]*)\}")
BRACE_RE = re.compile(r"[{}]")
HEADING_TAG_RE = re.compile(r"<h([1-6])>(.*?)</h\1>", re.IGNORECASE | re.DOTALL)
MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
NUMBERED_HEADING_RE = re.compile(r"^(?P<label>\d+(?:\.\d+)*)(?:\.)?\s+(?P<title>.+)$")
HANGUL_SUBSECTION_RE = re.compile(r"^[가-힣]\.")
TOP_LEVEL_TITLES = [
    "1. 적용범위",
    "2. 목적",
    "3. 중점관리 항목",
    "4. 조업기준",
    "5. 이상판단 및 조치기준",
    "6. 기술이론",
]


def normalize_title_key(title: str) -> str:
    return re.sub(r"\s+", "", title or "")


TOP_LEVEL_TITLES_NORMALIZED = {normalize_title_key(t): t for t in TOP_LEVEL_TITLES}
COMMAND_MAP: dict[str, str] = {
    r"\times": "x",
    r"\imes": "x",
    r"\cdot": "·",
    r"\pm": "±",
    r"\degree": "°",
    r"\deg": "°",
    r"\sim": "~",
    r"\circ": "°",
    r"\alpha": "alpha",
    r"\beta": "beta",
    r"\gamma": "gamma",
    r"\delta": "δ",
    r"\Delta": "Δ",
    r"\phi": "Ø",
    r"\eta": "η",
    r"\rightarrow": "→",
    r"\ge": "≥",
    r"\gt": ">",
    r"\sum": "Σ",
}
PAGE_BREAK_COMMENT = "<!-- 페이지번호: {page}, 파일명: {filename} -->"
MAX_MAJOR_LEVEL = 6
MAX_SUB_LEVEL = 30


def parse_numeric_label(label: str) -> list[int] | None:
    parts = label.split(".")
    values: list[int] = []
    for idx, part in enumerate(parts):
        if not part.isdigit():
            return None
        value = int(part)
        if idx == 0:
            if not (1 <= value <= MAX_MAJOR_LEVEL):
                return None
        else:
            if not (0 <= value <= MAX_SUB_LEVEL):
                return None
        values.append(value)
    return values


def numeric_major_key(label: str) -> str | None:
    parts = parse_numeric_label(label)
    if not parts:
        return None
    return str(parts[0])


def strip_html_tags(text: str) -> str:
    """HTML 태그를 제거하고 앞뒤 공백을 정리한다."""
    return re.sub(r"<[^>]+>", "", text or "").strip()


def replace_textcircled(text: str) -> str:
    pattern = re.compile(r"\\textcircled\s*\{([^{}]+)\}")
    return pattern.sub(lambda m: f"(circled)[{m.group(1)}]", text)


def replace_text_commands(text: str) -> str:
    while True:
        new_text = TEXT_COMMAND_RE.sub(lambda m: m.group(2), text)
        if new_text == text:
            return new_text
        text = new_text


def _extract_braced_expr(text: str, start: int) -> tuple[str | None, int]:
    """{ ... } 구간을 추출하고, 내부 문자열과 다음 인덱스를 반환한다."""
    if start >= len(text) or text[start] != "{":
        return None, start
    depth = 0
    idx = start
    while idx < len(text):
        ch = text[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : idx], idx + 1
        idx += 1
    return text[start + 1 : idx], idx


def replace_frac(text: str) -> str:
    result: list[str] = []
    idx = 0
    changed = False
    while idx < len(text):
        if text.startswith(r"\frac", idx):
            num, next_idx = _extract_braced_expr(text, idx + 5)
            den, final_idx = _extract_braced_expr(text, next_idx)
            if num is not None and den is not None:
                numerator = num.strip()
                denominator = den.strip()
                result.append(f"[({numerator})/({denominator})]")
                idx = final_idx
                changed = True
                continue
        result.append(text[idx])
        idx += 1
    if not changed:
        return text
    return "".join(result)


def replace_commands(text: str) -> str:
    for cmd, replacement in COMMAND_MAP.items():
        text = text.replace(cmd, replacement)
    return text


def flatten_sub_sup(text: str) -> str:
    text = re.sub(r"_\{([^{}]+)\}", lambda m: m.group(1), text)
    text = re.sub(r"_([0-9a-zA-Z]+)", lambda m: m.group(1), text)
    text = re.sub(r"\^\{([^{}]+)\}", lambda m: f"^{m.group(1)}", text)
    text = re.sub(r"\^([0-9a-zA-Z]+)", lambda m: f"^{m.group(1)}", text)
    text = text.replace("^°", "°")
    return text


def cleanup_math_content(raw: str) -> str:
    text = raw.strip()
    text = replace_text_commands(text)
    text = replace_frac(text)
    text = replace_commands(text)
    text = replace_textcircled(text)
    text = flatten_sub_sup(text)
    text = BRACE_RE.sub("", text)
    text = re.sub(r"\\[a-zA-Z]+", "", text)
    text = text.replace("\\", "")
    text = re.sub(r"\s+", " ", text)
    text = text.replace("^°C", "°C")
    return text.strip()


SUB_TAG_RE = re.compile(r"<sub>(.*?)</sub>", re.IGNORECASE | re.DOTALL)
SUP_TAG_RE = re.compile(r"<sup>(.*?)</sup>", re.IGNORECASE | re.DOTALL)


def replace_html_sub_sup(content: str) -> str:
    content = SUB_TAG_RE.sub(lambda m: m.group(1), content)
    content = SUP_TAG_RE.sub(lambda m: f"^{m.group(1)}", content)
    return content


def extract_page_sections(content: str) -> list[tuple[str, str]]:
    """<h2>Page N</h2> 블록을 찾아 페이지 번호와 내용을 반환한다."""
    sections: list[tuple[str, str]] = []
    pattern = re.compile(r"(<h2>Page\s+(\d+)</h2>)(.*?)(?=(<h2>Page\s+\d+</h2>)|$)", re.DOTALL)
    pos = 0
    while True:
        match = pattern.search(content, pos)
        if not match:
            break
        page_num = match.group(2) or ""
        body = match.group(3).strip()
        sections.append((page_num, body))
        pos = match.end()
    return sections


def extract_display_filename(filename: str) -> str:
    """
    파일명에서 TP-XXX-XXX-XXX 이후, (Rev.) 앞까지의 제목을 추출한다.
    접두어는 제외하고, \"(Rev\" 직전에서 잘라낸다.
    """
    stem = Path(filename).stem
    match = re.search(r"TP-\d{3}-\d{3}-\d{3}\s+(.+?)(?=\(Rev\.)", stem)
    if match:
        return match.group(1).strip()
    rev_idx = stem.find("(Rev")
    if rev_idx != -1:
        title_part = stem[:rev_idx].strip()
        parts = title_part.split(maxsplit=1)
        if len(parts) == 2:
            return parts[1].strip()
        return title_part
    return stem


def insert_page_breaks(content: str, filename: str) -> str:
    """페이지 단위로 주석(<!-- 페이지번호 ... -->)을 삽입한다."""
    sections = extract_page_sections(content)
    if not sections:
        sections = [("1", content.strip())]
    display_name = extract_display_filename(filename)
    if not display_name:
        display_name = filename
    fragments: list[str] = []
    for idx, (page, body) in enumerate(sections, start=1):
        page_label = page or str(idx)
        comment = PAGE_BREAK_COMMENT.format(page=page_label, filename=display_name)
        body_text = body.strip()
        if body_text:
            fragments.append(f"{comment}\n{body_text}")
        else:
            fragments.append(comment)
    return "\n\n".join(fragments)


def convert_headings(content: str) -> tuple[str, int]:
    """<h1>~<h6> 태그를 Markdown 헤딩(#) 형태로 바꾼다."""
    count = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        level = int(match.group(1))
        inner = strip_html_tags(match.group(2))
        count += 1
        hashes = "#" * level
        return f"{hashes} {inner}\n"

    content = HEADING_TAG_RE.sub(repl, content)

    def replace_paragraph_heading(match: re.Match[str]) -> str:
        inner = strip_html_tags(match.group(1)).strip()
        numbered = NUMBERED_HEADING_RE.match(inner)
        if not numbered:
            return match.group(0)
        if not parse_numeric_label(numbered.group("label")):
            return match.group(0)
        label = numbered.group("label")
        dot_count = label.count(".")
        if dot_count == 0:
            prefix = "#"
        elif dot_count == 1:
            prefix = "##"
        else:
            prefix = "###"
        return f"{prefix} {inner}\n"

    content = re.sub(r"<p>\s*(.+?)\s*</p>", replace_paragraph_heading, content)
    count += len(re.findall(r"<p>\s*(\d+(?:\.\d+)+)\s+.+?</p>", content))

    content, plain_count = convert_plain_numeric_lines(content)
    count += plain_count

    return content, count


def convert_plain_numeric_lines(content: str) -> tuple[str, int]:
    """HTML 태그 없이 남은 숫자형 문장을 헤딩으로 승격한다."""
    lines = content.splitlines()
    current_major: str | None = None
    converted = 0

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        heading_match = MARKDOWN_HEADING_RE.match(stripped)
        if heading_match:
            title_raw = strip_html_tags(heading_match.group(2))
            numbered = NUMBERED_HEADING_RE.match(title_raw)
            current_major = numeric_major_key(numbered.group("label")) if numbered else None
            continue

        if "<" in stripped and ">" in stripped:
            continue

        numbered = NUMBERED_HEADING_RE.match(stripped)
        if not numbered:
            continue

        label = numbered.group("label")
        numeric_parts = parse_numeric_label(label)
        if not numeric_parts:
            continue
        title = numbered.group("title")
        dot_count = label.count(".")
        major = str(numeric_parts[0])

        if dot_count == 0:
            prefix = "#"
            current_major = major
        else:
            if not current_major or current_major != major:
                current_major = major
            prefix = "##" if dot_count == 1 else "###"

        lines[idx] = f"{prefix} {label} {title}"
        converted += 1

    return "\n".join(lines), converted


def edit_headings(content: str) -> tuple[str, list[dict]]:
    """특정 제목 규칙에 따라 헤딩 레벨을 재조정한다."""
    lines = content.splitlines()
    headings: list[dict] = []
    last_numeric_level: int | None = None

    for idx, line in enumerate(lines):
        match = MARKDOWN_HEADING_RE.match(line.strip())
        if not match:
            continue

        hashes = match.group(1)
        title_raw = strip_html_tags(match.group(2))
        new_hashes = hashes

        normalized_title = normalize_title_key(title_raw)

        if normalized_title in TOP_LEVEL_TITLES_NORMALIZED:
            new_hashes = "#"
            last_numeric_level = len(new_hashes)
        else:
            numbered = NUMBERED_HEADING_RE.match(title_raw)
            parts = parse_numeric_label(numbered.group("label")) if numbered else None
            if parts:
                dot_count = len(parts) - 1
                if dot_count == 1:
                    new_hashes = "##"
                elif dot_count >= 2:
                    new_hashes = "###"
                last_numeric_level = len(new_hashes)
            elif HANGUL_SUBSECTION_RE.match(title_raw) and last_numeric_level:
                next_level = min(last_numeric_level + 1, 6)
                new_hashes = "#" * next_level
                last_numeric_level = len(new_hashes)
            else:
                last_numeric_level = None

        normalized_line = f"{new_hashes} {title_raw}"
        lines[idx] = normalized_line
        headings.append({"level": len(new_hashes), "title": title_raw})

    return "\n".join(lines), headings


PAGE_BREAK_COMMENT = "<!-- 페이지번호: {page}, 파일명: {filename} -->"


def extract_page_sections(content: str) -> list[tuple[str, str]]:
    """<h2>Page N</h2> 블록을 쪼개어 페이지별 내용과 번호를 반환한다."""
    sections: list[tuple[str, str]] = []
    pattern = re.compile(r"(<h2>Page\s+(\d+)</h2>)(.*?)(?=(<h2>Page\s+\d+</h2>)|$)", re.DOTALL)
    pos = 0
    while True:
        match = pattern.search(content, pos)
        if not match:
            break
        page_num = match.group(2)
        body = match.group(3).strip()
        sections.append((page_num, body))
        pos = match.end()
    if not sections:
        sections.append(("1", content.strip()))
    return sections


def insert_page_breaks(content: str, filename: str) -> str:
    """페이지 division 사이에 주석을 삽입한다."""
    sections = extract_page_sections(content)
    display_name = extract_display_filename(filename)
    fragments = []
    for page, body in sections:
        comment = PAGE_BREAK_COMMENT.format(page=page, filename=display_name)
        fragments.append(f"{comment}\n{body}")
    return "\n\n".join(fragments)


def transform_content(content: str, filename: str) -> tuple[str, int, list[dict], list[dict]]:
    count = 0
    replacements_detail: list[dict] = []

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        original = match.group(0)
        converted = cleanup_math_content(match.group(1))
        replacements_detail.append({"original": original, "converted": converted})
        return converted

    new_content, _ = MATH_BLOCK_RE.subn(repl, content)
    new_content = replace_html_sub_sup(new_content)
    new_content = insert_page_breaks(new_content, filename)
    new_content, _ = convert_headings(new_content)
    new_content, headings = edit_headings(new_content)
    return new_content, count, replacements_detail, headings


def _sanitized_dest(path: Path, target_dir: Path, out_dir: Path) -> Path:
    rel = path.relative_to(target_dir)
    sanitized_name = f"{rel.stem}_math_heading_sanitized{rel.suffix}"
    return out_dir / rel.parent / sanitized_name


def process_file(
    path: Path, target_dir: Path, out_dir: Path, dry_run: bool, overwrite: bool
) -> tuple[int, bool, Path, list[dict], list[dict]]:
    text = path.read_text(encoding="utf-8")
    new_text, replacements, detail, headings = transform_content(text, path.name)
    dest_path = path if overwrite else _sanitized_dest(path, target_dir, out_dir)
    changed = text != new_text
    if changed and not dry_run:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_text(new_text, encoding="utf-8")
    return replacements, changed, dest_path, detail, headings


def iter_markdown_files(target_dir: Path):
    return sorted(p for p in target_dir.rglob("*.md") if p.is_file())


def log_results(log_path: Path, entries: list[dict]) -> None:
    if not entries:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    import datetime as dt

    timestamp = dt.datetime.now().isoformat(timespec="seconds")
    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(f"[{timestamp}] rule_math_cleanup\n")
        json_lines = "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries)
        log_file.write(json_lines + "\n\n")


def write_heading_log(dest_path: Path, headings: list[dict]) -> None:
    """각 Markdown 결과 폴더에 헤딩 로그를 남긴다."""
    log_path = dest_path.parent / "headings.log"
    log_path.write_text(json.dumps(headings, ensure_ascii=False, indent=2), encoding="utf-8")


def append_text_log(path: Path, lines: Iterable[str]) -> None:
    lines = list(lines)
    if not lines:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with path.open("a", encoding="utf-8") as log_file:
        log_file.write(f"[{timestamp}]\n")
        for line in lines:
            log_file.write(line + "\n")
        log_file.write("\n")


def run(
    target_dir: Path,
    out_dir: Path,
    dry_run: bool,
    overwrite: bool,
    log_path: Path | None,
    skip_paths: Set[Path] | None = None,
) -> None:
    log_messages: list[str] = []
    if not target_dir.exists():
        log_messages.append(f"[WARN] Target directory {target_dir} does not exist.")
        append_text_log(RULE_LOG, log_messages)
        return
    if not overwrite:
        out_dir.mkdir(parents=True, exist_ok=True)

    total_files = 0
    changed_files = 0
    total_replacements = 0
    log_entries: list[dict] = []
    skip_set = {p.resolve() for p in skip_paths} if skip_paths else set()

    for md_path in iter_markdown_files(target_dir):
        resolved = md_path.resolve()
        if resolved in skip_set:
            log_messages.append(f"[SKIP] {md_path} (unbalanced math tags)")
            continue
        total_files += 1
        replacements, changed, dest_path, detail, headings = process_file(
            md_path, target_dir, out_dir, dry_run, overwrite
        )
        total_replacements += replacements
        if changed:
            changed_files += 1
            status = "DRY-RUN" if dry_run else ("OVERWRITE" if overwrite else "WRITE")
            rel_src = md_path.relative_to(target_dir)
            if overwrite:
                target_info = f"{rel_src}"
            else:
                dest_rel = dest_path.relative_to(out_dir)
                target_info = f"{rel_src} -> {dest_rel}"
            log_messages.append(f"[{status}] {target_info} ({replacements} replacements)")
            if not dry_run:
                write_heading_log(dest_path, headings)
            if log_path:
                log_entries.append(
                    {
                        "source": str(md_path),
                        "destination": str(dest_path),
                        "replacements": replacements,
                        "status": status,
                        "changes": detail,
                        "headings": headings,
                    }
                )

    log_messages.append(
        f"[SUMMARY] files={total_files} changed={changed_files} replacements={total_replacements} dry_run={dry_run} overwrite={overwrite}"
    )
    if log_path:
        log_results(log_path, log_entries)
    append_text_log(RULE_LOG, log_messages)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rule-based cleanup of <math> tags in Markdown outputs.")
    parser.add_argument(
        "--target",
        type=Path,
        default=DEFAULT_TARGET,
        help="Directory containing Markdown outputs (default: output/chandra).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Directory to write cleaned copies (default: output/sanitize). Ignored when --overwrite is set.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Modify original Markdown files instead of writing to --output-dir.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report changes without modifying files.",
    )
    parser.add_argument(
        "--log-path",
        type=Path,
        help="Optional path to append JSONL logs for changed files (default: <output-dir>/math_cleanup.log or target when overwriting).",
    )
    parser.add_argument(
        "--include-unbalanced",
        action="store_true",
        help="Process files even if math tags are unbalanced (default: skip them).",
    )
    return parser.parse_args()


def run_math_scan(target: Path) -> list[Path]:
    messages: list[str] = []
    markdown_files = find_markdown_files(target)
    if not markdown_files:
        messages.append(f"No Markdown files found under {target}")
        append_text_log(MATH_SCAN_LOG, messages)
        return []

    unbalanced: list[Path] = []
    for md_path in markdown_files:
        report = analyze_math_tags(md_path.read_text(encoding="utf-8"))
        rel = md_path.relative_to(target)
        if report["balanced"]:
            messages.append(f"OK: {rel}")
        else:
            desc = "; ".join(report["issues"]) if report["issues"] else "unmatched math tags"
            messages.append(f"UNBALANCED: {rel} -> {desc}")
            unbalanced.append(md_path)

    append_text_log(MATH_SCAN_LOG, messages)
    return unbalanced


if __name__ == "__main__":
    args = parse_args()
    output_dir = args.output_dir if not args.overwrite else args.target
    if args.log_path:
        log_path = args.log_path
    else:
        log_path = LOG_DIR / "math_cleanup.log"

    unbalanced_files = run_math_scan(args.target)
    skip_set: Set[Path] | None = None
    if unbalanced_files and not args.include_unbalanced:
        skip_set = {path.resolve() for path in unbalanced_files}
        append_text_log(
            RULE_LOG,
            ["Skipping files due to unbalanced math tags:"] + [str(p) for p in unbalanced_files],
        )

    run(args.target, output_dir, args.dry_run, args.overwrite, log_path, skip_set)
