#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path
from typing import Tuple

# 헤딩/경로 설정
DEFAULT_TARGET = Path("output/chandra")
DEFAULT_OUTPUT = Path("output/sanitize")
LOG_DIR = Path("logs")
HEADING_LOG = LOG_DIR / "headings.log"
PAGE_BREAK_COMMENT = "<!-- 페이지번호: {page}, 파일명: {filename} -->"
NUMBERED_HEADING_RE = re.compile(r"^(?P<label>\d+(?:\.\d+)*)(?:\.)?\s+(?P<title>.+)$")
TOP_LEVEL_TITLES = [
    "1. 적용범위",
    "2. 목적",
    "3. 중점관리 항목",
    "4. 조업기준",
    "5. 이상판단 및 조치기준",
    "6. 기술이론",
]
TOP_LEVEL_TITLES_NORM = [re.sub(r"\s+", "", t) for t in TOP_LEVEL_TITLES]
# 점(.)/공백이 빠진 변형도 허용
TOP_LEVEL_TITLES_DOTLESS = [re.sub(r"[.\s]+", "", t) for t in TOP_LEVEL_TITLES]
# 특수 오타/변형 허용:
# - "3.증정 관리항목" (030-070-010 장입물 분포제어 오타)
# - "4.조업관리 기준" (030-090-080 추가)
# - "3. 종점관리 항목" (030-100-010 문제)
# - "5이상판단및조치기준" (점/공백 누락)
TOP_LEVEL_TITLES_ALIASES = ["3증정관리항목", "4조업관리기준", "3종점관리항목", "5이상판단및조치기준"]
# 비교용: 점/공백 제거한 모든 후보 집합
TOP_LEVEL_COMPACT = set(TOP_LEVEL_TITLES_DOTLESS + TOP_LEVEL_TITLES_ALIASES)
HANGUL_SUBSECTION_RE = re.compile(r"^[가-힣]\.")
MAX_MAJOR_LEVEL = 6
MAX_SUB_LEVEL = 30
PAGE_BREAK_COMMENT = "<!-- 페이지번호: {page}, 파일명: {filename} -->"

# \frac{a}{b} 변환용
FRAC_RE = re.compile(r"\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}")
SQRT_RE = re.compile(r"\\sqrt\s*\{([^{}]+)\}")
FALLBACK_FRAC_RE = re.compile(r"\\frac\s*\{([^}]*)\}\s*\{([^}]*)\}", re.DOTALL)
UNBALANCED_FRAC_RE = re.compile(r"\\frac\s*\{([^{}]*?)\}\s*\{([^}]*)", re.DOTALL)


def _extract_braced(text: str, start: int) -> tuple[str | None, int]:
    """텍스트에서 start 위치의 { ... }를 파싱해 내용과 다음 인덱스를 반환한다."""
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
    return None, start


def convert_fracs(text: str) -> tuple[str, int]:
    """중첩된 \\frac{...}{...}을 [()/()] 형태로 변환한다."""
    out: list[str] = []
    idx = 0
    replaced = 0
    while idx < len(text):
        if text.startswith(r"\frac", idx):
            num, next_idx = _extract_braced(text, idx + 5)
            if num is None:
                out.append(text[idx])
                idx += 1
                continue
            den, final_idx = _extract_braced(text, next_idx)
            num_conv, num_repl = convert_fracs(num)
            if den is None:
                den_raw = text[next_idx:].lstrip("{").rstrip()
                den_conv, den_repl = convert_fracs(den_raw)
                out.append(f"[({num_conv})/({den_conv})]")
                replaced += 1 + num_repl + den_repl
                idx = len(text)
            else:
                den_conv, den_repl = convert_fracs(den)
                out.append(f"[({num_conv})/({den_conv})]")
                replaced += 1 + num_repl + den_repl
                idx = final_idx
        else:
            out.append(text[idx])
            idx += 1
    return "".join(out), replaced


def convert_sqrt(text: str) -> tuple[str, int]:
    """중첩된 \\sqrt{...}을 √(...) 형태로 변환한다."""
    out: list[str] = []
    idx = 0
    replaced = 0
    while idx < len(text):
        if text.startswith(r"\sqrt", idx):
            inner, next_idx = _extract_braced(text, idx + 5)
            if inner is None:
                out.append(text[idx])
                idx += 1
                continue
            inner_conv, inner_repl = convert_sqrt(inner)
            out.append(f"√({inner_conv})")
            replaced += 1 + inner_repl
            idx = next_idx
        else:
            out.append(text[idx])
            idx += 1
    return "".join(out), replaced


