#!/usr/bin/env python3
"""
Chandra OCR 모델을 이용해 PDF를 페이지 단위로 분석하고, 본문·표·이미지·다이어그램을 구조화된 결과로 추출한다.

예시:
  python script/chandra_pdf_parser.py --all
  python script/chandra_pdf_parser.py _datasets/ecminer/1장_v3.1.pdf --max-pages 2
"""
from __future__ import annotations

import argparse
import json
import datetime
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence
import sys
import time

import pypdfium2 as pdfium
import torch
from bs4 import BeautifulSoup
from PIL import Image
from transformers import AutoModelForImageTextToText, AutoProcessor

from chandra.model.hf import generate_hf
from chandra.model.schema import BatchInputItem
from chandra.output import get_image_name

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.append(str(SCRIPT_DIR))

from chandra_prompt_override import PROMPT_MAPPING

DATASET_ROOT = Path(__file__).resolve().parents[1] / "_datasets"
DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parents[1] / "output/" / "chandra"
DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[1] / ".models" / "datalab-to" / "chandra"
DEFAULT_HF_MODEL = "datalab-to/chandra"
LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
LOG_FILE = LOG_DIR / "chandra.log"


@dataclass
class ComponentAsset:
    page_index: int  # 컴포넌트가 속한 페이지 번호(0-based)
    bbox: Sequence[int]  # 원본 이미지 기준 크롭 영역 (crop-top 이후 좌표)
    image_path: Path  # 저장된 이미지 파일 경로
    label: str  # Table, Figure, Diagram, Math-Block 등 유형 정보
# _datasets 폴더 전체를 훑어서 PDF 목록을 반환한다.
def list_pdfs() -> List[Path]:
    if not DATASET_ROOT.exists():
        return []
    return sorted(DATASET_ROOT.rglob("*.pdf"))


def match_requested_pdfs(requests: Iterable[str]) -> List[Path]:
    all_pdfs = list_pdfs()
    if not all_pdfs:
        return []

    resolved: List[Path] = []
    for request in requests:
        req_path = Path(request)
        if req_path.is_absolute():
            if req_path.exists() and req_path.suffix.lower() == ".pdf":
                resolved.append(req_path)
            else:
                print(f"[WARN] PDF not found: {req_path}")
            continue

        possible_matches: List[Path] = [
            pdf
            for pdf in all_pdfs
            if pdf.name == request or str(pdf.relative_to(DATASET_ROOT)) == request
        ]

        # Allow passing explicit relative filesystem paths with optional _datasets prefix.
        manual_candidates: List[Path] = []
        if req_path.suffix.lower() == ".pdf":
            manual_candidates.append(DATASET_ROOT / req_path)
            if req_path.parts and req_path.parts[0] == "_datasets":
                manual_candidates.append(DATASET_ROOT / Path(*req_path.parts[1:]))

        for candidate in manual_candidates:
            if candidate.exists():
                possible_matches.append(candidate.resolve())

        matches = sorted(set(possible_matches))
        if matches:
            resolved.extend(matches)
        else:
            print(f"[WARN] Could not match '{request}' to any PDF under {DATASET_ROOT}")
    return resolved


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Use Chandra OCR on PDFs with table crops.")
    parser.add_argument("pdfs", nargs="*", help="Specific PDF names/paths under _datasets.")
    parser.add_argument("--all", action="store_true", help="Process every PDF under _datasets.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT, help="Directory to store outputs.")
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR, help="Path to the local Chandra model.")
    parser.add_argument(
        "--hf-model",
        default=DEFAULT_HF_MODEL,
        help="Hugging Face model ID used as a fallback when the local model is unavailable.",
    )
    parser.add_argument("--dpi", type=int, default=150, help="Rendering DPI for PDF to image conversion.")
    parser.add_argument("--max-pages", type=int, default=None, help="Optionally limit number of pages per PDF.")
    parser.add_argument("--max-tokens", type=int, default=1800, help="Maximum tokens per page generation.")
    parser.add_argument("--include-headers", action="store_true", help="Retain page headers/footers in output.")
    parser.add_argument(
        "--crop-top",
        type=int,
        default=0,
        help="Optional number of pixels to remove from the top of each rendered page before OCR.",
    )
    return parser.parse_args()


def render_pdf_pages(pdf_path: Path, dpi: int) -> List[Image.Image]:
    document = pdfium.PdfDocument(str(pdf_path))
    scale = dpi / 72
    images: List[Image.Image] = []
    for page in document:
        bitmap = page.render(scale=scale)
        images.append(bitmap.to_pil())
    return images


def _build_model_kwargs() -> dict:
    kwargs = {"trust_remote_code": True, "low_cpu_mem_usage": True}
    kwargs["dtype"] = torch.bfloat16 if torch.cuda.is_available() else torch.float32
    kwargs["device_map"] = "auto" if torch.cuda.is_available() else None
    return kwargs


def _load_model(checkpoint: str | Path):
    kwargs = _build_model_kwargs()
    model = AutoModelForImageTextToText.from_pretrained(checkpoint, **kwargs).eval()
    if torch.cuda.is_available():
        model = model.to("cuda")
    processor = AutoProcessor.from_pretrained(checkpoint, trust_remote_code=True)
    model.processor = processor
    return model


