#!/usr/bin/env python3
"""Batch QA over Qdrant-indexed sanitize results using Ollama embeddings + Qwen."""
from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
from typing import Iterable, List

import requests
from qdrant_client import QdrantClient


def embed(text: str, model: str, url: str, timeout: float = 60.0) -> List[float]:
    resp = requests.post(
        f"{url.rstrip('/')}/api/embeddings",
        json={"model": model, "prompt": text},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    embedding = data.get("embedding")
    if not embedding:
        raise RuntimeError(f"Ollama embeddings missing 'embedding': {data}")
    return embedding


def generate(prompt: str, model: str, url: str, timeout: float = 120.0) -> str:
    resp = requests.post(
        f"{url.rstrip('/')}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("response", "").strip()


def search_topk(
    client: QdrantClient,
    collection: str,
    vector: List[float],
    top_k: int,
) -> list[dict]:
    results = client.search(
        collection_name=collection,
        query_vector=vector,
        limit=top_k,
    )
    contexts = []
    for hit in results:
        payload = hit.payload or {}
        text = payload.get("content") or payload.get("context") or ""
        contexts.append(
            {
                "score": hit.score,
                "text": text,
                "doc_folder": payload.get("doc_folder"),
                "source_file": payload.get("source_file"),
                "record_type": payload.get("record_type"),
            }
        )
    return contexts


def build_prompt(question: str, contexts: Iterable[dict]) -> str:
    ctx_lines = []
    for idx, ctx in enumerate(contexts, start=1):
        meta = []
        if ctx.get("doc_folder"):
            meta.append(f"문서: {ctx['doc_folder']}")
        if ctx.get("record_type"):
            meta.append(f"종류: {ctx['record_type']}")
        if ctx.get("source_file"):
            meta.append(f"파일: {Path(ctx['source_file']).name}")
        prefix = f"[컨텍스트 {idx}] " + (" | ".join(meta) if meta else "")
        ctx_lines.append(prefix)
        ctx_lines.append(ctx.get("text", ""))
        ctx_lines.append("")
    ctx_block = "\n".join(ctx_lines).strip()
    system = (
        "너는 제철소 고로 운영/조업 전문가이다. 후배/사원에게 답변하듯 한국어로 설명하라. "
        "컨텍스트에서 확인된 사실만 사용하고, 없으면 모른다고 말한다. 숫자/조건/단위는 그대로 유지해라."
    )
    user = (
        f"질문: {question}\n\n"
        f"다음 컨텍스트를 참고해 5문장 이내로 답변하라. 불확실하면 모른다고 답변.\n\n"
        f"{ctx_block if ctx_block else '(컨텍스트 없음)'}"
    )
    return f"{system}\n\n{user}"


def read_questions(csv_path: Path, question_col: str) -> list[dict]:
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = [row for row in reader]
    if question_col not in reader.fieldnames:
        raise SystemExit(f"CSV에 '{question_col}' 컬럼이 없습니다. 필드: {reader.fieldnames}")
    return rows


def write_answers(csv_path: Path, rows: list[dict], answer_col: str) -> None:
    fieldnames = list(rows[0].keys())
    if answer_col not in fieldnames:
        fieldnames.append(answer_col)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch QA over Qdrant using Ollama embeddings + Qwen.")
    parser.add_argument("--collection", default="sanitize_records", help="Qdrant 컬렉션명")
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    parser.add_argument("--embed-model", default="snowflake-arctic-embed2", help="임베딩 모델명 (Ollama)")
    parser.add_argument("--llm-model", default="qwen2.5:3b-instruct", help="생성 모델명 (Ollama)")
    parser.add_argument("--csv", required=True, type=Path, help="질문이 담긴 CSV 경로")
    parser.add_argument("--question-col", default="question", help="질문 컬럼명")
    parser.add_argument("--answer-col", default="answer", help="답변 저장 컬럼명")
    parser.add_argument("--top-k", type=int, default=5, help="retrieval 개수")
    args = parser.parse_args()

    rows = read_questions(args.csv, args.question_col)
    client = QdrantClient(url=args.qdrant_url)

    for row in rows:
        question = row.get(args.question_col, "").strip()
        if not question:
            row[args.answer_col] = ""
            continue
        query_vec = embed(question, model=args.embed_model, url=args.ollama_url)
        contexts = search_topk(client, args.collection, query_vec, args.top_k)
        prompt = build_prompt(question, contexts)
        answer = generate(prompt, model=args.llm_model, url=args.ollama_url)
        row[args.answer_col] = answer

    write_answers(args.csv, rows, args.answer_col)
    print(f"[DONE] Answers written to {args.csv}")


if __name__ == "__main__":
    main()