def apply_minor_fixes(text: str) -> str:
    """자잘한 문자열 보정 전용 함수."""
    # \gammaCO 오탈자 → ηCO
    text = text.replace(r"\gammaCO", "ηCO")
    return text


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

# 키-값 매핑(LaTeX 표현 -> 유니코드/플레인텍스트)
# 1차: 섭씨/그리스/수학 기호
PRIMARY_MAP = [
    
    # 섭씨
    (r"\s*\^\s*\{?\s*\\circ\s*\}?\s*(?:\\text\{C\}|\\mathrm\{C\}|C)", "°C"),
    (r"\s*\^\s*\{?\s*\\circ\s*\}?", "°"),
    
    # 그리스 기호
    (r"\\ell", "ℓ"),
    (r"\\Delta", "Δ"),
    (r"\\delta", "δ"),
    (r"\\mu", "μ"),
    (r"\\eta", "η"),
    (r"\\Phi", "Φ"),
    (r"\\alpha", "α"),
    (r"\\beta", "β"),
    (r"\\pi", "π"),
    (r"\\lambda", "λ"),
    (r"\\sigma", "Σ"), # sigma로 읽히지만 썸이었다.
    (r"\\varepsilon", "ϵ"),
    (r"\\rho", "ρ"),
    
    # 수학 기호
    (r"\\uparrow", "↑"),
    (r"\\downarrow", "↓"),
    (r"\\leftrightarrow", "↔"),
    (r"\\rightarrow", "→"),
    (r"rightarrow", "→"),
    (r"\\nightarrow", "→"),  # markdown 파싱 오류 대응
    (r"nightarrow", "→"),  # markdown 파싱 오류 대응
    (r"(?<![A-Za-z])ightarrow(?![A-Za-z])", "→"),  # 파싱 중 앞글자 누락 대응
    (r"\\to", "→"),
    (r"\\pm", "±"),
    (r"\\%", "%"),
    (r"\\times", "×"),
    (r"times", "×"),
    (r"\\text\\{\\textbraceleft\\}", "{"),
    (r"\\text\\{\\textbraceright\\}", "}"),
    (r"\\cdot", "·"),
    (r"\\dots", "…"),
    (r"\\le(?!ft)", "≤"),  # \left 보호
    (r"\\ge(?!ft)", "≥"),  # \left 보호
    (r"\\sim", "~"),  
    (r"&Gt;", ">"),  
    (r"\\circ", "◯"),
    (r"\\triangle", "△"),
    (r"\\square", "□"),
    (r"\\div", "÷"),
    (r"\\approx", "≒"),
    (r"\\arrow", "→"),
    (r"(?<![A-Za-z])arrow(?![A-Za-z])", "→"),
    (r"\\quad", "     "),
]

