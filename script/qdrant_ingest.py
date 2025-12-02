#!/usr/bin/env python3
"""Ingest sanitized OCR output into Qdrant using Ollama embeddings."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import requests
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels


@dataclass(frozen=True)
class FileSpec:
    filename: str
    record_type: str
    content_field: str


FILE_SPECS: tuple[FileSpec, ...] = (
    FileSpec("tables_result.json", "table_summary", "summary"),
    FileSpec("text_result.json", "text", "text"),
)


def iter_records(base_dir: Path) -> Iterable[Dict[str, object]]:
    """Yield normalized records from every sanitized folder."""
    for doc_dir in sorted(base_dir.iterdir()):
        if not doc_dir.is_dir():
            continue
        for spec in FILE_SPECS:
            file_path = doc_dir / spec.filename
            if not file_path.exists():
                continue
            try:
                data = json.loads(file_path.read_text())
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON: {file_path}") from exc
            if not isinstance(data, list):
                raise ValueError(f"Expected list in {file_path}")
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                content = entry.get(spec.content_field)
                if not content:
                    continue
                context_text = str(content)
                metadata = {k: v for k, v in entry.items() if k != spec.content_field}
                yield {
                    "doc_folder": doc_dir.name,
                    "source_file": str(file_path),
                    "record_type": spec.record_type,
                    "content_field": spec.content_field,
                    "content": context_text,
                    "context": context_text,
                    "metadata": metadata,
                }


def embed_text(text: str, model: str, url: str, timeout: float = 120.0) -> List[float]:
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


def ensure_collection(client: QdrantClient, collection: str, vector_size: int) -> None:
    if client.collection_exists(collection):
        return
    client.create_collection(
        collection_name=collection,
        vectors_config=qmodels.VectorParams(size=vector_size, distance=qmodels.Distance.COSINE),
    )


def upsert_batch(
    client: QdrantClient,
    collection: str,
    items: List[Dict[str, object]],
    vectors: List[List[float]],
) -> None:
    ids = [str(uuid.uuid4()) for _ in items]
    payloads = []
    for item in items:
        payload = {k: v for k, v in item.items()}
        payloads.append(payload)
    client.upsert(
        collection_name=collection,
        points=qmodels.Batch(ids=ids, vectors=vectors, payloads=payloads),
    )


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Embed sanitized outputs into Qdrant.")
    parser.add_argument(
        "--base-dir",
        default="output/sanitize",
        type=Path,
        help="Base directory that contains sanitized folders.",
    )
    parser.add_argument(
        "--collection",
        default="sanitize_records",
        help="Qdrant collection name.",
    )
    parser.add_argument(
        "--qdrant-url",
        default=os.environ.get("QDRANT_URL", "http://localhost:6333"),
        help="Qdrant HTTP endpoint.",
    )
    parser.add_argument(
        "--ollama-url",
        default=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
        help="Ollama base URL.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OLLAMA_MODEL", "snowflake-arctic-embed2"),
        help="Embedding model to request from Ollama.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=32,
        help="Number of vectors to send per Qdrant upsert.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    base_dir = args.base_dir
    if not base_dir.exists():
        raise SystemExit(f"Base directory not found: {base_dir}")

    client = QdrantClient(url=args.qdrant_url)

    buffer: List[Dict[str, object]] = []
    vectors: List[List[float]] = []
    vector_size: Optional[int] = None

    for record in iter_records(base_dir):
        embedding = embed_text(record["content"], model=args.model, url=args.ollama_url)
        if vector_size is None:
            vector_size = len(embedding)
            ensure_collection(client, args.collection, vector_size)
        buffer.append(record)
        vectors.append(embedding)
        if len(buffer) >= args.batch_size:
            upsert_batch(client, args.collection, buffer, vectors)
            buffer.clear()
            vectors.clear()

    if buffer:
        if vector_size is None:
            raise RuntimeError("No embeddings were generated; nothing to upload.")
        ensure_collection(client, args.collection, vector_size)
        upsert_batch(client, args.collection, buffer, vectors)

    print("Ingestion complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
