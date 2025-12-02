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


class FinalLookup:
    """Load table/image final summaries for placeholder resolution."""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.cache: Dict[str, Dict[str, str]] = {}

    def _load_doc(self, doc_folder: str) -> Dict[str, str]:
        if doc_folder in self.cache:
            return self.cache[doc_folder]
        table_map: Dict[str, str] = {}
        image_map: Dict[str, str] = {}
        doc_path = self.base_dir / doc_folder
        t_path = doc_path / "table_final_result.json"
        i_path = doc_path / "image_final_result.json"
        if t_path.exists():
            try:
                for item in json.loads(t_path.read_text(encoding="utf-8")):
                    tid = item.get("id")
                    text = item.get("text") or item.get("summary")
                    if tid and text:
                        table_map[tid] = str(text)
            except Exception:
                pass
        if i_path.exists():
            try:
                for item in json.loads(i_path.read_text(encoding="utf-8")):
                    iid = item.get("id")
                    text = item.get("text") or item.get("summary")
                    if iid and text:
                        image_map[iid] = str(text)
            except Exception:
                pass
        self.cache[doc_folder] = {"table": table_map, "image": image_map}
        return self.cache[doc_folder]

    def get_table(self, doc_folder: Optional[str], table_id: str) -> Optional[str]:
        if not doc_folder:
            return None
        data = self._load_doc(doc_folder)
        table_map = data.get("table", {})
        return table_map.get(table_id)

    def get_image(self, doc_folder: Optional[str], image_id: str) -> Optional[str]:
        if not doc_folder:
            return None
        data = self._load_doc(doc_folder)
        image_map = data.get("image", {})
        return image_map.get(image_id)


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
                "text": payload.get("content") or "",
                "doc_folder": payload.get("doc_folder"),
                "record_type": payload.get("record_type"),
                "source_file": payload.get("source_file"),
            }
        )
    return contexts


def build_prompt(question: str, contexts: list[dict]) -> str:
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
        "너는 제철/제선/공정 운전 전문가다. 컨텍스트에 근거한 사실만 사용하여 답변하고, "
        "근거가 없으면 모른다고 답한다. 숫자/조건/단위/경계값은 원문 그대로 유지하라."
    )
    user = (
        f"질문: {question}\n\n"
        "컨텍스트를 참고해 5문장 이내로 답변하라. 모르면 모른다고 말해라.\n\n"
        f"{ctx_block if ctx_block else '(컨텍스트 없음)'}"
    )
    return f"{system}\n\n{user}"


def generate(prompt: str, model: str, url: str, timeout: float = 120.0) -> str:
    resp = requests.post(
        f"{url.rstrip('/')}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("response", "").strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Dense QA over Qdrant using Ollama + Qwen.")
    parser.add_argument("--collection", default="sanitize_hybrid", help="Qdrant 컬렉션명")
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
    parser.add_argument("--base-dir", default="output/sanitize", type=Path, help="final result가 있는 sanitize 루트")
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
    resolver = FinalLookup(args.base_dir)

    for row in rows:
        question = (row.get(qcol) or "").strip()
        if not question:
            row[args.answer_col] = ""
            continue
        dense_vec = embed_dense(question, model=args.embed_model, url=args.ollama_url)
        contexts = hybrid_search(client, args.collection, dense_vec, args.qdrant_url, args.top_k)
        # placeholder 해소
        augmented = []
        for ctx in contexts:
            text = ctx.get("text", "")
            doc_folder = ctx.get("doc_folder")
            add_lines = []
            for m in re.finditer(r"\{\{(TABLE_[0-9]+)(?:#summary)?\}\}", text):
                base_id = m.group(1)
                val = resolver.get_table(doc_folder, base_id) or resolver.get_table(doc_folder, f"{base_id}#summary")
                if val:
                    add_lines.append(f"[TABLE {base_id}] {val}")
            for m in re.finditer(r"\{\{(IMG_(?:SUM|TR)_[0-9]+)\}\}", text):
                img_id = m.group(1)
                val = resolver.get_image(doc_folder, img_id)
                if val:
                    add_lines.append(f"[IMAGE {img_id}] {val}")
            if add_lines:
                ctx = dict(ctx)
                ctx["text"] = text + "\n" + "\n".join(add_lines)
            augmented.append(ctx)

        prompt = build_prompt(question, augmented)
        answer = generate(prompt, model=args.llm_model, url=args.ollama_url)
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
