#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path
from typing import Tuple

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
    (r"\\to", "→"),
    (r"\\pm", "±"),
    (r"\\%", "%"),
    (r"\\times", "×"),
    (r"\\cdot", "·"),
    (r"\\dots", "…"),
    (r"\\le(?!ft)", "≤"),  # \left 보호
    (r"\\ge(?!ft)", "≥"),  # \left 보호
    (r"\\sim", "~"),    
    (r"\\circ", "◯"),
    (r"\\triangle", "△"),
    (r"\\square", "□"),
    (r"\\div", "÷"),
    (r"\\approx", "≒"),
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
    (r"\s*\\mathrm\{\s*cm\s*\}", "cm"),
]

INLINE_MATH_RE = re.compile(r"(<math(?![^>]*display=\"block\")[^>]*>)(.*?)(</math>)", re.IGNORECASE | re.DOTALL)
BLOCK_MATH_RE = re.compile(r"(<math[^>]*display=\"block\"[^>]*>)(.*?)(</math>)", re.IGNORECASE | re.DOTALL)


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

    text = re.sub(r"_(\d)", sub_digit_repl, text)
    text = re.sub(r"\^\s*\{\s*(-?\d+)\s*\}", sup_brace_repl, text)
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


def main() -> None:
    """예시 실행 흐름."""
    sample_path = Path("output/chandra/example.md")
    if not sample_path.exists():
        return
    text = sample_path.read_text(encoding="utf-8")
    text, meta_inline = process_inline_math(text)
    text, meta_block = process_block_math(text)
    text, meta_headings = process_headings(text)
    sample_path.write_text(text, encoding="utf-8")
    print("done", meta_inline, meta_block, meta_headings)


if __name__ == "__main__":
    main()
