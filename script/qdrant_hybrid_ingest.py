#!/usr/bin/env python3
"""Ingest *_result.json files into Qdrant with dense+hybrid (sparse) vectors."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional
import uuid

import requests
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels


def load_json(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def iter_records(base_dir: Path) -> Iterable[Dict[str, object]]:
    for doc_dir in sorted(base_dir.iterdir()):
        if not doc_dir.is_dir():
            continue
        doc_folder = doc_dir.name

        text_path = doc_dir / "text_result.json"
        if text_path.exists():
            for item in load_json(text_path):
                text = item.get("text", "")
                if not text:
                    continue
                yield {
                    "id": item.get("id") or item.get("page"),
                    "content": str(text),
                    "record_type": "text",
                    "source_file": str(text_path),
                    "doc_folder": doc_folder,
                }

        table_final = doc_dir / "table_final_result.json"
        if table_final.exists():
            for item in load_json(table_final):
                summary = item.get("summary") or ""
                if not summary:
                    continue
                yield {
                    "id": item.get("id"),
                    "content": str(summary),
                    "record_type": "table",
                    "source_file": str(table_final),
                    "doc_folder": doc_folder,
                }

        img_path = doc_dir / "image_final_result.json"
        if img_path.exists():
            for item in load_json(img_path):
                summary = item.get("summary", "")
                if not summary:
                    continue
                yield {
                    "id": item.get("id"),
                    "content": str(summary),
                    "record_type": "image",
                    "source_file": str(img_path),
                    "doc_folder": doc_folder,
                }


def embed_dense(text: str, model: str, url: str, timeout: float = 120.0) -> List[float]:
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


def ensure_collection(client: QdrantClient, name: str, dense_size: int) -> None:
    if client.collection_exists(name):
        return
    vectors_config = {"dense": qmodels.VectorParams(size=dense_size, distance=qmodels.Distance.COSINE)}
    client.create_collection(collection_name=name, vectors_config=vectors_config)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hybrid ingest (dense + sparse) into Qdrant.")
    parser.add_argument("--base-dir", default="output/sanitize", type=Path, help="sanitize 루트")
    parser.add_argument("--collection", default="sanitize_hybrid", help="Qdrant 컬렉션명")
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    parser.add_argument("--embed-model", default="snowflake-arctic-embed2", help="Ollama 임베딩 모델명")
    parser.add_argument("--batch-size", type=int, default=32)
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if not args.base_dir.exists():
        raise SystemExit(f"Base dir not found: {args.base_dir}")

    client = QdrantClient(url=args.qdrant_url)

    buffer: List[Dict[str, object]] = []
    dense_vectors: List[List[float]] = []
    sparse_vectors: List[Dict[str, List[float]]] = []
    dense_size: Optional[int] = None

    def flush_batch():
        nonlocal buffer, dense_vectors
        if not buffer:
            return
        ids = [str(uuid.uuid4()) for _ in buffer]
        client.upsert(
            collection_name=args.collection,
            points=qmodels.Batch(ids=ids, vectors={"dense": dense_vectors}, payloads=buffer),
        )
        buffer.clear()
        dense_vectors.clear()

    for record in iter_records(args.base_dir):
        content = (record.get("content") or "").strip()
        if not content:
            continue
        dense = embed_dense(content, model=args.embed_model, url=args.ollama_url)
        if dense_size is None:
            dense_size = len(dense)
            ensure_collection(client, args.collection, dense_size)
        buffer.append(record)
        dense_vectors.append(dense)

        if len(buffer) >= args.batch_size:
            flush_batch()

    if buffer:
        if dense_size is None:
            raise RuntimeError("No embeddings generated.")
        ensure_collection(client, args.collection, dense_size)
        flush_batch()

    print(f"[DONE] Ingested into collection '{args.collection}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
