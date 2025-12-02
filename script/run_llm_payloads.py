#!/usr/bin/env python3
"""Run local Qwen model on LLM payloads, validate outputs, and log errors."""
from __future__ import annotations

import argparse
import datetime
import json
import re
from pathlib import Path
from typing import Any, Dict, Tuple

from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline

LLM_DIR = Path(__file__).resolve().parents[1] / "output" / "llm"
LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
DEFAULT_MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "qwen"
ERROR_LOG = LOG_DIR / "llm_errors.log"


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


# --------------------- LLM 호출 ---------------------
def load_qwen_pipeline(model_path: Path, device: str = "auto"):
    model_path = model_path.resolve()
    tokenizer = AutoTokenizer.from_pretrained(str(model_path), trust_remote_code=True, local_files_only=True)
    model = AutoModelForCausalLM.from_pretrained(
        str(model_path), trust_remote_code=True, device_map=device, local_files_only=True
    )
    return pipeline(
        "text-generation",
        model=model,
        tokenizer=tokenizer,
        device_map=device,
    )


def call_llm(generator, prompt: str, max_new_tokens: int) -> str:
    outputs = generator(prompt, max_new_tokens=max_new_tokens, do_sample=False, return_full_text=False)
    if not outputs:
        return ""
    return outputs[0].get("generated_text", "")


# --------------------- 메인 처리 ---------------------
def process_file(path: Path, generator, max_new_tokens: int) -> None:
    payloads = load_payload(path)
    updated_payloads = []

    for entry in payloads:
        entry_id = entry.get("id", "")
        instruction = entry.get("instruction", "")
        input_payload = entry.get("input", {}) or {}
        template_output = entry.get("output", {}) or {}

        resp_text = ""
        try:
            prompt = build_prompt(instruction, input_payload)
            resp_text = call_llm(generator, prompt, max_new_tokens=max_new_tokens)
        except Exception as exc:  # pragma: no cover
            log_error(f"file={path.name} id={entry_id} error=LLM call failed: {exc}")
            resp_text = ""

        merged = template_output
        if resp_text:
            candidate = extract_json(resp_text)
            merged, ok = validate_output(template_output, candidate)
            if not ok:
                log_error(f"file={path.name} id={entry_id} error=invalid_output resp='{resp_text[:200]}'")
        else:
            log_error(f"file={path.name} id={entry_id} error=empty_response")

        entry["output"] = merged
        entry["raw_response"] = resp_text
        updated_payloads.append(entry)

    path.write_text(json.dumps(updated_payloads, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[INFO] updated {path} ({len(updated_payloads)} items)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local Qwen model on payload JSONs and fill outputs.")
    parser.add_argument("--model-path", type=Path, default=DEFAULT_MODEL_PATH, help="로컬 Qwen 모델 경로 (기본: models/qwen)")
    parser.add_argument("--payload", action="append", type=Path, help="처리할 payload 파일 경로(여러 번 지정 가능). 없으면 output/llm/*_payload.json 전부.")
    parser.add_argument("--max-new-tokens", type=int, default=512, help="생성 토큰 수 (기본 512)")
    parser.add_argument("--device", default="auto", help="transformers device_map (기본 auto, GPU 가능 시 cuda)")
    args = parser.parse_args()

    targets = args.payload
    if not targets:
        targets = sorted(LLM_DIR.glob("*_payload.json"))

    if not targets:
        print("[WARN] no payload files found.")
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    generator = load_qwen_pipeline(args.model_path, device=args.device)

    for path in targets:
        if path.is_dir():
            continue
        process_file(path.resolve(), generator=generator, max_new_tokens=args.max_new_tokens)


if __name__ == "__main__":
    main()