# 2차: 단위
SECONDARY_MAP = [
    
    # 단위
    (r"\s*\\text\{m\}\^\s*3", "m³"),
    (r"\s*\\?text\s*\{\s*\}\s*", ""),  # 빈 text{} 제거
    (r"\s*m\^\s*3", "m³"),  # m^3 형태 대응
    (r"\s*\\?text\s*\{\s*m\s*\}\s*\^\s*3", "m³"),
    (r"\s*\\?text\s*\{\s*T/D/m\s*\}\s*\^\s*3", "T/D/m³"),
    (r"\s*\\?text\s*\{\s*m\s*\}\s*\^\s*2", "m²"),
    (r"\s*\\?text\s*\{\s*cm\s*\}\s*\^\s*2", "cm²"),
    (r"\s*\\?text\s*\{\s*kg/cm\s*\}\s*\^\s*2", "kg/cm²"),
    (r"\s*\\?text\s*\{\s*kg/cm\s*\}", "kg/cm"),
    (r"\s*\\?text\s*\{\s*Kg/cm\s*\}", "kg/cm"),
    (r"\s*\\?text\s*\{\s*g/Nm\s*\}\s*\^\s*3", "g/Nm³"),
    (r"\s*\\?text\s*\{\s*g/cm\s*\}\s*\^\s*2", "g/cm²"),
    (r"\s*g/cm\^\s*2", "g/cm²"),
    (r"\s*\\?text\s*\{\s*Kcal/m\s*\}\s*\^\s*2", "kcal/m²"),
    (r"\s*\\?text\s*\{\s*Kcal/m\s*\}", "kcal/m"),
    (r"\s*\\?text\s*\{\s*Gcal\s*\}", "Gcal"),
    (r"\s*\\?text\s*\{\s*Nm\s*\}\s*\^\s*3", "Nm³"),
    (r"\s*\\?text\s*\{\s*N\s*m\s*\}\s*\^\s*3", "Nm³"),
    (r"\s*\\?text\s*\{\s*Nm\s*\}\s*\^\s*2", "Nm²"),
    (r"\s*\\?text\s*\{\s*N\s*m\s*\}\s*\^\s*2", "Nm²"),
    (r"\s*\\mu\\text\{m\}", "μm"),
    (r"\s*\\text\{kg\}/\\text\{t-p\}", "kg/t-p"),
    (r"\s*\\?text\s*\{\s*mm\s*\}", "mm"),
    (r"\s*\\?text\s*\{\s*KW\s*\}", "KW"),
    (r"\s*\\?text\s*\{\s*m/s\s*\}", "m/s"),
    (r"\s*\\?text\s*\{\s*m\s*\}", "m"),
    (r"\s*\\?text\s*\{\s*kg\s*\}", "kg"),
    (r"\s*\\?text\s*\{\s*kg/t-p\s*\}", "kg/t-p"),
    (r"\s*\\?text\s*\{\s*kg/T-P\s*\}", "kg/T-P"),
    (r"\s*\\?text\s*\{\s*g/1000\s*\}", "g/1000"),
    (r"\s*\\?text\s*\{\s*C/Hr\s*\}", "C/Hr"),
    (r"\s*\\?text\s*\{\s*m/sec\s*\}", "m/sec"),
    (r"\s*\\?text\s*\{\s*sec\s*\}", "sec"),
    (r"\s*\\?text\s*\{\s*min\s*\}", "min"),
    (r"\s*\\?text\s*\{\s*min/day\s*\}", "min/day"),
    (r"\s*\\?text\s*\{\s*h\s*\}", "h"),
    (r"\s*\\?text\s*\{\s*Hr\s*\}", "Hr"),
    (r"\s*\\?text\s*\{\s*kg/t\s*\}", "kg/t"),
    (r"\s*\\?text\s*\{\s*T/D\s*\}", "T/D"),
    (r"\s*\\?text\s*\{\s*cal/mol\s*\}", "cal/mol"),
    (r"\s*\\?text\s*\{\s*kcal/g\s*\}", "kcal/g"),
    (r"\s*\\?text\s*\{\s*mol\s*\}", "mol"),
    (r"\s*\\?text\s*\{\s*min/Hr\s*\}", "min/Hr"),
    (r"\s*\\?text\s*\{\s*Ton\s*\}", "Ton"),
    (r"\s*\\?text\s*\{\s*ton\s*\}", "ton"),
    (r"\s*\\?text\s*\{\s*cm\s*\}", "cm"),
    (r"\s*\\?text\s*\{\s*sec\s*\}", "sec"),
    (r"\s*\\?text\s*\{\s*T-P\s*\}", "T-P"),
    (r"\s*\\?text\s*\{\s*Ton-Slag\s*\}", "Ton-Slag"),
    (r"\s*\\?text\s*\{\s*N\s*m\s*\}", "Nm"),
    (r"\s*\\?text\s*\{\s*mmAq\s*\}", "mmAq"),
    (r"\s*\\?text\s*\{\s*Max\s*\}", "Max"),
    (r"\s*\\?text\s*\{\s*ton-pig\s*\}", "ton-pig"),
    (r"\s*\\?text\s*\{\s*kcal\s*\}", "kcal"),
    (r"\s*\\?text\s*\{\s*g\s*\}", "g"),
    (r"\s*\\mathrm\{\s*cm\s*\}\s*\^\s*\{?\s*2\s*\}?", "cm²"),
    (r"\s*\\mathrm\{\s*Kg\s*\}", "kg"),
    (r"\s*\\mathrm\{\s*mm\s*\}", "mm"),
    (r"\s*\\mathrm\{\s*O\s*\}", "O"),
    (r"\s*\\mathrm\{\s*cm\s*\}", "cm"),
]

INLINE_MATH_RE = re.compile(r"(<math(?![^>]*display=\"block\")[^>]*>)(.*?)(</math>)", re.IGNORECASE | re.DOTALL)
BLOCK_MATH_RE = re.compile(r"(<math[^>]*display=\"block\"[^>]*>)(.*?)(</math>)", re.IGNORECASE | re.DOTALL)
INLINE_OR_BLOCK_RE = re.compile(r"(<math[^>]*>)(.*?)(</math>)", re.IGNORECASE | re.DOTALL)

DEFAULT_TARGET = Path("output/chandra")
DEFAULT_OUTPUT = Path("output/sanitize")


