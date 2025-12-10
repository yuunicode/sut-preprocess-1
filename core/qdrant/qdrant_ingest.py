#!/usr/bin/env python3
"""Ingest output/final/*_final.json files into Qdrant with dense vectors."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional
import uuid
import time

import requests
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels
from enum import Enum


class Collection(Enum):
    FINAL = "final_embeddings"


DEFAULT_COLLECTION = Collection.FINAL.value
DEFAULT_DISTANCE = "cosine"
DEFAULT_HNSW_M = 16
DEFAULT_HNSW_EF_CONSTRUCT = 100
DEFAULT_ON_DISK = False
DISTANCE_CHOICES = {"cosine", "dot", "euclid", "euclidean", "l2"}


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
                "original": item.get("original"),
            }
            # 메타데이터 보존
            for key in [
                "placeholder",
                "component_type",
                "image_link",
                "section_path",
                "filename",
                "page",
                "placeholders",
                "original",
            ]:
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


def resolve_distance(name: str) -> qmodels.Distance:
    name = (name or "").strip().lower()
    if name == "dot":
        return qmodels.Distance.DOT
    if name in {"l2", "euclid", "euclidean"}:
        return qmodels.Distance.EUCLID
    return qmodels.Distance.COSINE


def ensure_collection(
    client: QdrantClient,
    name: str,
    dense_size: int,
    distance: str = "cosine",
    hnsw_m: int = DEFAULT_HNSW_M,
    hnsw_ef_construct: int = DEFAULT_HNSW_EF_CONSTRUCT,
    on_disk: bool = DEFAULT_ON_DISK,
) -> None:
    if client.collection_exists(name):
        return
    vectors_config = {
        "dense": qmodels.VectorParams(
            size=dense_size,
            distance=resolve_distance(distance),
            hnsw_config=qmodels.HnswConfigDiff(m=hnsw_m, ef_construct=hnsw_ef_construct),
            on_disk=on_disk,
        )
    }
    client.create_collection(collection_name=name, vectors_config=vectors_config)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest final JSONs into Qdrant (dense embeddings).")
    parser.add_argument("--base-dir", default="output/final", type=Path, help="final JSON 루트")
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    parser.add_argument("--embed-model", default="snowflake-arctic-embed2", help="Ollama 임베딩 모델명")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, help="Qdrant 컬렉션명 (기본: final_embeddings)")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument(
        "--distance",
        default=DEFAULT_DISTANCE,
        choices=sorted(DISTANCE_CHOICES),
        help="벡터 거리 함수 (기본: cosine)",
    )
    parser.add_argument(
        "--hnsw-m",
        type=int,
        default=DEFAULT_HNSW_M,
        help=f"HNSW m (기본: {DEFAULT_HNSW_M})",
    )
    parser.add_argument(
        "--hnsw-ef-construct",
        type=int,
        default=DEFAULT_HNSW_EF_CONSTRUCT,
        help=f"HNSW ef_construct (기본: {DEFAULT_HNSW_EF_CONSTRUCT})",
    )
    parser.add_argument(
        "--on-disk",
        action="store_true",
        default=DEFAULT_ON_DISK,
        help="벡터를 디스크에 저장 (기본: 메모리)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    base_collection = args.collection or DEFAULT_COLLECTION
    distance_name = (args.distance or DEFAULT_DISTANCE).lower()
    # suffix 규칙: cosine + 기본 HNSW + on_disk False + final_embeddings 은 그대로, 나머지는 suffix 부여
    use_defaults = (
        base_collection == DEFAULT_COLLECTION
        and distance_name == DEFAULT_DISTANCE
        and args.hnsw_m == DEFAULT_HNSW_M
        and args.hnsw_ef_construct == DEFAULT_HNSW_EF_CONSTRUCT
        and args.on_disk == DEFAULT_ON_DISK
    )
    suffix_parts: list[str] = []
    if distance_name != DEFAULT_DISTANCE:
        suffix_parts.append(distance_name)
    if args.hnsw_m != DEFAULT_HNSW_M or args.hnsw_ef_construct != DEFAULT_HNSW_EF_CONSTRUCT:
        suffix_parts.append(f"m{args.hnsw_m}-ef{args.hnsw_ef_construct}")
    if args.on_disk:
        suffix_parts.append("disk")
    if suffix_parts and not use_defaults:
        args.collection = f"{base_collection}_{'_'.join(suffix_parts)}"
    else:
        args.collection = base_collection
    if not args.base_dir.exists():
        raise SystemExit(f"Base dir not found: {args.base_dir}")

    client = QdrantClient(url=args.qdrant_url)

    buffer: List[Dict[str, object]] = []
    dense_vectors: List[List[float]] = []
    sparse_vectors: List[Dict[str, List[float]]] = []
    dense_size: Optional[int] = None
    processed = 0
    start_ts = time.monotonic()
    embed_time_total = 0.0
    upsert_time_total = 0.0

    def make_point_id(rec: Dict[str, object]) -> str | int:
        """
        Qdrant point id 생성: 동일 id를 여러 문서에서 재사용해도 충돌하지 않도록
        record_type/id/placeholder/filename/image_link/section_path/page를 모두 포함해 UUID5를 만든다.
        """
        parts = [
            rec.get("record_type") or "",
            rec.get("id") or rec.get("placeholder") or "",
            rec.get("filename") or "",
            rec.get("image_link") or "",
            rec.get("section_path") or "",
            rec.get("page") or "",
        ]
        key = "||".join(str(p) for p in parts)
        # UUID string을 입력해도 동일 결과가 나오도록 한번 더 UUID5로 감싼다.
        return str(uuid.uuid5(uuid.NAMESPACE_URL, key))

    def flush_batch():
        nonlocal buffer, dense_vectors, upsert_time_total
        if not buffer:
            return
        # point ID는 payload 기반으로 생성한 UUID/int 사용
        ids: List[str | int] = []
        for rec in buffer:
            ids.append(make_point_id(rec))
        flush_start = time.monotonic()
        client.upsert(
            collection_name=args.collection,
            points=qmodels.Batch(ids=ids, vectors={"dense": dense_vectors}, payloads=buffer),
        )
        upsert_time_total += (time.monotonic() - flush_start)
        buffer.clear()
        dense_vectors.clear()

    for record in iter_records(args.base_dir):
        content = (record.get("text") or "").strip()
        if not content:
            continue
        embed_start = time.monotonic()
        dense = embed_dense(content, model=args.embed_model, url=args.ollama_url)
        embed_time_total += (time.monotonic() - embed_start)
        if dense_size is None:
            dense_size = len(dense)
            ensure_collection(
                client,
                args.collection,
                dense_size,
                distance_name,
                args.hnsw_m,
                args.hnsw_ef_construct,
                args.on_disk,
            )
        buffer.append(record)
        dense_vectors.append(dense)
        processed += 1

        if len(buffer) >= args.batch_size:
            flush_batch()

    if buffer:
        if dense_size is None:
            raise RuntimeError("No embeddings generated.")
        ensure_collection(
            client,
            args.collection,
            dense_size,
            distance_name,
            args.hnsw_m,
            args.hnsw_ef_construct,
            args.on_disk,
        )
        flush_start = time.monotonic()
        flush_batch()
        upsert_time_total += (time.monotonic() - flush_start)

    elapsed = time.monotonic() - start_ts
    print(
        f"[DONE] Ingested {processed} points into collection '{args.collection}' "
        f"(distance={distance_name}, hnsw_m={args.hnsw_m}, ef_construct={args.hnsw_ef_construct}, on_disk={args.on_disk}) "
        f"elapsed={elapsed:.2f}s embed_time={embed_time_total:.2f}s upsert_time={upsert_time_total:.2f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