def load_model_with_fallback(model_dir: Path, hf_model: str):
    attempts: List[tuple[str | Path, str]] = []
    if model_dir and model_dir.exists():
        attempts.append((model_dir, f"local path {model_dir}"))
    else:
        print(f"[WARN] Local model directory {model_dir} not found; falling back to Hugging Face hub.")
    if hf_model:
        attempts.append((hf_model, f"Hugging Face model '{hf_model}'"))

    last_error: Exception | None = None
    for checkpoint, label in attempts:
        try:
            print(f"[INFO] Loading Chandra model from {label}")
            return _load_model(checkpoint)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"[WARN] Failed to load model from {label}: {exc}")

    raise RuntimeError("Unable to load any Chandra model checkpoint.") from last_error


def scaled_bbox(div, image: Image.Image) -> List[int]:
    bbox_raw = div.get("data-bbox")
    try:
        bbox = json.loads(bbox_raw) if bbox_raw else [0, 0, 1, 1]
    except json.JSONDecodeError:
        bbox = [0, 0, 1, 1]

    width, height = image.size
    width_scaler = width / 1024
    height_scaler = height / 1024
    x0 = max(0, int(bbox[0] * width_scaler))
    y0 = max(0, int(bbox[1] * height_scaler))
    x1 = min(width, int(bbox[2] * width_scaler))
    y1 = min(height, int(bbox[3] * height_scaler))
    return [x0, y0, x1, y1]


def ensure_paragraph_wrapping(div) -> None:
    text_content = str(div.decode_contents()).strip()
    if not text_content:
        return
    if re.search(r"<.+>", text_content):
        return
    wrapped = f"<p>{text_content}</p>"
    div.clear()
    div.append(BeautifulSoup(wrapped, "html.parser"))


def has_block_math(div) -> bool:
    """<math display="block"> 형태의 수식이 있는지 여부를 확인한다."""
    return any(tag.name == "math" and tag.get("display") == "block" for tag in div.find_all("math"))


def adjust_bbox_with_padding(
    bbox: List[int],
    image: Image.Image,
    padding: int = 15,
    top_extra: int = 30,
    bottom_cut: int = 0,
    full_width: bool = False,
) -> List[int]:
    """크롭 범위를 위쪽으로 넉넉히, 아래쪽은 조금 덜 포함하도록 조정한다."""
    width, height = image.size
    x0, y0, x1, y1 = bbox
    if full_width:
        x0 = 0
        x1 = width
    else:
        x0 = max(0, x0 - padding)
        x1 = min(width, x1 + padding)
    y0 = max(0, y0 - (padding + top_extra))
    y1 = min(height, y1 + max(0, padding - bottom_cut))
    return [x0, y0, x1, y1]


def build_page_html(
    raw_html: str,
    image: Image.Image,
    pdf_stem: str,
    page_index: int,
    include_headers: bool,
    components_dir: Path,
    components_rel_dir: Path,
) -> tuple[str, List[ComponentAsset]]:
    soup = BeautifulSoup(raw_html, "html.parser")
    full_width_crop = pdf_stem.startswith("TP-030-010-030 ")
    top_level_divs = soup.find_all("div", recursive=False)
    fragments: List[str] = []
    div_idx = 0
    table_counter = 0
    components: List[ComponentAsset] = []
    # OCR로 재구성하기 어려운 레이블은 이미지로 잘라 저장한다.
    diagram_labels = {"Image", "Figure", "Diagram", "Complex-Block"}

    for div in top_level_divs:
        div_idx += 1
        label = div.get("data-label") or ""
        block_math = has_block_math(div)

        if not include_headers and label in {"Page-Header", "Page-Footer"}:
            continue

        if label in {"Text", "Title"}:
            ensure_paragraph_wrapping(div)

        # Image/Figure/Diagram/Complex-Block 레이블 혹은 block math는 모두 컴포넌트 이미지로 저장
        if label in diagram_labels or block_math:
            img_tag = div.find("img")
            if not img_tag:
                img_tag = soup.new_tag("img")
                div.append(img_tag)
            bbox = scaled_bbox(div, image)
            padded_bbox = adjust_bbox_with_padding(bbox, image, full_width=full_width_crop)
            block_image = image.crop(padded_bbox)
            img_name = get_image_name(raw_html, div_idx)
            components_dir.mkdir(parents=True, exist_ok=True)
            img_path = components_dir / img_name
            block_image.save(img_path)
            img_tag["src"] = (components_rel_dir / img_name).as_posix()
            if not img_tag.get("alt"):
                alt_label = label if label else ("Math-Block" if block_math else "Figure")
                img_tag["alt"] = f"{alt_label} snippet"
            asset_label = label if label else ("Math-Block" if block_math else "Figure")
            if block_math and label not in diagram_labels:
                asset_label = "Math-Block"
            components.append(ComponentAsset(page_index, padded_bbox, img_path, asset_label))

        if label == "Table":
            table_counter += 1
            bbox = scaled_bbox(div, image)
            padded_bbox = adjust_bbox_with_padding(bbox, image, full_width=full_width_crop)
            table_name = f"{pdf_stem}_p{page_index+1:03d}_table_{table_counter:02d}.png"
            components_dir.mkdir(parents=True, exist_ok=True)
            table_image_path = components_dir / table_name
            image.crop(padded_bbox).save(table_image_path)
            components.append(ComponentAsset(page_index, padded_bbox, table_image_path, "Table"))

            link_tag = soup.new_tag("a", href=(components_rel_dir / table_name).as_posix())
            link_tag.string = "Table snapshot"
            paragraph = soup.new_tag("p")
            paragraph.append(link_tag)
            div.append(paragraph)
        fragments.append(str(div.decode_contents()))

    return "\n\n".join(fragments), components


