"""Qdrant 임베딩 적재(+선택적 QA) 실행 헬퍼.

기본은 ingest만 수행하고, --qa-csv가 주어지면 QA도 연달아 수행한다.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def run_step(cmd: list[str]) -> None:
    print(f"[qdrant] running: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Qdrant ingest and optional QA")
    parser.add_argument("--base-dir", default="output/final", help="final JSON 위치")
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--embed-model", default="snowflake-arctic-embed2")
    parser.add_argument("--batch-size", default="32")
    parser.add_argument("--collection", default="final_embeddings")
    parser.add_argument("--qa-csv", help="지정 시 ingest 후 QA까지 수행")
    parser.add_argument("--llm-model", default="qwen2.5:14b-instruct", help="QA용 LLM 모델")
    parser.add_argument("--top-k", default="5", help="QA 검색 top-k")
    args = parser.parse_args()

    ingest_cmd = [
        sys.executable,
        "core/qdrant/qdrant_ingest.py",
        "--base-dir",
        args.base_dir,
        "--qdrant-url",
        args.qdrant_url,
        "--ollama-url",
        args.ollama_url,
        "--embed-model",
        args.embed_model,
        "--batch-size",
        args.batch_size,
        "--collection",
        args.collection,
    ]
    run_step(ingest_cmd)

    if args.qa_csv:
        qa_cmd = [
            sys.executable,
            "core/qdrant/qdrant_qa.py",
            "--csv",
            args.qa_csv,
            "--collection",
            args.collection,
            "--qdrant-url",
            args.qdrant_url,
            "--ollama-url",
            args.ollama_url,
            "--embed-model",
            args.embed_model,
            "--llm-model",
            args.llm_model,
            "--top-k",
            args.top_k,
        ]
        run_step(qa_cmd)

    print("[qdrant] done")


if __name__ == "__main__":
    main()
