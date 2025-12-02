#!/usr/bin/env python3
"""Ingest output/final/*_final.json files into Qdrant with dense vectors."""
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
from enum import Enum


class Collection(Enum):
    FINAL = "final_embeddings"


def load_json(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def iter_records(base_dir: Path) -> Iterable[Dict[str, object]]:
    mapping = {
        "texts_final.json": "text",
        "tables_str_final.json": "table_str",
        "tables_unstr_final.json": "table_unstr",
        "images_formula_final.json": "image_formula",
        "images_sum_final.json": "image_sum",
        "images_trans_final.json": "image_trans",
    }
    for fname, rtype in mapping.items():
        fpath = base_dir / fname
        if not fpath.exists():
            continue
        for item in load_json(fpath):
            raw_text = item.get("text", "")
            if isinstance(raw_text, list):
                text = "\n".join(str(x) for x in raw_text if x is not None)
            else:
                text = str(raw_text or "")
            if not text.strip():
                continue
            rec = {
                "id": item.get("id") or str(uuid.uuid4()),
                "record_type": rtype,
                "source_file": str(fpath),
                "text": text,
            }
            # 메타데이터 보존
            for key in ["placeholder", "component_type", "image_link", "section_path", "filename", "page"]:
                if key in item:
                    rec[key] = item.get(key)
            yield rec


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
    parser = argparse.ArgumentParser(description="Ingest final JSONs into Qdrant (dense embeddings).")
    parser.add_argument("--base-dir", default="output/final", type=Path, help="final JSON 루트")
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
            collection_name=Collection.FINAL.value,
            points=qmodels.Batch(ids=ids, vectors={"dense": dense_vectors}, payloads=buffer),
        )
        buffer.clear()
        dense_vectors.clear()

    for record in iter_records(args.base_dir):
        content = (record.get("text") or "").strip()
        if not content:
            continue
        dense = embed_dense(content, model=args.embed_model, url=args.ollama_url)
        if dense_size is None:
            dense_size = len(dense)
            ensure_collection(client, Collection.FINAL.value, dense_size)
        buffer.append(record)
        dense_vectors.append(dense)

        if len(buffer) >= args.batch_size:
            flush_batch()

    if buffer:
        if dense_size is None:
            raise RuntimeError("No embeddings generated.")
        ensure_collection(client, Collection.FINAL.value, dense_size)
        flush_batch()

    print(f"[DONE] Ingested into collection '{Collection.FINAL.value}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
