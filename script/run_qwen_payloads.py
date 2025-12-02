#!/usr/bin/env python3
"""Use Qwen2.5-VL-7B-Instruct to process payloads and produce result JSON."""
from __future__ import annotations

import argparse
import datetime
import json
import re
from pathlib import Path
from typing import Iterable, Tuple
from bs4 import BeautifulSoup

import torch
from transformers import AutoTokenizer, AutoModelForVision2Seq, BitsAndBytesConfig
from huggingface_hub import snapshot_download
DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "output" / "sanitize"
DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[1] / ".models" / "qwen" / "Qwen2.5-VL-7B-Instruct"
DEFAULT_MODEL_ID = "Qwen/Qwen2.5-VL-7B-Instruct"
TABLE_MODEL_DIR = DEFAULT_MODEL_DIR
TABLE_MODEL_ID = DEFAULT_MODEL_ID
LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
LLM_LOG = LOG_DIR / "qwen_runs.log"
TABLE_MAX_NEW_TOKENS = 2048
UNSTRUCTURED_TABLE_INSTRUCTIONS = (
    "당신은 산업 공정 문서 전용 테이블 보정 전문가입니다.\n\n"
    "아래는 HTML을 정규화한 JSON 형태의 테이블입니다. 이 구조는 rowspan/colspan이 손실된 상태이며, "
    "빈 셀(\"\"), 잘못된 위치에 있는 셀, 계층형 헤더가 모두 무너진 비정상적인 상태입니다.\n\n"
    "임무:\n"
    "1) 테이블의 원래 구조를 복원하고\n"
    "2) multi-level column header를 재조립하고\n"
    "3) row header / section header를 올바르게 배치하며\n"
    "4) 틀린 위치의 값을 맞는 위치로 이동시키고\n"
    "5) 빈 셀(\"\")은 필요할 경우 적절한 값으로 채워 넣고\n"
    "6) 최종적으로 \"정상적인 직사각형 테이블\" 형태로 재구성하세요.\n\n"
    "응답 형식(JSON만): {\"fixed_table_html\": \"<table>…</table>\", \"is_fixed\": true}"
)


