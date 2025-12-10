#!/usr/bin/env python3
"""Run local Qwen VL model on LLM payloads, validate outputs, and log errors."""
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

REPO_ROOT = Path(__file__).resolve().parents[2]
LLM_DIR = REPO_ROOT / "output" / "llm"
LOG_DIR = REPO_ROOT / "logs"
DEFAULT_MODEL_PATH = REPO_ROOT / ".models" / "qwen" / "qwen3-vl-8b"
ERROR_LOG = LOG_DIR / "llm_errors.log"
FAILED_LOG = LOG_DIR / "failed_llm.log"
DEFAULT_DEVICE = "cuda"
# LLM 세부 파라미터 (필요시 상단에서만 수정)
MAX_NEW_TOKENS = 512  # 생성 토큰 수
REPETITION_PENALTY = 1.3  # 이미 생성된 토큰 반복을 억제 (값이 클수록 반복 감소)
NO_REPEAT_NGRAM_SIZE = 4  # 지정된 ngram 크기 반복 금지 (4-gram 반복 방지)
RETRY_MAX_ATTEMPTS = 3  # 이미지(SUM/TRANS) 파싱 실패 시 최대 재시도 횟수


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


def clean_response_text(text: str) -> str:
    """LLM 응답에서 ```json ``` 코드펜스, 탭 등을 제거해 파싱을 돕는다."""
    cleaned = text.strip()
    # 코드펜스 제거
    cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.I | re.M)
    cleaned = re.sub(r"```$", "", cleaned, flags=re.M)
    # 탭 제거
    cleaned = cleaned.replace("\t", "")
    return cleaned.strip()


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


def needs_image_detail_retry(file_name: str, output: Dict[str, Any]) -> bool:
    """이미지 SUM/TRANS에서 내용이 너무 빈약하면 재시도 요구."""
    is_sum = file_name.startswith("llm_images_sum")
    is_trans = file_name.startswith("llm_images_trans")
    if not (is_sum or is_trans):
        return False
    summary = (output or {}).get("image_summary", "") or ""
    if isinstance(summary, list):
        summary = " ".join([s for s in summary if isinstance(s, str)])
    summary = summary or ""
    stripped = summary.strip()
    if is_sum and len(stripped) < 60:
        return True
    # 중국어/러시아어/아랍권 문자나 아랍-인도 숫자가 포함되면 재시도
    if re.search(r"[\u4e00-\u9fff]", stripped) or re.search(r"[А-Яа-яЁё]", stripped):
        return True
    if re.search(r"[\u0600-\u06FF]", stripped):  # Arabic block
        return True
    if re.search(r"[\u0660-\u0669\u06F0-\u06F9]", stripped):  # Arabic-Indic, Eastern Arabic-Indic digits
        return True
    return False


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


def run_chat(model, tokenizer, prompt: str, image: Image.Image | None = None) -> str:
    messages = build_vl_messages(prompt, image)
    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(text, return_tensors="pt").to(model.device)
    gen_kwargs = {
        "max_new_tokens": MAX_NEW_TOKENS,
        "repetition_penalty": REPETITION_PENALTY,
        "no_repeat_ngram_size": NO_REPEAT_NGRAM_SIZE,
        "pad_token_id": tokenizer.eos_token_id,
    }
    with torch.no_grad():
        outputs = model.generate(**inputs, **gen_kwargs)
    generated = outputs[0][inputs["input_ids"].shape[-1] :]
    return tokenizer.decode(generated, skip_special_tokens=True).strip()


