#!/usr/bin/env python3
"""Run local Qwen2.5-VL model on LLM payloads, validate outputs, and log errors."""
from __future__ import annotations

import argparse
import datetime
import json
import re
import torch
from pathlib import Path
from typing import Any, Dict, Tuple

from PIL import Image
from transformers import AutoModelForVision2Seq, AutoTokenizer
from huggingface_hub.errors import HFValidationError

LLM_DIR = Path(__file__).resolve().parents[1] / "output" / "llm"
LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
DEFAULT_MODEL_PATH = Path(__file__).resolve().parents[1] / ".models" / "qwen" / "Qwen2.5-VL-7B-Instruct"
ERROR_LOG = LOG_DIR / "llm_errors.log"
DEFAULT_DEVICE = "cuda"


# --------------------- 공통 유틸 ---------------------
def load_payload(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def log_error(msg: str) -> None:
    ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    with ERROR_LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")


def extract_json(text: str) -> Dict[str, Any]:
    """Best-effort JSON 추출: 전체 파싱 → 첫 braces 블록 시도."""
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    return {}


def validate_output(template: Dict[str, Any], candidate: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """template 구조에 맞게 candidate를 병합. 형식 불일치 시 template 유지."""
    merged: Dict[str, Any] = {}
    ok = True
    for key, default_val in template.items():
        if key not in candidate:
            merged[key] = default_val
            ok = False
            continue
        val = candidate[key]
        if isinstance(default_val, list):
            if isinstance(val, list) and all(isinstance(x, str) for x in val):
                merged[key] = val
            elif isinstance(val, str):
                merged[key] = [val]
                ok = False
            else:
                merged[key] = default_val
                ok = False
        elif isinstance(default_val, str):
            if isinstance(val, str):
                merged[key] = val
            else:
                merged[key] = default_val
                ok = False
        else:
            merged[key] = val
    return merged, ok


def build_prompt(instruction: str, input_payload: Dict[str, Any]) -> str:
    return f"""{instruction}

입력:
{json.dumps(input_payload, ensure_ascii=False, indent=2)}

출력은 위 출력 형식에 맞는 JSON만 반환하라."""


# --------------------- LLM 호출 (Vision-Language) ---------------------
def load_image(image_path: Path) -> Image.Image | None:
    try:
        img = Image.open(image_path).convert("RGB")
        return img
    except Exception as exc:
        log_error(f"image open failed: {image_path} ({exc})")
        return None


def build_vl_messages(prompt: str, image: Image.Image | None) -> list[dict]:
    if image is not None:
        return [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": image},
                    {"type": "text", "text": prompt},
                ],
            }
        ]
    return [{"role": "user", "content": prompt}]


def load_qwen(model_path: Path, device: str = DEFAULT_DEVICE):
    model_path = model_path.resolve()
    if not model_path.exists():
        raise FileNotFoundError(f"model path not found: {model_path}")
    try:
        tokenizer = AutoTokenizer.from_pretrained(
            str(model_path), trust_remote_code=True, local_files_only=True, cache_dir=str(model_path)
        )
        model = AutoModelForVision2Seq.from_pretrained(
            str(model_path),
            trust_remote_code=True,
            device_map=device,
            local_files_only=True,
            cache_dir=str(model_path),
        )
    except HFValidationError as exc:  # pragma: no cover
        raise FileNotFoundError(
            f"local model files not found under {model_path}. "
            "config.json/model.safetensors 등이 포함된 모델 루트 폴더를 --model-path로 지정해야 합니다."
        ) from exc
    return tokenizer, model


def run_chat(model, tokenizer, prompt: str, image: Image.Image | None = None, max_new_tokens: int = 512) -> str:
    messages = build_vl_messages(prompt, image)
    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(text, return_tensors="pt").to(model.device)
    gen_kwargs = {
        "max_new_tokens": max_new_tokens,
        "repetition_penalty": 1.3,
        "no_repeat_ngram_size": 4,
        "pad_token_id": tokenizer.eos_token_id,
    }
    with torch.no_grad():
        outputs = model.generate(**inputs, **gen_kwargs)
    generated = outputs[0][inputs["input_ids"].shape[-1] :]
    return tokenizer.decode(generated, skip_special_tokens=True).strip()


# --------------------- 메인 처리 ---------------------
def process_file(path: Path, tokenizer, model, max_new_tokens: int) -> None:
    payloads = load_payload(path)
    results = []

    for entry in payloads:
        entry_status = "성공"
        entry_id = entry.get("id", "")
        instruction = entry.get("instruction", "")
        input_payload = entry.get("input", {}) or {}
        template_output = entry.get("output", {}) or {}
        image_obj = None
        if isinstance(input_payload, dict):
            img_link = input_payload.get("image_link")
            if img_link:
                img_candidate = Path(img_link)
                if not img_candidate.is_absolute():
                    img_candidate = Path(__file__).resolve().parents[1] / img_link
                if img_candidate.exists():
                    image_obj = load_image(img_candidate)
                    if image_obj:
                        print(f"[이미지사용] {path.name} id={entry_id} image={img_candidate}")
                    else:
                        print(f"[이미지없음] {path.name} id={entry_id} image_link={img_link} (로드 실패)")
                else:
                    print(f"[이미지없음] {path.name} id={entry_id} image_link={img_link} (파일 없음)")

        resp_text = ""
        try:
            prompt = build_prompt(instruction, input_payload)
            resp_text = run_chat(model, tokenizer, prompt, image=image_obj, max_new_tokens=max_new_tokens)
        except Exception as exc:  # pragma: no cover
            log_error(f"file={path.name} id={entry_id} error=LLM call failed: {exc}")
            resp_text = ""

        merged = template_output
        if resp_text:
            candidate = extract_json(resp_text)
            merged, ok = validate_output(template_output, candidate)
            if not ok:
                log_error(f"file={path.name} id={entry_id} error=invalid_output resp='{resp_text[:200]}'")
                entry_status = "실패"
        else:
            log_error(f"file={path.name} id={entry_id} error=empty_response")
            entry_status = "실패"

        result_entry = dict(entry)
        result_entry["output"] = merged
        result_entry["raw_response"] = resp_text
        results.append(result_entry)
        print(f"[{entry_status}] {path.name} id={entry_id}")

    out_path = path.with_name(path.name.replace("_payload", "_result"))
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[INFO] wrote {out_path} ({len(results)} items)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local Qwen model on payload JSONs and fill outputs.")
    parser.add_argument("--payload", action="append", type=Path, help="처리할 payload 파일 경로(여러 번 지정 가능). 없으면 output/llm/*_payload.json 전부.")
    args = parser.parse_args()

    targets = args.payload
    if not targets:
        targets = sorted(LLM_DIR.glob("*_payload.json"))

    if not targets:
        print("[WARN] no payload files found.")
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    tokenizer, model = load_qwen(DEFAULT_MODEL_PATH, device=DEFAULT_DEVICE)

    for path in targets:
        if path.is_dir():
            continue
        process_file(path.resolve(), tokenizer=tokenizer, model=model, max_new_tokens=512)


if __name__ == "__main__":
    main()