def relative_output_dir(pdf_path: Path, output_root: Path) -> Path:
    if DATASET_ROOT in pdf_path.parents:
        rel = pdf_path.relative_to(DATASET_ROOT)
        return output_root / rel.with_suffix("")
    return output_root / pdf_path.stem


def already_processed(pdf_path: Path, output_root: Path) -> bool:
    target_dir = relative_output_dir(pdf_path, output_root)
    if not target_dir.exists():
        return False
    expected_md = target_dir / f"{pdf_path.stem}.md"
    if expected_md.exists():
        return True
    return any(target_dir.glob("*.md"))


def process_pdf(
    pdf_path: Path,
    model,
    args: argparse.Namespace,
) -> None:
    start_timestamp = datetime.datetime.now()
    images = render_pdf_pages(pdf_path, dpi=args.dpi)
    if args.crop_top > 0:
        cropped = []
        for img in images:
            width, height = img.size
            top = min(args.crop_top, height)
            cropped.append(img.crop((0, top, width, height)))
        images = cropped
    if args.max_pages:
        images = images[: args.max_pages]

    if not images:
        print(f"[WARN] No pages rendered for {pdf_path}")
        return

    output_dir = relative_output_dir(pdf_path, args.output)
    components_dir = output_dir / "components"
    components_rel = Path("components")
    output_dir.mkdir(parents=True, exist_ok=True)
    start_time = time.time()

    sections: List[str] = []
    component_assets: List[ComponentAsset] = []

    for page_idx, image in enumerate(images):
        batch = BatchInputItem(image=image, prompt_type="ocr_layout")
        result = generate_hf([batch], model, max_output_tokens=args.max_tokens)[0]
        if result.error:
            raise RuntimeError(f"OCR failed for {pdf_path} page {page_idx+1}")

        page_html, components = build_page_html(
            raw_html=result.raw,
            image=image,
            pdf_stem=pdf_path.stem,
            page_index=page_idx,
            include_headers=args.include_headers,
            components_dir=components_dir,
            components_rel_dir=components_rel,
        )
        component_assets.extend(components)
        sections.append(f"<h2>Page {page_idx + 1}</h2>\n{page_html}")

    output_file = output_dir / f"{pdf_path.stem}.md"
    output_file.write_text("\n\n<hr/>\n\n".join(sections), encoding="utf-8")

    if component_assets:
        manifest = [
            {
                "page": asset.page_index + 1,
                "bbox": asset.bbox,
                "image": str(asset.image_path.relative_to(output_dir)),
                "label": asset.label,
            }
            for asset in component_assets
        ]
        (output_dir / "comps.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    elapsed_sec = time.time() - start_time
    elapsed_min = elapsed_sec / 60
    print(
        f"[INFO] Processed {pdf_path.name} -> {output_file} "
        f"({elapsed_min:.2f} min / {elapsed_sec:.1f} s)"
    )
    log_line = (
        f"{start_timestamp.strftime('%Y-%m-%d %H:%M:%S')}\t{pdf_path.name}\t"
        f"{elapsed_sec:.2f}s ({elapsed_min:.2f}m)\n"
    )
    with LOG_FILE.open("a", encoding="utf-8") as lf:
        lf.write(log_line)


def main() -> int:
    args = parse_args()
    args.output = args.output.resolve()
    args.model_dir = args.model_dir.resolve()
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    if args.all:
        targets = list_pdfs()
    else:
        if not args.pdfs:
            raise SystemExit("Provide at least one PDF path or use --all.")
        targets = match_requested_pdfs(args.pdfs)

    if not targets:
        print("[INFO] No PDFs to process.")
        return 1

    model = load_model_with_fallback(args.model_dir, args.hf_model)

    for pdf in targets:
        if args.all and already_processed(pdf, args.output):
            print(f"[INFO] Skipping {pdf} (existing Markdown found under output).")
            continue
        try:
            process_pdf(pdf, model, args)
        except Exception as exc:  # noqa: BLE001
            error_msg = f"[ERROR] Failed to process {pdf.name}: {exc}"
            print(error_msg)
            log_line = (
                f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\t"
                f"{pdf.name}\tERROR: {exc}\n"
            )
            with LOG_FILE.open("a", encoding="utf-8") as lf:
                lf.write(log_line)
            continue
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