def load_payload(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else [data]


def save_result(path: Path, items: Iterable[dict]) -> None:
    path.write_text(json.dumps(list(items), ensure_ascii=False, indent=2), encoding="utf-8")


def extract_json_blob(text: str) -> str | None:
    matches = list(re.finditer(r"\{.*\}", text, re.DOTALL))
    if not matches:
        return None
    # 마지막 JSON 블록을 사용해 중간 끊김/중복 출력에 덜 민감하도록 함
    return matches[-1].group(0)


def parse_response(response: str, fallback: dict) -> tuple[dict, bool]:
    """Parse JSON blob from response. Returns (data, is_valid_json)."""
    def strip_fence(text: str) -> str:
        if text.strip().startswith("```"):
            lines = text.strip().splitlines()
            # drop first line (``` 혹은 ```json)
            lines = lines[1:] if lines else lines
            # drop last fence
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            return "\n".join(lines)
        return text

    cleaned = strip_fence(response)
    blob = extract_json_blob(cleaned)
    if not blob:
        return fallback, False
    try:
        data = json.loads(blob)
        if isinstance(data, str):
            data = json.loads(data)
        if isinstance(data, dict):
            return data, True
    except json.JSONDecodeError:
        return fallback, False
    return fallback, False


def summarize_table_001(html: str) -> list[str]:
    """TABLE_001 전용 간단 요약: 헤더별 컬럼 값을 모아 key:value로 반환."""
    if not html:
        return ["TABLE_001: 테이블 HTML이 비어 있습니다."]
    try:
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table")
        if not table:
            return ["TABLE_001: 테이블을 찾지 못했습니다."]
        header_row = table.find("thead")
        headers: list[str] = []
        if header_row:
            headers = [th.get_text(strip=True) for th in header_row.find_all("th")]
        if not headers:
            first_tr = table.find("tr")
            headers = [cell.get_text(strip=True) for cell in first_tr.find_all(["th", "td"])] if first_tr else []
        body_rows = table.find("tbody").find_all("tr") if table.find("tbody") else table.find_all("tr")[1:]
        col_count = len(headers) if headers else max((len(r.find_all(["th", "td"])) for r in body_rows), default=0)
        cols: list[list[str]] = [[] for _ in range(col_count)]
        for row in body_rows:
            cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
            for idx in range(col_count):
                val = cells[idx] if idx < len(cells) else ""
                if val:
                    cols[idx].append(val)
        lines = []
        for idx in range(col_count):
            head = headers[idx] if idx < len(headers) else f"col{idx+1}"
            vals = ", ".join(cols[idx]) if cols[idx] else "(없음)"
            lines.append(f"{head}: {vals}")
        return lines or ["TABLE_001: 내용을 파싱하지 못했습니다."]
    except Exception as exc:
        return [f"TABLE_001 파싱 오류: {exc}"]


def compact_parsed_rows(parsed: dict | None) -> str:
    """parsed_table에서 비어 있지 않은 셀만 모아 행 단위 텍스트로 압축."""
    if not parsed:
        return ""
    cells = parsed.get("cells") or []
    is_header = parsed.get("is_header") or []
    lines = []
    for idx, row in enumerate(cells):
        vals = [v for v in row if v]
        if not vals:
            continue
        header_flag = False
        if idx < len(is_header):
            header_flag = any(is_header[idx])
        prefix = "HEADER" if header_flag else "Row"
        lines.append(f"{prefix} {idx+1}: " + " | ".join(vals))
    return "\n".join(lines)


def build_vl_prompt(prompt: str, image: str | None = None, html: str | None = None) -> list[dict]:
    contents = []

    # 1) 이미지 먼저
    if image:
        contents.append({"type": "image", "image": image})

    # 2) 테이블 HTML (원본)
    # 2) 테이블 HTML (텍스트 힌트로만 사용; bbox는 이미지 기준)
    if html:
        contents.append({
            "type": "text",
            "text": "[TEXT_HINT]\n" + html
        })

    # 3) LLM 명령 프롬프트
    contents.append({"type": "text", "text": prompt})

    return [
        {"role": "system", "content": [{"type": "text", "text": "결과는 JSON만 출력하세요."}]},
        {"role": "user", "content": contents},
    ]



def run_chat(
    model,
    tokenizer,
    prompt: str,
    image: str | None = None,
    html: str | None = None,
    max_new_tokens: int = 512,
    top_p: float | None = None,
    do_sample: bool | None = None,
) -> str:
    messages = build_vl_prompt(prompt, image=image, html=html)
    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(text, return_tensors="pt").to(model.device)
    gen_kwargs = {
        "max_new_tokens": max_new_tokens,
        "repetition_penalty": 1.3,
        "no_repeat_ngram_size": 4,
        "pad_token_id": tokenizer.eos_token_id,
    }
    if top_p is not None:
        gen_kwargs["top_p"] = top_p
    if do_sample is not None:
        gen_kwargs["do_sample"] = do_sample
    with torch.no_grad():
        outputs = model.generate(**inputs, **gen_kwargs)
    generated = outputs[0][inputs["input_ids"].shape[-1] :]
    return tokenizer.decode(generated, skip_special_tokens=True).strip()


def strip_html_text(html: str | None) -> str:
    if not html:
        return ""
    try:
        soup = BeautifulSoup(html, "html.parser")
        return soup.get_text(" ", strip=True)
    except Exception:
        return ""


def ensure_json_response(response: str, fallback: dict) -> dict:
    parsed, ok = parse_response(response, fallback)
    return parsed if ok else fallback


def normalize_component_path(path_str: str) -> str:
    if not path_str:
        return ""
    p = Path(path_str)
    if p.is_absolute():
        return f"components/{p.name}"
    # 이미 상대경로면 그대로
    return str(p)


def build_component_map(directory: Path) -> dict:
    comp_path = directory / "components.json"
    if not comp_path.exists():
        return {}
    data = json.loads(comp_path.read_text(encoding="utf-8"))
    mapping: dict[str, str] = {}
    for table in data.get("tables", []):
        tid = table.get("id")
        href = table.get("image_path") or table.get("table_image_path", "")
        if tid:
            mapping[tid] = normalize_component_path(href)
    for img in data.get("images_summary", []):
        iid = img.get("id")
        href = img.get("image", "")
        if iid:
            mapping[iid] = normalize_component_path(href)
    for img in data.get("images_translation", []):
        iid = img.get("id")
        href = img.get("image", "")
        if iid and iid not in mapping:
            mapping[iid] = normalize_component_path(href)
    return mapping


def process_tables(
    directory: Path,
    model,
    tokenizer,
    table_model,
    table_tokenizer,
    component_map: dict,
    doc_folder: str,
    log_entries: list[str],
    debug_llm: bool,
) -> tuple[int, list[dict]]:
    payload_path = directory / "tables_payload.json"
    payload = load_payload(payload_path)

    results: list[dict] = []
    missing: list[dict] = []

    # TABLE_001은 payload 없이 components.json을 직접 파싱해 요약을 생성
    comp_path = directory / "components.json"
    if comp_path.exists():
        try:
            comp = json.loads(comp_path.read_text(encoding="utf-8"))
            for tbl in comp.get("tables", []):
                if tbl.get("id") == "TABLE_001":
                    image_rel = normalize_component_path(tbl.get("table_image_path") or tbl.get("image_path", ""))
                    summary_lines = summarize_table_001(tbl.get("table_html", ""))
                    results.append(
                        {
                            "id": tbl.get("id", "TABLE_001"),
                            "section_path": tbl.get("section_path", ""),
                            "image_path": image_rel,
                            "page": tbl.get("page"),
                            "filename": tbl.get("filename", ""),
                            "summary": " / ".join(summary_lines),
                            "doc_folder": doc_folder,
                            "component_map": component_map,
                            "retry_count": 0,
                        }
                    )
                    break
        except Exception as exc:
            print(f"[WARN] TABLE_001 summary 생성 실패: {exc}")

    if not payload:
        save_result(directory / "tables_result.json", results)
        return len(results)
    for item in payload:
        task = item.get("task", "table_analysis")
        section_path = item.get("section_path", "")
        image_rel = normalize_component_path(item.get("image_path") or item.get("table_image_path", ""))
        image_ref = str((directory / image_rel).resolve()) if image_rel else ""
        filename = item.get("filename", "")
        page = item.get("page")
        original_html = item.get("original_html", "")
        is_structured = item.get("is_structured", True)
        parsed_table = item.get("parsed_table")
        parsed_table_text = item.get("parsed_table_text", "")

        # 평탄화 row (LLM 미사용)
        if task == "table_flatten" and item.get("ell_flatten"):
            result_id = item.get("id_sub") or item.get("id", "")
            results.append(
                {
                    "id": result_id,
                    "parent_id": item.get("parent_id", item.get("id", "")),
                    "section_path": section_path,
                    "image_path": image_rel,
                    "page": page,
                    "filename": filename,
                    "summary": item.get("summary", []),
                    "doc_folder": doc_folder,
                    "component_map": component_map,
                    "retry_count": 0,
                    "ell_flatten": True,
                }
            )
            continue

        # 구조 정상 테이블 요약 (3~5문장)
        if task == "table_summary":
            result_id = item.get("id_sub") or item.get("id", "")
            prompt = (
                "아래 파싱된 테이블을 읽고 3~5줄의 핵심 요약을 한국어 문장으로 작성하라. bullet은 사용하지 말 것.\n"
                "헤더만 나열하지 말고, 각 행/셀에 담긴 수치·조건·조치(Action)를 함께 설명하라. 테이블 내용의 수치/조건을 가능한 한 유지하라.\n"
                'JSON만 출력하고, 형식은 {"summary": "문장들"} 하나로 제한하라.\n'
                f"[파싱 테이블]\n{json.dumps(parsed_table, ensure_ascii=False)}\n"
                f"[텍스트 전개]\n{parsed_table_text or '(없음)'}"
            )
            fallback = {"summary": ""}
            parsed_resp, ok = parse_response("", fallback)
            retries = 0
            for attempt in range(1, 3):
                response = run_chat(
                    model,
                    tokenizer,
                    prompt,
                    image=None,
                    html=parsed_table_text,
                    max_new_tokens=256,
                )
                if debug_llm:
                    print(f"[DEBUG][{item.get('id_sub') or item.get('id')}] LLM raw response:\n{response}\n")
                parsed_resp, ok = parse_response(response, fallback)
                if response.strip() and ok and parsed_resp.get("summary"):
                    break
                if response.strip() and not ok:
                    parsed_resp = {"summary": response.strip()}
                    ok = True
                    break
                retries += 1
                log_entries.append(f"  [retry] table {item.get('id_sub') or item.get('id')} attempt {attempt} failed (summary)")
            summary_val = parsed_resp.get("summary", "")
            if isinstance(summary_val, list):
                if not summary_val:
                    summary_val = ["(정보 없음)"]
            elif isinstance(summary_val, str):
                summary_val = [summary_val] if summary_val.strip() else ["(정보 없음)"]
            else:
                summary_val = ["(정보 없음)"]
            result_entry = {
                "id": result_id,
                "parent_id": item.get("parent_id", item.get("id", "")),
                "section_path": section_path,
                "image_path": image_rel,
                "page": page,
                "filename": filename,
                "summary": summary_val,
                "row_flatten": item.get("row_flatten", []),
                "doc_folder": doc_folder,
                "component_map": component_map,
                "retry_count": retries,
            }
            results.append(result_entry)
            if summary_val == ["(정보 없음)"]:
                missing.append({"type": "table_summary", "id": result_id, "doc_folder": doc_folder})
            continue

        if not is_structured:
            cell_text = item.get("cell_text") or []
            cell_text_hint = "\n".join(cell_text) if cell_text else ""
            prompt = (
                "너는 제철·제선·고로 조업 문서의 테이블 해석 전문가이다.\n"
                "아래 테이블 이미지와 제공된 cell_text만 사용해 주요 조건·수치·단계·조치(Action)를 3~5문장으로 요약하라.\n"
                "cell_text에 있는 단어/수치/조건을 그대로 사용하고, 보이지 않거나 없는 내용은 만들지 마라.\n"
                "출력은 JSON 하나만, 형식: {\"summary\": [\"문장1\", \"문장2\", ...]}.\n"
                "불확실하거나 읽히지 않는 값은 건너뛰어도 된다.\n"
                f"[cell_text]\n{cell_text_hint or '(제공 없음)'}"
            )
            fallback = {"summary": []}
            parsed_resp, ok = parse_response("", fallback)
            retries = 0
            for attempt in range(1, 3):
                response = run_chat(
                    model,
                    tokenizer,
                    prompt,
                    image=image_ref,
                    html=None,
                    max_new_tokens=TABLE_MAX_NEW_TOKENS,
                    top_p=1.0,
                    do_sample=False,
                )
                if debug_llm:
                    print(f"[DEBUG][{item.get('id')}] LLM raw response:\n{response}\n")
                parsed_resp, ok = parse_response(response, fallback)
                if response.strip() and ok and parsed_resp.get("summary"):
                    break
                if response.strip() and not ok:
                    parsed_resp = {"summary": [response.strip()]}
                    ok = True
                    break
                retries += 1
                log_entries.append(f"  [retry] table {item.get('id')} attempt {attempt} failed (unstructured summary)")
            summary_val = parsed_resp.get("summary", [])
            if isinstance(summary_val, str):
                summary_val = [summary_val]
            if not summary_val:
                summary_val = ["(정보 없음)"]
            result_entry = {
                "id": item.get("id", ""),
                "section_path": section_path,
                "image_path": image_rel,
                "page": page,
                "filename": filename,
                "summary": summary_val,
                "doc_folder": doc_folder,
                "component_map": component_map,
                "retry_count": retries,
            }
            results.append(result_entry)
            if summary_val == ["(정보 없음)"]:
                missing.append({"type": "table_unstructured", "id": item.get("id", ""), "doc_folder": doc_folder})
            continue
        # 알 수 없는 task는 건너뜀
        log_entries.append(f"  [skip] table {item.get('id')} unknown task={task}")

    save_result(directory / "tables_result.json", results)
    return len(results), missing


def process_images(
    directory: Path,
    payload_name: str,
    output_name: str,
    model,
    tokenizer,
    label: str,
    component_map: dict,
    doc_folder: str,
    log_entries: list[str],
) -> Tuple[int, dict]:
    payload_path = directory / payload_name
    payload = load_payload(payload_path)
    if not payload:
        return 0, {}

    results: list[dict] = []
    category_counts: dict[str, int] = {}
    missing: list[dict] = []
    for item in payload:
        image_rel = normalize_component_path(item.get("image", ""))
        image_ref = str((directory / image_rel).resolve()) if image_rel else ""
        section = item.get("section_path") or item.get("section", "")
        parent_section = item.get("parent_section", "")
        if alt_lower == "equation-block snippet":
            block_html = item.get("raw_html", "")
        else:
            block_html = item.get("block_html", item.get("context_html", ""))
        alt_text = item.get("alt", "")
        alt_lower = alt_text.strip().lower()
        instructions = item.get("instructions", "")
        filename = item.get("filename", "")
        # 공통 서두: 섹션 맥락을 우선 고려하고, 반드시 "이 도표/그림은 … 대한 설명이다."로 시작
        base_header = (
            f"섹션 경로를 우선 고려하여 요약/번역하라. 반드시 '이 도표/그림은 어떤 것에 대한 설명이다.'로 시작하고, "
            f"필요하면 괄호 안에 영어 원문을 병기하라.\n"
            f"섹션 경로: {section or '미지정'}\n"
        )

        # equation 여부에 따른 시작 문구 지시
        start_note = ""
        if alt_lower != "equation-block snippet":
            start_note = "요약/번역 문장은 '이 그림은 …' 또는 '이 도표는 …'으로 시작하라.\n"

        if label == "summary":
            extra_note = ""
            if alt_lower == "complex-block snippet":
                extra_note = "복잡한 블록도라면 어떤 대상/목적을 설명하는지 중심으로 간결히 서술하라."
            prompt_header = f"{base_header}{start_note}{instructions}\n"
            if extra_note:
                prompt_header += extra_note + "\n"
            prompt = (
                f"{prompt_header}"
                "가능한 한 많은 세부 정보를 포함해 요약하되, 총 1~5문장으로 작성하라.\n"
                f"문단/주변 HTML(이미지 앞뒤 약간 포함):\n{block_html}\n"
                f"파일명: {filename}, 페이지: {item.get('page')}\n"
                f"이미지 경로: {image_ref}, alt: {alt_text}\n"
                'JSON 형식으로만 응답하세요. 예) {"summary": "...", "keyword": ["...", "..."]}'
            )
            response = run_chat(model, tokenizer, prompt, image=image_ref, max_new_tokens=256)
        else:
            extra_note = ""
            context_html = item.get("raw_html", "") if alt_lower == "equation-block snippet" else (item.get("context_html") or item.get("raw_html", ""))
            prompt_parts = [
                f"{base_header}{start_note}{instructions}",
                f"{extra_note}",
                "가능한 한 많은 세부 정보를 포함해 1~5문장으로 번역하라.",
            ]
            if context_html:
                prompt_parts.append(f"원문 HTML/문맥:\n{context_html}")
            prompt_parts.extend(
                [
                    f"파일명: {filename}, 페이지: {item.get('page')}",
                    f"이미지 경로: {image_ref}",
                    f"alt 설명: {alt_text}",
                    'JSON 형식으로만 응답하세요. 예) {"summary": "...", "keyword": ["...", "..."]}',
                ]
            )
            prompt = "\n".join(part for part in prompt_parts if part)
            response = run_chat(model, tokenizer, prompt, image=image_ref, max_new_tokens=256)
        fallback = {"summary": "", "keyword": []}
        parsed, ok = parse_response("", fallback)
        retries = 0
        for attempt in range(1, 3):
            response = run_chat(model, tokenizer, prompt, image=image_ref, max_new_tokens=256)
            parsed, ok = parse_response(response, fallback)
            if response.strip() and ok:
                break
            retries += 1
            log_entries.append(f"  [retry] {label} {item.get('id')} attempt {attempt} failed (empty/invalid JSON)")
        if not ok:
            log_entries.append(f"  [retry] {label} {item.get('id')} fallback used after {retries} attempts")
        if not isinstance(parsed.get("keyword"), list):
            parsed["keyword"] = []
        ordered: dict = {
            "id": item.get("id", ""),
            "section_path": section,
            "image_path": image_rel,
            "page": item.get("page"),
            "filename": filename,
            "summary": parsed.get("summary", ""),
            "keyword": parsed.get("keyword", []),
            "doc_folder": doc_folder,
            "component_map": component_map,
            "retry_count": retries,
        }
        ordered.update(
            {
                "section": section,
                "parent_section": parent_section,
                "alt": alt_text,
                "block_html": block_html,
                "is_checked": parsed.get("is_checked", False),
            }
        )
        results.append(ordered)
        if not parsed.get("summary"):
            missing.append({"type": f"image_{label}", "id": item.get("id", ""), "doc_folder": doc_folder})
        if label == "summary":
            category = item.get("alt", "").strip().lower() or "unknown"
            category_counts[category] = category_counts.get(category, 0) + 1
    save_result(directory / output_name, results)
    if label == "summary":
        return len(results), category_counts, missing
    return len(results), {}, missing


def has_model_files(model_dir: Path) -> bool:
    return (model_dir / "config.json").exists()


def log_run(entries: list[str]) -> None:
    if not entries:
        return
    for line in entries:
        print(line)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with LLM_LOG.open("a", encoding="utf-8") as log_file:
        log_file.write(f"[{timestamp}]\n")
        for line in entries:
            log_file.write(line + "\n")
        log_file.write("\n")


def log_missing(missing: list[dict]) -> None:
    if not missing:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "missing.log"
    with log_path.open("a", encoding="utf-8") as f:
        for item in missing:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate tables/images result JSON using Qwen2.5-VL-7B-Instruct.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="sanitize 디렉터리 (기본: output/sanitize)")
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR, help="이미지 요약/번역 Qwen7B 경로")
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID, help="이미지 요약/번역 HF ID (기본: Qwen/Qwen2.5-VL-7B-Instruct)")
    parser.add_argument("--table-model-dir", type=Path, default=TABLE_MODEL_DIR, help="테이블용 Qwen 경로 (기본: 7B)")
    parser.add_argument("--table-model-id", default=TABLE_MODEL_ID, help="테이블용 HF ID (기본: Qwen/Qwen2.5-VL-7B-Instruct)")
    parser.add_argument(
        "--table-quantization",
        choices=["bf16", "8bit", "4bit"],
        default="bf16",
        help="테이블용 모델 양자화 옵션 (기본 bf16)",
    )
    parser.add_argument(
        "--debug-table-llm",
        action="store_true",
        default=True,
        help="테이블 LLM 원문 응답을 stdout에 출력",
    )
    parser.add_argument("--dirs", nargs="*", type=Path, help="특정 디렉터리만 처리")
    parser.add_argument(
        "--tasks",
        nargs="+",
        choices=["tables", "image-summary", "image-translation"],
        help="실행할 작업만 선택 (기본: 모두)",
    )
    args = parser.parse_args()

    model_dir = args.model_dir
    table_model_dir = args.table_model_dir
    if not has_model_files(model_dir):
        raise SystemExit(
            f"모델 디렉터리 {model_dir} 에 config.json 을 찾을 수 없습니다. "
            "--model-dir 경로가 올바른 Qwen2.5-VL 체크포인트 루트인지 확인하세요."
        )
    if not has_model_files(table_model_dir):
        raise SystemExit(
            f"테이블 모델 디렉터리 {table_model_dir} 에 config.json 을 찾을 수 없습니다. "
            "--table-model-dir 경로가 올바른 Qwen2.5-VL 체크포인트 루트인지 확인하세요."
        )

    def load_vl(model_dir: Path, model_id: str, quantization: str | None = None):
        try:
            tok = AutoTokenizer.from_pretrained(str(model_dir), trust_remote_code=True)
        except Exception:
            print(
                f"[WARN] 토크나이저 로드에 실패했습니다. HF Hub에서 {model_id}를 다시 다운로드합니다."
            )
            snapshot_download(
                repo_id=model_id,
                local_dir=str(model_dir),
                local_dir_use_symlinks=False,
                resume_download=False,
                force_download=True,
            )
            tok = AutoTokenizer.from_pretrained(
                model_id,
                cache_dir=str(model_dir),
                trust_remote_code=True,
            )

        def load_once(local_path: Path | str, use_cache: bool = False):
            cfg = {}
            if quantization == "8bit":
                cfg["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
            elif quantization == "4bit":
                cfg["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True)
            else:
                cfg["dtype"] = torch.bfloat16 if torch.cuda.is_available() else torch.float32
            return AutoModelForVision2Seq.from_pretrained(
                local_path,
                device_map="auto",
                trust_remote_code=True,
                **cfg,
            )

        try:
            mdl = load_once(model_dir)
        except Exception:
            print(
                f"[WARN] 모델 로드에 실패했습니다. HF Hub에서 {model_id}를 다시 다운로드합니다."
            )
            snapshot_download(
                repo_id=model_id,
                local_dir=str(model_dir),
                local_dir_use_symlinks=False,
                resume_download=False,
                force_download=True,
            )
            mdl = load_once(model_id, use_cache=True)
        mdl.eval()
        return mdl, tok

    model, tokenizer = load_vl(model_dir, args.model_id, quantization=None)
    table_quant = args.table_quantization if args.table_quantization != "bf16" else None

    if (
        table_model_dir.resolve() == model_dir.resolve()
        and args.table_model_id == args.model_id
        and table_quant is None
    ):
        table_model, table_tokenizer = model, tokenizer
    else:
        table_model, table_tokenizer = load_vl(
            table_model_dir,
            args.table_model_id,
            quantization=table_quant,
        )

    directories = args.dirs or [path.parent for path in args.root.rglob("components.json")]
    if not directories:
        print("[WARN] components.json을 찾지 못했습니다.")
        return

    task_set = set(args.tasks) if args.tasks else {"tables", "image-summary", "image-translation"}

    all_missing: list[dict] = []
    for directory in directories:
        if not directory.exists():
            continue
        log_entries: list[str] = [f"Processing payloads under {directory}"]
        table_count = 0
        summary_count = translation_count = 0
        summary_categories = {}
        component_map = build_component_map(directory)
        doc_folder = directory.name

        if "tables" in task_set:
            table_count, miss_tables = process_tables(
                directory,
                table_model,
                table_tokenizer,
                table_model,
                table_tokenizer,
                component_map,
                doc_folder,
                log_entries,
                debug_llm=args.debug_table_llm,
            )
            log_entries.append(f"  tables done (items={table_count})")
            all_missing.extend(miss_tables)
        if "image-summary" in task_set:
            summary_count, summary_categories, miss_img_sum = process_images(
                directory,
                "images_summary_payload.json",
                "images_summary_result.json",
                model,
                tokenizer,
                "summary",
                component_map,
                doc_folder,
                log_entries,
            )
            log_entries.append(f"  image summaries done (items={summary_count})")
            all_missing.extend(miss_img_sum)
        if "image-translation" in task_set:
            translation_count, _, miss_img_tr = process_images(
                directory,
                "images_translation_payload.json",
                "images_translation_result.json",
                model,
                tokenizer,
                "translation",
                component_map,
                doc_folder,
                log_entries,
            )
            log_entries.append(f"  image translations done (items={translation_count})")
            all_missing.extend(miss_img_tr)
        if summary_categories:
            cat_str = ", ".join(f"{k}:{v}" for k, v in summary_categories.items())
            log_entries.append(f"    categories -> {cat_str}")
        log_run(log_entries)
    log_missing(all_missing)


if __name__ == "__main__":
    main()
