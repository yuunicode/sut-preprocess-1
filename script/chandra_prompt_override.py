"""Chandra 기본 프롬프트를 덮어써서 수식·다이어그램 출력을 제어하는 모듈."""
from __future__ import annotations

from chandra.prompts import (
    ALLOWED_ATTRIBUTES,
    ALLOWED_TAGS,
    OCR_LAYOUT_PROMPT,
    OCR_PROMPT,
    PROMPT_ENDING,
    PROMPT_MAPPING as BASE_PROMPT_MAPPING,
)

# Original snippet in chandra.prompts urged LaTeX-formatted math (e.g. "Inline math: Surround math with <math>...> tags")

CUSTOM_PROMPT_ENDING = f"""
Only use these tags {ALLOWED_TAGS}, and these attributes {ALLOWED_ATTRIBUTES}.

Guidelines:
* Math: express formulas as plain strings such as "CO2 40%" or "Si2 = 0.5", use A/B for fractions, and avoid LaTeX commands like \\frac, _ or ^.
* Tables: Use colspan and rowspan attributes to match table structure.
* Formatting: Maintain consistent formatting with the image, including spacing, indentation, subscripts/superscripts, and special characters.
* Images/Diagrams: summarize complex diagrams or arrow flows in a short sentence and rely on the cropped image instead of reproducing every shape.
* Forms: Mark checkboxes and radio buttons properly.
* Text: join lines together properly into paragraphs using <p>...</p> tags. Use <br> only when absolutely necessary.
* Use the simplest possible HTML structure that accurately represents the content of the block.
* Make sure the text is accurate and easy for a human to read and interpret. Reading order should be correct and natural.
""".strip()


def _patch_prompt(prompt: str) -> str:
    if PROMPT_ENDING in prompt:
        return prompt.replace(PROMPT_ENDING, CUSTOM_PROMPT_ENDING)
    return f"{prompt}\n\n{CUSTOM_PROMPT_ENDING}"


PROMPT_MAPPING = dict(BASE_PROMPT_MAPPING)
PROMPT_MAPPING["ocr_layout"] = _patch_prompt(OCR_LAYOUT_PROMPT)
PROMPT_MAPPING["ocr"] = _patch_prompt(OCR_PROMPT)
