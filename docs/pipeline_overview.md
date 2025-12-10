# Pipeline Overview (Modules, I/O, Prerequisites)

## A. Sanitize & Aggregate (source → output/sanitize → output/extract)
- **입력**: `output/chandra/**.md` (원본 변환물)
- **모듈/명령** (모두 `core/sanitize/`):
  1. `python3 core/sanitize/rule_cleanup.py` → `_rule_sanitized.md` (math/heading 정규화)
  2. `python3 core/sanitize/copy_components.py` → `output/sanitize/**/components/` 동기화
  3. `python3 core/sanitize/extract_components.py --root output/sanitize` → `_placeholders.md` + `components.json` (table/image placeholder, `context_html` 없음)
  4. `python3 core/sanitize/extract_texts.py --root output/sanitize [--strict-headings]` → `_cleaned.md` 덮어쓰기 + `components.json`에 `texts` 추가  
     - `--strict-headings`(2025-12-08 추가): 숫자 헤더(`1.`, `4.7`, `4.7.2`)만 섹션으로 인정하고 부모 접두어 일관성(`4.7` → `4.7.1/4.7.2`)을 체크. 비숫자 헤더(`가.`, `1)`)는 본문으로 내려감.
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
  - 텍스트 청크 옵션(2025-12-08 추가): `--chunk-size N --chunk-overlap M`으로 `texts_final.json`을 추가 분할할 수 있음. `{{ID}}` placeholder는 청크 경계에서 끊지 않으며 `placeholders` 매핑은 서브 청크가 그대로 상속.
  - 이미지 번역/요약 후처리: 요약이 비어 있으면 적재에서 제외하고, 이미지 번역에 금지 문자(중국어/키릴/아랍권) 포함 시 원본 description으로 대체. 이미지 요약/번역은 `image_summary`가 리스트면 이어붙여 사용.
  - 이미지 번역 `image_summary`는 문자열, 이미지 요약은 문장 리스트(`image_keyword` 미사용).
  - 테이블/이미지 첫 번째 항목 건너뛰기: 테이블 STR/UNSTR은 파일별 첫 테이블을 LLM payload에서 제외.

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
  `python3 core/qdrant/qdrant_ingest.py --base-dir output/final --qdrant-url http://localhost:6333 --ollama-url http://localhost:11434 --embed-model snowflake-arctic-embed2 --batch-size 32`  
  - 컬렉션: `final_embeddings` 고정, 벡터 거리함수는 cosine 기본, `text` 필드 임베딩(+메타 보존)
- **QA**:  
  `python3 core/qdrant/qdrant_qa.py --csv input.csv --collection final_embeddings --qdrant-url http://localhost:6333 --ollama-url http://localhost:11434 --embed-model snowflake-arctic-embed2 --llm-model qwen2.5:14b-instruct --top-k 7`  
  - dense 검색 7개 그대로 사용(확장/재정렬 없음)  
  - 상단 상수로 LLM 파라미터 조정: `SYSTEM_PROMPT`, `LLM_TEMPERATURE`, `LLM_TOP_P`, `LLM_MAX_TOKENS` (top_p는 샘플링 시만 의미)  
  - 컨텍스트에 `{{ID}}`가 있으면 그때그때 컬렉션에서 조회해 치환(사전 캐시 없음)  
  - 결과 CSV: `answer`, `evidence` 컬럼 추가 저장(임베딩/검색/생성 소요 ms 포함)

## LLM 입력 필드 요약 (2025-12-08 업데이트)
- 테이블 STR: `row_flatten`, `filename`, `image_link` (출력: table_summary) — 파일별 첫 테이블은 payload에서 제외
- 테이블 UNSTR: `section_path`, `filename`, `image_link`, `context_before/after`(각 20토큰, `{{...}}`는 다른 테이블/이미지 존재 의미) (출력: table_summary)
- 이미지 TR: `description`, `image_link` (출력: image_summary 문자열, `image_keyword` 미사용)
- 이미지 SUM: `image_link`, `context_before/after` (description 없음) (출력: image_summary 리스트, `image_keyword` 미사용)
- 이미지 FORMULA: LLM 미사용, `description`을 text로 사용

## LLM 프롬프트/튜닝 포인트
| 파일 | 대상 | 입력 필드 | 출력 | 프롬프트 특징 | 조정 가능한 상수 |
| --- | --- | --- | --- | --- | --- |
| `core/llm/llm_payloads.py` (`TABLE_STR_INSTRUCTIONS`) | 테이블 정형 | row_flatten, filename, image_link | table_summary | 4~8문장, 단위·조건 보존, 조업 기준/임계값 강조 | 없음(프롬프트 직접 수정) |
| `core/llm/llm_payloads.py` (`TABLE_UNSTR_INSTRUCTIONS`) | 테이블 비정형 | section_path, filename, image_link | table_summary | 이미지 기반 재구성, 4~8문장, 단위·조건 유지 | 없음(프롬프트 직접 수정) |
| `core/llm/llm_payloads.py` (`IMAGE_TRANS_INSTRUCTIONS`) | 이미지 번역(IMG_TR) | description, image_link, section_path | image_summary(문자열) | ‘이 도표/그림/그래프는’ 시작, 4~5문장, 단위·수치 보존, 키워드 미사용 | 없음(프롬프트 직접 수정) |
| `core/llm/llm_payloads.py` (`IMAGE_SUM_INSTRUCTIONS`) | 이미지 요약(IMG_SUM) | image_link, context_before, context_after | image_summary(문장 리스트) | 컨텍스트 15토큰, 축/단위/추세/흐름 4~5문장, 키워드 미사용 | 없음(프롬프트 직접 수정) |
| `core/llm/run_llm_payloads.py` | LLM 생성 파라미터 | payload JSON | result JSON | Qwen2.5-VL 실행, 이미지 로드 | `MAX_NEW_TOKENS`, `REPETITION_PENALTY`, `NO_REPEAT_NGRAM_SIZE` 상단 상수 |
| `core/qdrant/qdrant_qa.py` | QA 생성 | 검색 컨텍스트 + placeholder 해석 | CSV(answer/evidence) | 시스템 프롬프트 기반 QA | 상단 상수: `SYSTEM_PROMPT`, `LLM_TEMPERATURE`, `LLM_TOP_P`, `LLM_MAX_TOKENS` (top_p는 sampling 시 의미) |

### 추가 변형사항 (2025-12-08)
- 이미지 번역은 `image_summary` 문자열만 사용, 금지 문자(중국어/키릴/아랍권) 포함 시 원본 description으로 fallback, 비어 있으면 적재 제외.
- 이미지 요약은 `image_summary` 리스트를 이어붙여 사용, 빈 값이면 적재 제외, `image_keyword` 미사용.
- 테이블 비정형은 컨텍스트 20토큰을 사용하며, 컨텍스트의 `{{...}}`는 다른 테이블/이미지 존재를 의미.
- Qdrant QA는 placeholder를 인라인 치환(이미지→`[이미지 참고]`, 테이블→`[테이블 참고]`, 실패 시 `[이미지 있음]/[테이블 있음]`), dense 7개 검색만 사용(확장/재정렬 없음).
