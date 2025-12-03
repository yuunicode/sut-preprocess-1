# Pipeline Overview (Modules, I/O, Prerequisites)

## A. Sanitize & Aggregate (source → output/sanitize → output/extract)
- **입력**: `output/chandra/**.md` (원본 변환물)
- **모듈/명령** (모두 `core/sanitize/`):
  1. `python3 core/sanitize/rule_cleanup.py` → `_rule_sanitized.md` (math/heading 정규화)
  2. `python3 core/sanitize/copy_components.py` → `output/sanitize/**/components/` 동기화
  3. `python3 core/sanitize/extract_components.py --root output/sanitize` → `_placeholders.md` + `components.json` (table/image placeholder, `context_html` 없음)
  4. `python3 core/sanitize/extract_texts.py --root output/sanitize` → `_cleaned.md` 덮어쓰기 + `components.json`에 `texts` 추가
  5. `python3 core/sanitize/aggregate_components.py` → split JSON을 `output/extract/`에 생성 (`components_tables_str/unstr/images_sum/trans/formula/texts.json`, `logs/components_total.json`)
- **출력**: `output/sanitize/**/{stem}_rule_sanitized.md`, `{stem}_placeholders.md`, `{stem}_cleaned.md`, `components.json`, `output/extract/*.json`

## B. LLM 준비/실행 (output/extract → output/llm, GPU 권장)
- **전제**: A단계 완료. GPU 서버에서 실행 시 `output/extract`와 모델(.models/qwen/…)만 옮겨도 됨.
- **모듈/명령** (모두 `core/llm/`):
  1. `python3 core/llm/llm_payloads.py` → `output/llm/*_payload.json` (테이블/이미지 LLM 입력용)
  2. `python3 core/llm/run_llm_payloads.py` → `output/llm/*_result.json` (항목별 상태 로그, raw_response 포함; 동일 `id+filename` result 있으면 스킵)

## C. Final 생성 (extract + llm result → output/final)
- **전제**: `output/extract/*.json` + `output/llm/*_result.json`
- **모듈/명령**: `python3 core/finalize/finalize_jsons.py`
- **출력**: `output/final/` (요약 표)

| 파일 | id 구성 | text 내용 | 비고 |
| --- | --- | --- | --- |
| texts_final.json | `TEXT_xxx` | `[문서:][경로:]` 프리픽스 + 본문 | placeholder 매핑 포함 |
| tables_str_final.json | 기본 id, `id#<n>`(행별), `id#summary` | 프리픽스 + row_flatten / 행별 row_flatten / LLM summary(있을 때만) | 행·요약마다 별도 임베딩 가능 |
| tables_unstr_final.json | 기본 id만 | 프리픽스 + LLM summary, 없으면 `full_html` → 없으면 `No Description` | placeholder 포함 |
| images_formula_final.json | 기본 id | 프리픽스 + description | LLM 미사용 |
| images_sum_final.json | 기본 id | 프리픽스 + LLM summary, 없으면 `No Description` | keyword 포함 |
| images_trans_final.json | 기본 id | 프리픽스 + LLM summary, 없으면 `No Description` | keyword 포함 |

## D. Qdrant 적재 & QA
- **전제**: `output/final/*.json`
- **임베딩 적재 (dense-only)**:  
  `python3 core/qdrant/qdrant_hybrid_ingest.py --base-dir output/final --qdrant-url http://localhost:6333 --ollama-url http://localhost:11434 --embed-model snowflake-arctic-embed2 --batch-size 32`  
  - 컬렉션: `final_embeddings` 고정, `text` 필드 임베딩(+메타로 보존)
- **QA**:  
  `python3 core/qdrant/qdrant_hybrid_qa.py --csv input.csv --collection final_embeddings --qdrant-url http://localhost:6333 --ollama-url http://localhost:11434 --embed-model snowflake-arctic-embed2 --llm-model qwen2.5:14b-instruct --top-k 5`  
  - 상단 상수로 LLM 파라미터 조정: `SYSTEM_PROMPT`, `LLM_TEMPERATURE`, `LLM_TOP_P`, `LLM_MAX_TOKENS` (top_p는 샘플링 시만 의미)  
  - 컨텍스트에 `{{ID}}`가 있으면 동일 컬렉션에서 해당 ID 텍스트를 조회해 추가 컨텍스트로 사용  
  - 결과 CSV: `answer`, `evidence` 컬럼 추가 저장

## LLM 입력 필드 요약
- 테이블 STR: `row_flatten`, `filename`, `image_link` (출력: table_summary)
- 테이블 UNSTR: `section_path`, `filename`, `image_link` (출력: table_summary)
- 이미지 TR: `description`, `image_link` (출력: image_summary, image_keyword)
- 이미지 SUM: `image_link`, `context_before/after` (description 없음) (출력: image_summary, image_keyword)
- 이미지 FORMULA: LLM 미사용, `description`을 text로 사용
