#!/usr/bin/env python3
"""Batch QA with dense search from Qdrant and Qwen generation."""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
from pathlib import Path
from typing import List, Dict, Optional

import requests
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels


# ----- 사용자 조정용 상수 -----
# build_prompt 참고 
SYSTEM_PROMPT = (
    "너는 제철/제선/공정 운전 전문가다. 컨텍스트에 근거한 사실만 사용하여 답변하고, "
    "근거가 없으면 모른다고 답한다. 숫자/조건/단위/경계값은 원문 그대로 유지하며, "
    "수식과 각 고로(2고로, 3고로, 4고로 등)별 상이한 기준/값이 나오면 구분해 설명한다. "
    "추가 미사여구 없이 간결하게 사실만 전달한다."
)
DEFAULT_COLLECTION = "final_embeddings"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_TOKENS = 512


def embed_dense(text: str, model: str, url: str, timeout: float = 60.0) -> List[float]:
    resp = requests.post(
        f"{url.rstrip('/')}/api/embeddings",
        json={"model": model, "prompt": text},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    embedding = data.get("embedding")
    if not embedding:
        raise RuntimeError(f"Ollama response missing embedding: {data}")
    return embedding


def hybrid_search(
    client: QdrantClient,
    collection: str,
    dense_vec: List[float],
    qdrant_url: str,
    top_k: int,
) -> list[dict]:
    vector_named = qmodels.NamedVector(name="dense", vector=dense_vec)

    # 1) 최신 클라이언트: search
    try:
        results = client.search(
            collection_name=collection,
            query_vector=vector_named,
            limit=top_k,
            with_payload=True,
        )
    except Exception:
        results = None

    # 2) search_points (일부 버전)
    if results is None:
        try:
            results = client.search_points(
                collection_name=collection,
                query_vector=vector_named,
                limit=top_k,
                with_payload=True,
            ).points
        except Exception:
            results = None

    # 3) HTTP fallback (REST API)
    if results is None:
        import requests

        base = qdrant_url.rstrip("/")
        resp = requests.post(
            f"{base}/collections/{collection}/points/search",
            json={
                "vector": {"name": "dense", "vector": dense_vec},
                "limit": top_k,
                "with_payload": True,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json().get("result", [])
        results = data

    contexts = []
    for point in results:
        payload = getattr(point, "payload", {}) if hasattr(point, "payload") else point.get("payload", {}) or {}
        score = getattr(point, "score", None) if hasattr(point, "score") else point.get("score")
        contexts.append(
            {
                "score": score,
                "text": payload.get("text") or payload.get("content") or "",
                "placeholder": payload.get("placeholder"),
                "record_type": payload.get("record_type"),
                "source_file": payload.get("source_file"),
                "filename": payload.get("filename"),
                "page": payload.get("page"),
                "image_link": payload.get("image_link"),
                "placeholders": payload.get("placeholders") or {},
            }
        )
    return contexts


def fetch_placeholder_payload(
    client: QdrantClient, collection: str, placeholder_id: str, image_link: Optional[str] = None
) -> Optional[dict]:
    must_filters = [
        qmodels.FieldCondition(key="id", match=qmodels.MatchValue(value=placeholder_id)),
    ]
    if image_link:
        must_filters.append(qmodels.FieldCondition(key="image_link", match=qmodels.MatchValue(value=image_link)))
    flt = qmodels.Filter(must=must_filters)
    try:
        res, _ = client.scroll(collection_name=collection, scroll_filter=flt, limit=1, with_payload=True)
    except Exception:
        res = []
    if not res:
        return None
    payload = res[0].payload if hasattr(res[0], "payload") else res[0].get("payload", {}) or {}
    return payload or None


def build_prompt(question: str, contexts: list[dict]) -> str:
    ctx_lines = []
    for idx, ctx in enumerate(contexts, start=1):
        meta = []
        if ctx.get("record_type"):
            meta.append(f"종류: {ctx['record_type']}")
        if ctx.get("source_file"):
            meta.append(f"파일: {Path(ctx['source_file']).name}")
        prefix = f"[컨텍스트 {idx}] " + (" | ".join(meta) if meta else "")
        ctx_lines.append(prefix)
        ctx_lines.append(ctx.get("text", ""))
        ctx_lines.append("")
    ctx_block = "\n".join(ctx_lines).strip()
    system = SYSTEM_PROMPT
    user = (
        f"질문: {question}\n\n"
        "컨텍스트를 참고해 5문장 이내로 답변하라. 모르면 모른다고 말해라.\n\n"
        f"{ctx_block if ctx_block else '(컨텍스트 없음)'}"
    )
    return f"{system}\n\n{user}"


def generate(prompt: str, model: str, url: str, timeout: float = 120.0, temperature: float = DEFAULT_TEMPERATURE, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    resp = requests.post(
        f"{url.rstrip('/')}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("response", "").strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Dense QA over Qdrant using Ollama + Qwen.")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, help="Qdrant 컬렉션명")
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    parser.add_argument("--embed-model", default="snowflake-arctic-embed2", help="Ollama 임베딩 모델명")
    parser.add_argument("--llm-model", default="qwen2.5:14b-instruct", help="답변 생성 모델명 (Ollama)")
    parser.add_argument("--csv", required=True, type=Path, help="입력 CSV 경로")
    parser.add_argument("--out-csv", type=Path, help="결과 저장 CSV (기본: 입력 경로에 덮어쓰기)")
    parser.add_argument("--question-col", default="question", help="질문 컬럼명")
    parser.add_argument("--answer-col", default="answer", help="답변 컬럼명")
    parser.add_argument("--evidence-col", default="evidence", help="검색 컨텍스트를 저장할 컬럼명")
    parser.add_argument("--top-k", type=int, default=7, help="retrieval 개수 (dense-only)")
    args = parser.parse_args()

    rows = []
    per_row_elapsed: list[float] = []
    per_row_embed_ms: list[float] = []
    per_row_search_ms: list[float] = []
    per_row_gen_ms: list[float] = []
    with args.csv.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        qcol = args.question_col
        if qcol not in fieldnames:
            # BOM 등으로 필드명이 어긋난 경우 보정
            for name in fieldnames:
                if name and name.lstrip("\ufeff").strip().lower() == qcol.lower():
                    qcol = name
                    break
        if qcol not in fieldnames:
            raise SystemExit(f"CSV에 '{args.question_col}' 컬럼이 없습니다. 필드: {fieldnames}")
        rows = [row for row in reader]

    client = QdrantClient(url=args.qdrant_url)

    placeholder_lookup_cache: dict[tuple[str, Optional[str]], Optional[dict]] = {}

    for row in rows:
        question = (row.get(qcol) or "").strip()
        if not question:
            row[args.answer_col] = ""
            continue
        row_start = time.monotonic()
        embed_start = time.monotonic()
        dense_vec = embed_dense(question, model=args.embed_model, url=args.ollama_url)
        embed_ms = (time.monotonic() - embed_start) * 1000

        search_start = time.monotonic()
        contexts = hybrid_search(client, args.collection, dense_vec, args.qdrant_url, args.top_k)
        search_ms = (time.monotonic() - search_start) * 1000

        # placeholder 해소: 텍스트 내 {{ID}} 치환
        augmented = []
        ph_pattern = re.compile(r"\{\{([^{}#]+(?:#[^{}]+)?)\}\}")
        for ctx in contexts:
            text = ctx.get("text", "")
            placeholders_map = ctx.get("placeholders") or {}

            def replace_placeholder(match: re.Match) -> str:
                pid = match.group(1)
                if "#" in pid:
                    return match.group(0)
                image_link_hint = placeholders_map.get(pid) if isinstance(placeholders_map, dict) else None
                cache_key = (pid, image_link_hint)
                if cache_key in placeholder_lookup_cache:
                    fetched_payload = placeholder_lookup_cache[cache_key]
                else:
                    fetched_payload = fetch_placeholder_payload(client, args.collection, pid, image_link_hint)
                    placeholder_lookup_cache[cache_key] = fetched_payload
                if fetched_payload:
                    comp_type = (
                        fetched_payload.get("record_type")
                        or fetched_payload.get("component_type")
                        or ""
                    )
                    body = (
                        fetched_payload.get("original")
                        or fetched_payload.get("text")
                        or fetched_payload.get("content")
                        or ""
                    )
                    # 비어있거나 No Description이면 사용하지 않음
                    if not body or str(body).strip().lower() == "no description":
                        pid_upper = pid.upper()
                        if pid_upper.startswith("IMG"):
                            return "[이미지 있음]"
                        if pid_upper.startswith("TB"):
                            return "[테이블 있음]"
                        return "[참고 있음]"
                    label = "[참고]"
                    if "image" in str(comp_type):
                        label = "[이미지 참고]"
                    elif "table" in str(comp_type):
                        label = "[테이블 참고]"
                    if body:
                        return f"{label} {body}"
                # fallback: 적재 실패 시 플레이스홀더 존재만 표시
                pid_upper = pid.upper()
                if pid_upper.startswith("IMG"):
                    return "[이미지 있음]"
                if pid_upper.startswith("TB"):
                    return "[테이블 있음]"
                return "[참고 있음]"

            new_text = ph_pattern.sub(replace_placeholder, text)
            ctx = dict(ctx)
            ctx["text"] = new_text
            augmented.append(ctx)

        prompt = build_prompt(question, augmented)
        gen_start = time.monotonic()
        answer = generate(
            prompt, model=args.llm_model, url=args.ollama_url, temperature=DEFAULT_TEMPERATURE, max_tokens=DEFAULT_MAX_TOKENS
        )
        gen_ms = (time.monotonic() - gen_start) * 1000
        row[args.answer_col] = answer
        # 간단한 evidence 로그/저장
        ev_lines = []
        for idx, ctx in enumerate(augmented, start=1):
            meta = []
            if ctx.get("doc_folder"):
                meta.append(f"doc={ctx['doc_folder']}")
            if ctx.get("record_type"):
                meta.append(f"type={ctx['record_type']}")
            if ctx.get("source_file"):
                meta.append(f"file={Path(ctx['source_file']).name}")
            if ctx.get("score") is not None:
                meta.append(f"score={ctx['score']:.4f}")
            meta.append(f"t_embed_ms={embed_ms:.1f} t_search_ms={search_ms:.1f} t_gen_ms={gen_ms:.1f}")
            meta_part = " | ".join(meta)
            text_part = ctx.get("text", "")
            ev_lines.append(f"[{idx}] {meta_part}\n{text_part}")
        evidence_text = "\n\n".join(ev_lines)
        row[args.evidence_col] = evidence_text
        per_row_embed_ms.append(embed_ms)
        per_row_search_ms.append(search_ms)
        per_row_gen_ms.append(gen_ms)
        per_row_elapsed.append(time.monotonic() - row_start)

    fieldnames = list(rows[0].keys()) if rows else [qcol]
    # per-question total elapsed(sec) 컬럼 추가
    elapsed_col = "qa_elapsed_sec"
    embed_col = "qa_embed_ms"
    search_col = "qa_search_ms"
    gen_col = "qa_gen_ms"
    for col in (args.answer_col, args.evidence_col, embed_col, search_col, gen_col, elapsed_col):
        if col not in fieldnames:
            fieldnames.append(col)
    # Excel 호환을 위해 utf-8-sig로 BOM 포함 저장
    default_out = args.csv.with_name("output.csv")
    out_path = args.out_csv if args.out_csv else default_out
    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        if rows:
            for idx, row in enumerate(rows):
                if idx < len(per_row_elapsed):
                    row[elapsed_col] = f"{per_row_elapsed[idx]:.3f}"
                if idx < len(per_row_embed_ms):
                    row[embed_col] = f"{per_row_embed_ms[idx]:.1f}"
                if idx < len(per_row_search_ms):
                    row[search_col] = f"{per_row_search_ms[idx]:.1f}"
                if idx < len(per_row_gen_ms):
                    row[gen_col] = f"{per_row_gen_ms[idx]:.1f}"
                writer.writerow(row)
    total_elapsed = sum(per_row_elapsed)
    print(f"[DONE] Answers written to {out_path} (total_elapsed={total_elapsed:.2f}s, rows={len(rows)})")


if __name__ == "__main__":
    main()
