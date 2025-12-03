#!/usr/bin/env python3
"""Batch QA with dense search from Qdrant and Qwen generation."""
from __future__ import annotations

import argparse
import csv
import os
import re
from pathlib import Path
from typing import List, Dict, Optional

import requests
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels
from enum import Enum


# ----- 사용자 조정용 상수 -----
# build_prompt 참고 
SYSTEM_PROMPT = (
    "너는 제철/제선/공정 운전 전문가다. 컨텍스트에 근거한 사실만 사용하여 답변하고, "
    "근거가 없으면 모른다고 답한다. 숫자/조건/단위/경계값은 원문 그대로 유지하라."
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
            }
        )
    return contexts


def fetch_placeholder_text(client: QdrantClient, collection: str, placeholder_id: str) -> Optional[str]:
    try:
        res = client.retrieve(collection_name=collection, ids=[placeholder_id], with_payload=True)
    except Exception:
        return None
    if not res:
        return None
    payload = res[0].payload if hasattr(res[0], "payload") else res[0].get("payload", {})
    text = payload.get("text") or payload.get("content")
    if text:
        return str(text)
    return None


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
    parser.add_argument("--top-k", type=int, default=5, help="retrieval 개수")
    args = parser.parse_args()

    rows = []
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

    for row in rows:
        question = (row.get(qcol) or "").strip()
        if not question:
            row[args.answer_col] = ""
            continue
        dense_vec = embed_dense(question, model=args.embed_model, url=args.ollama_url)
        contexts = hybrid_search(client, args.collection, dense_vec, args.qdrant_url, args.top_k)
        # placeholder 해소: 텍스트 내 {{ID}}가 있으면 해당 ID 벡터의 text를 추가 컨텍스트로 삽입
        augmented = []
        for ctx in contexts:
            text = ctx.get("text", "")
            add_lines = []
            for m in re.finditer(r"\{\{([^{}#]+(?:#[^{}]+)?)\}\}", text):
                pid = m.group(1)
                fetched = fetch_placeholder_text(client, args.collection, pid)
                if fetched:
                    add_lines.append(f"[{pid}] {fetched}")
            if add_lines:
                ctx = dict(ctx)
                ctx["text"] = text + "\n" + "\n".join(add_lines)
            augmented.append(ctx)

        prompt = build_prompt(question, augmented)
        answer = generate(prompt, model=args.llm_model, url=args.ollama_url, temperature=DEFAULT_TEMPERATURE, max_tokens=DEFAULT_MAX_TOKENS)
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
            meta_part = " | ".join(meta)
            text_part = ctx.get("text", "")
            ev_lines.append(f"[{idx}] {meta_part}\n{text_part}")
        evidence_text = "\n\n".join(ev_lines)
        row[args.evidence_col] = evidence_text

    fieldnames = list(rows[0].keys()) if rows else [qcol]
    for col in (args.answer_col, args.evidence_col):
        if col not in fieldnames:
            fieldnames.append(col)
    # Excel 호환을 위해 utf-8-sig로 BOM 포함 저장
    default_out = args.csv.with_name("output.csv")
    out_path = args.out_csv if args.out_csv else default_out
    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        if rows:
            writer.writerows(rows)
    print(f"[DONE] Answers written to {out_path}")


if __name__ == "__main__":
    main()