# --------------------- 메인 처리 ---------------------
def process_file(path: Path, tokenizer, model, max_new_tokens: int) -> None:
    payloads = load_payload(path)
    out_path = path.with_name(path.name.replace("_payload", "_result"))
    results = []
    existing_keys = set()
    if out_path.exists():
        try:
            existing = load_payload(out_path)
            results.extend(existing)
            for item in existing:
                key = (item.get("id"), (item.get("input") or {}).get("filename"))
                existing_keys.add(key)
        except Exception:
            pass

    for entry in payloads:
        entry_status = "성공"
        entry_id = entry.get("id", "")
        instruction = entry.get("instruction", "")
        input_payload = entry.get("input", {}) or {}
        key = (entry_id, input_payload.get("filename"))
        if key in existing_keys:
            print(f"[SKIP] {path.name} id={entry_id} (already in result)")
            continue
        template_output = entry.get("output", {}) or {}
        image_obj = None
        if isinstance(input_payload, dict):
            img_link = input_payload.get("image_link")
            if img_link:
                img_candidate = Path(img_link)
                if not img_candidate.is_absolute():
                    img_candidate = REPO_ROOT / img_link
                if img_candidate.exists():
                    image_obj = load_image(img_candidate)
                    if image_obj:
                        print(f"[이미지사용] {path.name} id={entry_id} image={img_candidate}")
                    else:
                        print(f"[이미지없음] {path.name} id={entry_id} image_link={img_link} (로드 실패)")
                else:
                    print(f"[이미지없음] {path.name} id={entry_id} image_link={img_link} (파일 없음)")

        # 이미지 SUM/TRANS, 테이블 STR/UNSTR는 파싱 실패 시 최대 RETRY_MAX_ATTEMPTS까지 재시도
        retry_targets = ("llm_images_sum", "llm_images_trans", "llm_tables_str", "llm_tables_unstr")
        is_retry_target = path.name.startswith(retry_targets)
        attempts = RETRY_MAX_ATTEMPTS if is_retry_target else 1

        resp_text = ""
        merged = template_output
        success = False
        last_reason = "empty_response"
        for attempt in range(1, attempts + 1):
            try:
                prompt = build_prompt(instruction, input_payload)
                resp_text = run_chat(model, tokenizer, prompt, image=image_obj)
            except Exception as exc:  # pragma: no cover
                log_error(f"file={path.name} id={entry_id} attempt={attempt} error=LLM call failed: {exc}")
                resp_text = ""
                last_reason = f"exception:{exc}"

            if resp_text:
                cleaned = clean_response_text(resp_text)
                candidate = extract_json(cleaned)
                merged, ok = validate_output(template_output, candidate)
                if ok and needs_image_detail_retry(path.name, merged):
                    ok = False
                    last_reason = "weak_image_summary"
                    log_error(
                        f"file={path.name} id={entry_id} attempt={attempt} error=weak_image_summary resp='{resp_text[:200]}'"
                    )
                if ok:
                    print(f"[출력성공] {path.name} id={entry_id} attempt={attempt}")
                    success = True
                    break
                last_reason = last_reason if last_reason else "invalid_output"
                if last_reason == "invalid_output":
                    log_error(
                        f"file={path.name} id={entry_id} attempt={attempt} error=invalid_output resp='{resp_text[:200]}'"
                    )
            else:
                last_reason = "empty_response"
                log_error(f"file={path.name} id={entry_id} attempt={attempt} error=empty_response")

        if not success:
            entry_status = "실패"
            ts = datetime.datetime.now().isoformat(timespec="seconds")
            FAILED_LOG.parent.mkdir(parents=True, exist_ok=True)
            with FAILED_LOG.open("a", encoding="utf-8") as f:
                f.write(
                    f"[{ts}] file={path.name} id={entry_id} attempts={attempts} reason={last_reason} "
                    f"resp_snippet='{(resp_text or '')[:200]}'\n"
                )
            print(f"[실패] {path.name} id={entry_id} attempts={attempts}")
        else:
            entry_status = "성공"

        result_entry = dict(entry)
        result_entry["output"] = merged
        result_entry["raw_response"] = resp_text
        results.append(result_entry)
        print(f"[{entry_status}] {path.name} id={entry_id}")
        # 중간 진행상황도 바로 저장
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
