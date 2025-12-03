# SUT Preprocess 파이프라인 안내

## 0. 한 줄 개요
`chandra -> sanitize -> extract -> llm -> final -> qdrant` 순서로 문서/테이블/이미지를 플레이스홀더화·요약·병합·임베딩합니다.

## 0-1. 디렉터리 개요
| 경로 | 내용 |
| --- | --- |
| core/sanitize/ | Sanitize 단계 스크립트 (rule 정리, components 복사/추출, text 정리, aggregate, run_pipeline 포함) |
| core/llm/ | LLM 페이로드 생성 및 실행(run_pipeline 포함) |
| core/finalize/ | 최종 JSON 생성 스크립트(run_pipeline 포함) |
| core/qdrant/ | Qdrant 적재/QA 스크립트(run_pipeline 포함) |
| output/chandra/ | 원본 md/컴포넌트 변환물 입력 위치 |
| output/sanitize/ | rule/placeholder/cleaned md와 components.json이 저장되는 중간 산출물 |
| output/extract/ | aggregate_components 결과 split JSON (tables/images/texts) |
| output/llm/ | LLM 입력/출력 JSON (`*_payload.json`, `*_result.json`) |
| output/final/ | 최종 병합 JSON (texts/tables/images) |
| logs/ | aggregate 등에서 남기는 보조 로그 (`components_total.json` 등) |
| .models/ | Qwen 모델 등 LLM 실행에 필요한 로컬 모델 경로 |
| docs/ | 파이프라인/실행/인수인계 문서 |

### 디렉터리 트리 (발췌)
```
.
├─ core
│  ├─ sanitize (rule_cleanup.py, copy_components.py, extract_*.py, aggregate_components.py, run_pipeline.py)
│  ├─ llm (llm_payloads.py, run_llm_payloads.py, run_pipeline.py)
│  ├─ finalize (finalize_jsons.py, run_pipeline.py)
│  └─ qdrant (qdrant_ingest.py, qdrant_qa.py, run_pipeline.py)
├─ output/{chandra,sanitize,extract,llm,final}
├─ logs/
├─ .models/ (Qwen 등)
└─ docs/
```

### 준비/확인 체크

- wsl2 환경이면 start.sh가 알아서 다 해줌
- 도커만 깔아두면 됨 (docker desktop)
 
```bash
chmod +x start.sh install_qdrant_ollama.sh
./start.sh # 실행
./install_qdrant_ollama.sh # 실행
```
- `output/chandra/**`에 md와 components 존재 여부 확인 후 sanitize 실행.


## 1. 단계 요약 (주요 커맨드)
- Sanitize 일괄: `python3 core/sanitize/run_pipeline.py --root output/sanitize`
  - 내부: `rule_cleanup` → `copy_components` → `extract_components` → `extract_texts` → `aggregate_components`
- LLM 준비/실행: `python3 core/llm/run_pipeline.py` (payload만 필요하면 `core/llm/llm_payloads.py`만 실행)
- Final 병합: `python3 core/finalize/run_pipeline.py`
- Qdrant 적재(+QA 옵션): `python3 core/qdrant/run_pipeline.py --base-dir output/final --collection final_embeddings --qdrant-url http://localhost:6333 --ollama-url http://localhost:11434 --embed-model snowflake-arctic-embed2 [--qa-csv input.csv --llm-model qwen2.5:14b-instruct --top-k 5]`

## 2. 산출물 위치
- `output/sanitize/**`: `_rule_sanitized.md`, `_placeholders.md`, `_cleaned.md`, `components.json`, `components/`
- `output/extract/`: `components_tables_str/unstr.json`, `components_images_sum/trans/formula.json`, `components_texts.json`
- `output/llm/`: `*_payload.json`, `*_result.json`
- `output/final/`: `texts_final.json`, `tables_str_final.json`, `tables_unstr_final.json`, `images_formula_final.json`, `images_sum_final.json`, `images_trans_final.json`
- Qdrant 컬렉션 기본: `final_embeddings` (임베딩 대상 필드 = `text`, 메타에도 `text` 보존)

## 3. LLM 요약 프롬프트 & 입력 필드
- 테이블(정형) payload `llm_tables_str_payload.json`
  - 입력: `row_flatten`(리스트), `filename`, `image_link`
  - 프롬프트 요지: “테이블 수치/조건을 4~8문장으로 요약, 단위 보존, 조업 기준/임계값 강조”
  - 출력: `table_summary` 배열
- 테이블(비정형) payload `llm_tables_unstr_payload.json`
  - 입력: `section_path`, `filename`, `image_link`
  - 프롬프트 요지: “불완전한 테이블 이미지를 보고 4~8문장으로 의미 재구성, 단위/조건 유지”
  - 출력: `table_summary` 배열
- 이미지 번역(IMG_TR) payload `llm_images_trans_payload.json`
  - 입력: `description`, `image_link`, `section_path`
  - 프롬프트 요지: “이 도표/그림/그래프는…으로 시작, description을 한국어 4~5문장으로 풀고 필요 시 축·눈금·흐름 보완, 단위·수치 보존, 키워드 5~15개”
  - 출력: `image_summary`, `image_keyword`
- 이미지 요약(IMG_SUM) payload `llm_images_sum_payload.json`
  - 입력: `image_link`, `context_before`, `context_after` (15토큰씩, 주석 제외)
  - 프롬프트 요지: “이미지 자체+앞뒤 컨텍스트 참고, 첫 문장 ‘이 도표/그림/그래프는…’, 축/단위/추세/흐름을 4~5문장, 키워드 5~15개”
  - 출력: `image_summary`, `image_keyword`

## 4. Final JSON 필드 규칙
- 모든 `text`는 프리픽스 `[문서: <filename>] [경로: <section_path>]` 후 본문.
- 테이블 STR: 기본 `id`(row_flatten), `id#<n>`(행별), `id#summary`(LLM 요약)
- 테이블 UNSTR: `id` 하나, LLM 요약 없으면 `full_html`, 그것도 없으면 `No Description`
- 이미지 SUM/TRANS: LLM 요약 없으면 `No Description`; FORMULA는 LLM 미사용, description을 text로 사용.

## 5. 참고
- LLM 모델: `.models/qwen/Qwen2.5-VL-7B-Instruct` (GPU 필요)
- 이미지 경로: `output/sanitize/<doc>/components/...`를 payload/result/final에 사용
- 동일 `id+filename`의 `_result.json` 항목이 있으면 `run_llm_payloads.py`가 스킵
- QA 시 placeholder `{{ID}}`가 등장하면 동일 컬렉션에서 해당 ID의 `text`를 조회해 컨텍스트에 합칩니다.