def normalize_html_subsup(text: str) -> str:
    """모든 영역에서 <sub>…</sub>, <sup>…</sup>를 숫자 아래/윗첨자로 변환한다."""
    sub_map = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
    sup_map = str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹")

    def sub_repl(match: re.Match[str]) -> str:
        inner = match.group(1).strip()
        return inner.translate(sub_map)

    def sup_repl(match: re.Match[str]) -> str:
        inner = match.group(1).strip()
        return inner.translate(sup_map)

    text = re.sub(r"<sub>(.*?)</sub>", sub_repl, text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<sup>(.*?)</sup>", sup_repl, text, flags=re.IGNORECASE | re.DOTALL)
    return text


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


def extract_display_filename(filename: str) -> str:
    """
    파일명에서 TP-XXX-XXX-XXX 이후, (Rev.) 앞까지의 제목을 추출한다.
    접두어는 제외하고, "(Rev" 직전에서 잘라낸다.
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
    """페이지 division 사이에 주석을 삽입한다."""
    sections = extract_page_sections(content)
    display_name = extract_display_filename(filename)
    fragments = []
    for page, body in sections:
        comment = PAGE_BREAK_COMMENT.format(page=page, filename=display_name)
        body_text = body.strip()
        if body_text:
            fragments.append(f"{comment}\n{body_text}")
        else:
            fragments.append(comment)
    return "\n\n".join(fragments)

def decode_basic_entities(text: str) -> str:
    """단순 HTML 엔티티(&lt;, &gt;, &amp;)를 실제 기호로 변환한다."""
    return (
        text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
    )


def preprocess_special_math(text: str) -> str:
    """
    파일명이 '조업공정식 해설' 계열일 때 실행:
    1) block math 내부의 인라인 <math>...</math> 제거
    2) block math 외부 인라인 <math>...</math> 제거
    3) block math가 숫자형(예: 4.3.11H₂, 4.3.9 대기습분...)으로 시작하면 '=' 앞까지를 제목으로 추출해
       해당 블록 위에 숫자형에 맞는 레벨(#/##/###)의 헤딩을 삽입
    4) 본문에 숫자형이 포함된 <p>...</p> 또는 맨 앞 숫자형 라인은 원문을 유지한 채,
       동일한 레벨의 헤딩을 해당 라인 바로 위에 추가
    5) block math가 '>= ' 또는 '=' 로 시작하면 직전 헤딩 라인(마지막으로 삽입된 헤딩)을 복제해 블록 위에 한 번 더 삽입
    """
    def make_heading(label: str, title: str) -> str:
        parts = parse_numeric_label(label)
        if not parts:
            return ""
        level = 1 if len(parts) == 1 else 2 if len(parts) == 2 else 3
        heading_title = f"{label} {title}".strip()
        return f"{'#' * level} {heading_title}"

    def remove_inline_math(content: str) -> str:
        return re.sub(r"<math(?![^>]*display=\"block\")[^>]*>(.*?)</math>", r"\1", content, flags=re.IGNORECASE | re.DOTALL)

    last_heading_line = ""

    def block_repl(match: re.Match[str]) -> str:
        prefix, body, suffix = match.groups()
        cleaned_body = remove_inline_math(body)
        heading = ""
        start = cleaned_body.lstrip()
        num_match = re.match(r"(\d+(?:\.\d+){1,2})([^=\n]*)", start)
        if num_match:
            label = num_match.group(1)
            title_part = num_match.group(2)
            title = title_part.split("=", 1)[0].strip()
            heading_line = make_heading(label, title)
            if heading_line:
                heading = f"{heading_line}\n"
                nonlocal last_heading_line
                last_heading_line = heading_line
        # '>=', '=' 시작 시 헤딩 복제 로직은 제거 (요청에 따라 비활성화)
        return f"{heading}{prefix}{cleaned_body}{suffix}"

    # 1) block math 내부 인라인 제거 + 헤딩 삽입
    text = BLOCK_MATH_RE.sub(block_repl, text)
    # 2) block math 외부 인라인 제거
    text = remove_inline_math(text)
    # src="..." 내부는 보존하기 위해 마스킹 (아래첨자 변환 영향 제거)
    src_placeholders: list[str] = []
    def mask_src(match: re.Match[str]) -> str:
        src_placeholders.append(match.group(0))
        return f"__SRCPLACEHOLDER{len(src_placeholders)-1}__"
    text = re.sub(r'src="[^"]*"', mask_src, text, flags=re.IGNORECASE)
    # 4) 특수 기호/윗·아랫첨자 보정 (조업공정식 해설 전용)
    sup_map = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
    sub_map = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")

    def sup_repl(match: re.Match[str]) -> str:
        digit = match.group(1)
        return digit.translate(sup_map)

    def sub_repl(match: re.Match[str]) -> str:
        digit = match.group(1)
        return digit.translate(sub_map)

    text = re.sub(r"\^\s*\{?\s*([0-9])(?![0-9.])\s*\}?", sup_repl, text)
    text = re.sub(r"_\s*\{?\s*([0-9])(?![0-9.])\s*\}?", sub_repl, text)
    text = text.replace(r"\mu", "μ")
    text = text.replace(r"\varepsilon", "ϵ")
    text = text.replace(r"\beta", "β")
    text = text.replace(r"\Delta", "Δ")
    text = text.replace(r"\rightarrow", "→")
    text = text.replace(r"rightarrow", "→")
    text = text.replace(r"\approx", "≒")
    text = re.sub(r"\s*\^\s*\{?\s*\\circ\s*\}?", "°", text)
    # \underline 제거 및 간단 기호 치환
    text = re.sub(r"\\underline\s*\{\s*(.*?)\s*\}", lambda m: m.group(1).strip(), text, flags=re.DOTALL)
    text = text.replace(r"\cdot", "·")
    text = text.replace(r"\eta", "η")
    text = text.replace(r"\%", "%")
    # \text{...} 내용만 남기기 (트림)
    text = re.sub(r"\\text\s*\{\s*(.*?)\s*\}", lambda m: m.group(1).strip(), text)
    # 깨진 \frac{...{...} 형태 보정 후 변환
    text = re.sub(r"\\frac\s*\{\s*([^{}]+)\s*\{\s*([^{}]+)\}\s*\}", r"\\frac{\1}{\2}", text)
    text, _ = convert_fracs(text)
    # 마스킹 복원
    def restore_src(match: re.Match[str]) -> str:
        idx = int(match.group(1))
        return src_placeholders[idx] if 0 <= idx < len(src_placeholders) else match.group(0)
    text = re.sub(r"__SRCPLACEHOLDER(\d+)__", restore_src, text)
    return text

def normalize_math_text(text: str) -> Tuple[str, int]:
    """1차(섭씨/그리스/수학) 후 2차(화학식/단위) 순서로 변환한다."""
    total = 0

    # 숫자 아래첨자/윗첨자 유니코드 변환 (_2 -> ₂, ^-2 -> ⁻²) - 한 자리 또는 음수 한 자리만 대상
    sub_map = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
    sup_map = str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹")

    def html_sub_repl(match: re.Match[str]) -> str:
        nonlocal total
        inner = match.group(1).strip()
        total += 1
        return inner.translate(sub_map)

    def html_sup_repl(match: re.Match[str]) -> str:
        nonlocal total
        inner = match.group(1).strip()
        total += 1
        return inner.translate(sup_map)

    def sub_digit_repl(match: re.Match[str]) -> str:
        nonlocal total
        digit = match.group(1)
        total += 1
        return digit.translate(sub_map)

    def sup_digit_repl(match: re.Match[str]) -> str:
        nonlocal total
        digit = match.group(1)
        total += 1
        return digit.translate(sup_map)

    def sup_brace_repl(match: re.Match[str]) -> str:
        nonlocal total
        content = match.group(1).replace(" ", "")
        if not re.fullmatch(r"-?\d+", content):
            return match.group(0)
        total += 1
        return content.translate(sup_map)

    def sup_paren_repl(match: re.Match[str]) -> str:
        nonlocal total
        content = match.group(1).replace(" ", "")
        if not re.fullmatch(r"-?\d+", content):
            return match.group(0)
        total += 1
        return content.translate(sup_map)
    
    text = re.sub(r"_(\d)", sub_digit_repl, text)
    text = re.sub(r"\^\s*\{\s*(-?\d+)\s*\}", sup_brace_repl, text)
    text = re.sub(r"\^\s*\(\s*(-?\d+)\s*\)", sup_paren_repl, text)
    text = re.sub(r"\^(-?\d)", sup_digit_repl, text)
    text = re.sub(r"<sub>(.*?)</sub>", html_sub_repl, text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<sup>(.*?)</sup>", html_sup_repl, text, flags=re.IGNORECASE | re.DOTALL)

    def sum_paren_repl(match: re.Match[str]) -> str:
        nonlocal total
        inner = match.group(1).strip()
        total += 1
        return f"Σ({inner})" if inner else "Σ"

    def sum_word_repl(match: re.Match[str]) -> str:
        nonlocal total
        total += 1
        return "Σ"

    text = re.sub(r"\\sum\s*\(\s*([^\)]*)\s*\)", sum_paren_repl, text)
    text = re.sub(r"\\sum\b", sum_word_repl, text)

    # \frac{...}{...} → [(...)/(...) ] (중첩 포함)
    text, frac_repl_count = convert_fracs(text)
    total += frac_repl_count


    while True:
        new_text, n = FALLBACK_FRAC_RE.subn(
            lambda m: f"[({m.group(1).strip()})/({m.group(2).strip()})]", text
        )
        total += n
        if n == 0:
            break
        text = new_text
    
    # 여전히 남은 불완전 frac 처리 (닫힘 누락 등)
    if "\\frac" in text:
        text, n = UNBALANCED_FRAC_RE.subn(
            lambda m: f"[({m.group(1).strip()})/({m.group(2).strip()})]", text
        )
        total += n
        
    # \sqrt 처리 (중첩 포함)
    text, sqrt_repl_count = convert_sqrt(text)
    total += sqrt_repl_count

    # \left / \right 제거 (구분자는 그대로 둠)
    text = re.sub(r"\\left\s*", "", text)
    text = re.sub(r"\\right\s*", "", text)

    for mapping in (PRIMARY_MAP, SECONDARY_MAP):
        for pattern, repl in mapping:
            text, n = re.subn(pattern, lambda m, r=repl: r, text, flags=re.IGNORECASE)
            total += n

    # 최종적으로 \text{ ... } 형태를 내용만 남기고 양쪽 공백 제거
    def strip_text(match: re.Match[str]) -> str:
        nonlocal total
        inner = match.group(1).strip()
        total += 1
        return inner

    text = re.sub(r"\\?text\s*\{\s*(.*?)\s*\}", strip_text, text)
    text = re.sub(r"\bext\s*\{\s*(.*?)\s*\}", strip_text, text)
    # \boxed{...}, \underlined{...} 제거 (내용만 남기고 양쪽 공백 제거)
    text = re.sub(r"\\boxed\s*\{\s*(.*?)\s*\}", lambda m: m.group(1).strip(), text, flags=re.DOTALL)
    text = re.sub(r"\\underlined\s*\{\s*(.*?)\s*\}", lambda m: m.group(1).strip(), text, flags=re.DOTALL)

    # final mandatory cleanups (order-sensitive)
    # 1) residual \frac -> [()/()] (catch any new ones)
    text, frac_repl_tail = convert_fracs(text)
    total += frac_repl_tail
    # 2) tabs 제거
    text = text.replace("\t", "")
    # 3) \begin{aligned} 제거/ \end{aligned}, \begin{array}{l} 제거/ \end{array} 제거
    text = text.replace(r"\begin{aligned}", "")
    text = text.replace(r"\begin{array}{l}", "")
    text = re.sub(r"\\end.*", "", text)
    # 4) 남은 \\ 제거 (붙어있는 경우 포함) + \\bulet 등 제거
    text = re.sub(r"\\\\bulet", "", text)
    text = re.sub(r"\\\\", "", text)
    # 5) &amp; 제거 (+ %amp; 오기까지 보정)
    text = text.replace("&amp;", "")
    text = text.replace("%amp;", "&")
    # 6) h₂ -> H₂
    text = text.replace("h₂", "H₂")
    # 7) ^\to C 변형 -> ℃ (섭씨 기호) (whitespace 허용)
    text = re.sub(r"\s*\^\s*\\?to\s*C", "℃", text, flags=re.IGNORECASE)
    # 8) stray times/imes -> × (tab/escape 제거 후 깨진 경우 보정)
    text = re.sub(r"(?<![A-Za-z])times(?![A-Za-z])", "×", text)
    text = re.sub(r"(?<![A-Za-z])imes(?![A-Za-z])", "×", text)
    # 9) 기타 소규모 오탈자 보정
    text = apply_minor_fixes(text)
    # 9-1) η_{CO} -> ηCO
    text = re.sub(r"η\s*_\s*\{\s*CO\s*\}", "ηCO", text)
    # 10) '\\ <<' 같은 패턴 제거 및 리터럴 '\n' 제거
    text = re.sub(r"\\\s*<<", "", text)
    text = text.replace(r"\n", "")
    # 11) <begin{aligned}foo></begin{aligned}foo> → foo (태그 래핑 제거)
    text = re.sub(r"<begin\{aligned\}([^<>]+)></begin\{aligned\}\1>", r"\1", text, flags=re.IGNORECASE)
    # 12) 남은 역슬래시 전부 제거 (단독/붙어있는 경우 포함)
    text = text.replace("\\bullet", "")
    text = text.replace("\\", "")
    
    return text, total

def process_inline_math(content: str) -> Tuple[str, dict]:
    """
    <math>...</math> 인라인 수식을 KEYVAL_MAP에 따라 정규화한다.
    """
    count = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        prefix, body, suffix = match.groups()
        new_body, n = normalize_math_text(body)
        count += n
        # math 태그 자체는 제거하고 내용만 남긴다.
        return new_body

    new_content = INLINE_MATH_RE.sub(repl, content)
    return new_content, {"math_inlines": count}


def process_block_math(content: str) -> Tuple[str, dict]:
    """
    <math display=\"block\">...</math> 블록 수식을 KEYVAL_MAP에 따라 정규화한다.
    """
    count = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        prefix, body, suffix = match.groups()
        new_body, n = normalize_math_text(body)
        count += n
        # block math는 태그를 유지한다.
        return f"{prefix}{new_body}{suffix}"

    new_content = BLOCK_MATH_RE.sub(repl, content)
    return new_content, {"math_blocks": count}


def process_headings(content: str) -> Tuple[str, dict]:
    """
    헤딩을 정규화하고 로그용 데이터를 반환한다.
    대상: HTML <h1>~<h6>, Markdown #, 숫자형(1./1.1/1.1.1), <p>안의 숫자형, 한글 소제목(가. 등)
    규칙:
      - TOP_LEVEL_TITLES(공백 무시) 포함 시 무조건 level 1
      - 숫자형: len(parts)==1 -> #, len==2 -> ##, len>=3 -> ###
      - 계층: 하위는 상위 prefix가 같을 때만 승격
      - 레벨 점프 시 이전레벨+1로 보정
      - 한글 소제목은 직전 헤딩이 있을 때 level+1 (최대 3)
    """
    def is_top_level(title: str) -> bool:
        norm_compact = re.sub(r"[.\s]+", "", title)
        return any(candidate in norm_compact for candidate in TOP_LEVEL_COMPACT)

    def html_to_md(match: re.Match[str]) -> str:
        level = int(match.group(1))
        raw_title = re.sub(r"<[^>]+>", "", match.group(2)).strip()
        hashes = "#" * level
        return f"{hashes} {raw_title}\n"

    def label_to_level(parts: list[int], title: str) -> int:
        if is_top_level(title):
            return 1
        # 번호 깊이에 맞춰 레벨 증가 (최대 5)
        return min(len(parts), 5)

    def ok_prefix(parts: list[int], prev_parts: list[int]) -> bool:
        """하위 번호가 상위 번호 prefix를 따르는지 확인한다."""
        if len(parts) == 1:
            return True
        if not prev_parts:
            return True
        if len(parts) == 2:
            # 대제목 번호만 바뀌는 경우 허용
            return True
        need = len(parts) - 1
        return prev_parts[:need] == parts[:need]

    # 1) HTML 헤딩 변환
    content = re.sub(r"<h([1-6])>(.*?)</h\1>", html_to_md, content, flags=re.IGNORECASE | re.DOTALL)

    headings: list[dict] = []
    last_level = 0
    last_parts: list[int] = []
    last_numeric_level = 0

    def apply_heading(idx: int, level: int, title: str, label_parts: list[int] | None = None, original_line: str | None = None):
        nonlocal last_level, last_parts, last_numeric_level, lines
        if level > last_level + 1:
            level = last_level + 1 if last_level else level
        heading_line = f"{'#' * level} {title}"
        # 헤딩이 확정되면 본문 라인은 제거하고 헤딩만 남긴다.
        lines[idx] = heading_line
        headings.append({"level": level, "title": title})
        last_level = level
        if label_parts is not None:
            # 숫자형 헤딩일 때만 계층 prefix 추적
            last_parts = label_parts
            last_numeric_level = level

    lines = content.splitlines()

    for idx, line in enumerate(lines):
        stripped = line.strip()
        p_match = re.match(r"<p>\s*(.*?)\s*</p>", stripped, flags=re.IGNORECASE | re.DOTALL)
        is_p_heading = bool(p_match)
        if p_match:
            inner = re.sub(r"<[^>]+>", "", p_match.group(1)).strip()
            stripped = inner
        md = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        numbered = NUMBERED_HEADING_RE.match(stripped)
        enum_match = re.match(r"^\d+\)\s+(.+)$", stripped)

        if md:
            hashes, title = md.groups()
            num_in_title = NUMBERED_HEADING_RE.match(title)
            if num_in_title:
                label = num_in_title.group("label")
                parts = parse_numeric_label(label)
                if parts and ok_prefix(parts, last_parts):
                    level = label_to_level(parts, title)
                    apply_heading(idx, level, title, parts)
                    continue
            level = 1 if is_top_level(title) else len(hashes)
            apply_heading(idx, level, title)
            continue

        if numbered:
            label = numbered.group("label")
            title = numbered.group("title")
            parts = parse_numeric_label(label)
            if not parts:
                continue
            if parts == last_parts:
                continue
            level = label_to_level(parts, title)
            if not ok_prefix(parts, last_parts):
                continue
            apply_heading(idx, level, f"{label} {title}", parts)
            continue

        loose = re.match(r"^(\d+(?:\.\d+){0,2})\s+(.+)$", stripped)
        if loose:
            label = loose.group(1)
            title = loose.group(2)
            parts = parse_numeric_label(label)
            if not parts:
                continue
            level = label_to_level(parts, title)
            # top-level 하위 번호만 허용: prefix가 있어야 승격
            if not ok_prefix(parts, last_parts):
                continue
            if parts == last_parts:
                continue
            apply_heading(idx, level, f"{label} {title}", parts)
            continue

        # 한글 소제목/열거형: 직전 숫자 헤딩보다 한 단계 낮게 승격 (자기들끼리는 레벨 증가 없음)
        if HANGUL_SUBSECTION_RE.match(stripped) or enum_match:
            if last_numeric_level == 0:
                lines[idx] = f"<p>{line.strip()}</p>"
            else:
                level = min(last_numeric_level + 1, 6)
                apply_heading(idx, level, stripped)

    return "\n".join(lines), {"headings": headings}

def sanitize_file(path: Path, target_dir: Path, out_dir: Path) -> Path:
    """단일 파일을 정규화하고 _rule_sanitized.md로 저장한다."""
    text = path.read_text(encoding="utf-8")
    # 페이지 주석 삽입 후 아래/윗첨자, math 처리, 헤딩 처리 순서로 진행
    text = insert_page_breaks(text, path.name)
    # 특정 파일명(조업공정식 해설) 전용 사전처리
    stem_no_space = re.sub(r"\s+", "", path.stem)
    if "조업공정식해설" in stem_no_space:
        text = preprocess_special_math(text)
    # 단순 HTML 엔티티 디코딩
    text = decode_basic_entities(text)
    # math 태그 외부에 있는 HTML sub/sup까지 포함해 숫자 아래/윗첨자로 변환
    text = normalize_html_subsup(text)
    text, _ = process_inline_math(text)
    text, _ = process_block_math(text)
    text, heading_meta = process_headings(text)
    # η_{CO} 형태를 평문으로 정규화
    text = re.sub(r"η\s*_\s*\{?\s*CO\s*\}?", "ηCO", text)
    # 상위 헤딩 1. 적용범위 누락 시 페이지 주석 바로 아래에 추가
    if not any(re.sub(r"\s+", "", "1. 적용범위") in re.sub(r"\s+", "", h["title"]) for h in heading_meta.get("headings", [])):
        text = re.sub(
            r"(<!-- 페이지번호: [^>]+ -->)",
            r"\1\n# 1. 적용범위",
            text,
            count=1,
        )

    rel = path.relative_to(target_dir)
    dest = out_dir / rel.parent / f"{rel.stem}_rule_sanitized{rel.suffix}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    headings = heading_meta.get("headings") or []
    missing_top = [t for t in TOP_LEVEL_TITLES if not any(re.sub(r"\s+", "", t) in re.sub(r"\s+", "", h["title"]) for h in headings)]
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with HEADING_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{rel}\n")
        f.write("parsed_headings:\n")
        for h in headings:
            f.write(f"- {h['level']} {h['title']}\n")
        f.write("missing:\n")
        if missing_top:
            for m in missing_top:
                f.write(f"- {m}\n")
        else:
            f.write("NONE\n")
        f.write("\n")
    return dest


def sanitize_directory(target_dir: Path = DEFAULT_TARGET, out_dir: Path = DEFAULT_OUTPUT) -> None:
    """target_dir의 모든 md를 정규화하여 out_dir에 저장한다."""
    if not target_dir.exists():
        return
    for md_path in sorted(target_dir.rglob("*.md")):
        sanitize_file(md_path, target_dir, out_dir)

def main() -> None:
    """기본 target 디렉터리를 정규화하여 sanitize 폴더에 저장."""
    sanitize_directory()
    print(f"sanitized markdown written under {DEFAULT_OUTPUT}")

if __name__ == "__main__":
    main()
