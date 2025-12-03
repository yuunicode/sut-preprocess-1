# 실행 가이드 (준비 → Sanitize → LLM → Final → Qdrant)

## 0) 초기 준비
- 실행 권한 부여: `chmod +x start.sh install_qdrant_ollama.sh`
- Qdrant/Ollama 도커 구동이 필요하면 `./start.sh` (필요 시) 후 `./install_qdrant_ollama.sh` 실행  
- 모든 초기설정 끝난 뒤 source .venv/bin/activate

## 1) Sanitize 단계 (CPU)
- 한 번에 실행: `python3 core/sanitize/run_pipeline.py --root output/sanitize`
  - 수동 실행 순서: rule_cleanup → copy_components → extract_components → extract_texts → aggregate_components
- 산출물: `output/sanitize/**/_rule_sanitized.md`, `_placeholders.md`, `_cleaned.md`, `components.json`, 그리고 `output/extract/*.json`

## 2) LLM 단계 (GPU 권장)
- 페이로드만 생성하려면: `python3 core/llm/llm_payloads.py` (→ `output/llm/*_payload.json`)
- GPU에서만 LLM 실행하려면: payload 파일과 모델 디렉터리(`.models/qwen/Qwen2.5-VL-7B-Instruct`)만 GPU 서버로 옮긴 뒤 `python3 core/llm/run_llm_payloads.py [payload들...]`
- 한 번에 실행하려면: `python3 core/llm/run_pipeline.py` (특정 payload만 돌리려면 `--payload output/llm/tables_str_payload.json` 식으로 반복 지정)
- 내부 순서
  1) 페이로드 생성: 테이블/이미지별 입력 필드 포함
  2) LLM 실행: `_result.json` 생성, 동일 `id+filename`이 이미 result에 있으면 스킵
     - 이미지 파일 경로 로드 여부를 상태 로그로 출력, raw_response 포함

## 3) Final JSON 생성 (CPU)
- 한 번에 실행: `python3 core/finalize/run_pipeline.py`
- 산출물: `output/final/*.json` (texts/tables/images 각각)

## 4) Qdrant 적재 & QA (CPU)
- 한 번에 실행: `python3 core/qdrant/run_pipeline.py --base-dir output/final --qdrant-url http://localhost:6333 --ollama-url http://localhost:11434 --embed-model snowflake-arctic-embed2 --batch-size 32 --collection final_embeddings`
  - QA까지 같이 돌리려면 `--qa-csv input.csv` 추가 (LLM 모델은 기본 `qwen2.5:14b-instruct`, 필요 시 `--llm-model`/`--top-k` 지정)
- 내부 순서: ingest → (옵션) QA
- 시스템 프롬프트/temperature/top_p/max_tokens은 QA 스크립트 상단 상수로 조정

## 5) 파이프라인 요약 (현재 경로 기준)
- Sanitize: `core/sanitize/*` (4단계)
- LLM 준비/실행: `core/llm/*`
- Final 병합: `core/finalize/finalize_jsons.py`
- Qdrant: `core/qdrant/*`
