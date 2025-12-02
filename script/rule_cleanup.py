#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path
from typing import Tuple

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
    (r"&Gt;", ">")  
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

def normalize_math_text(text: str) -> Tuple[str, int]:
    """1차(섭씨/그리스/수학) 후 2차(화학식/단위) 순서로 변환한다."""
    total = 0

    # 숫자 아래첨자/윗첨자 유니코드 변환 (_2 -> ₂, ^-2 -> ⁻²) - 한 자리 또는 음수 한 자리만 대상
    sub_map = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
    sup_map = str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹")

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
    # 5) &amp; 제거
    text = text.replace("&amp;", "")
    # 6) h₂ -> H₂
    text = text.replace("h₂", "H₂")
    # 7) ^\to C 변형 -> ℃ (섭씨 기호) (whitespace 허용)
    text = re.sub(r"\s*\^\s*\\?to\s*C", "℃", text, flags=re.IGNORECASE)
    # 8) stray times/imes -> × (tab/escape 제거 후 깨진 경우 보정)
    text = re.sub(r"(?<![A-Za-z])times(?![A-Za-z])", "×", text)
    text = re.sub(r"(?<![A-Za-z])imes(?![A-Za-z])", "×", text)
    # 9) 기타 소규모 오탈자 보정
    text = apply_minor_fixes(text)
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
        return f"{prefix}{new_body}{suffix}"

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
        return f"{prefix}{new_body}{suffix}"

    new_content = BLOCK_MATH_RE.sub(repl, content)
    return new_content, {"math_blocks": count}


def process_headings(content: str) -> Tuple[str, dict]:
    """
    헤딩 변환/정리를 처리한다.
    현재는 베이스라인: 그대로 반환.
    """
    return content, {"headings": []}

def sanitize_file(path: Path, target_dir: Path, out_dir: Path) -> Path:
    """단일 파일을 정규화하고 _rule_sanitized.md로 저장한다."""
    text = path.read_text(encoding="utf-8")
    text, _ = process_inline_math(text)
    text, _ = process_block_math(text)
    text, _ = process_headings(text)

    rel = path.relative_to(target_dir)
    dest = out_dir / rel.parent / f"{rel.stem}_rule_sanitized{rel.suffix}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
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
