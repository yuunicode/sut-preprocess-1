# 확인할 점
TP-030-030-100 에서 테이블 파싱 도중 너무 복잡해서 기존 로직으로 파싱이 안되는 페이지 (5, 7) 있음. 어떻게 고려할 것인가? (지금은 complex_table로 아예 빼서 고려 안함)

# 준비
output/chandra에 processed된 폴더들을 넣어주세요.  
그 뒤에 chmod +x start.sh, ./start.sh 로 환경 세팅

# 디렉터리 개요

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


## 디렉터리 트리
```
.
├─ core
│  ├─ sanitize
│  │  ├─ rule_cleanup.py
│  │  ├─ copy_components.py
│  │  ├─ extract_components.py
│  │  ├─ extract_texts.py
│  │  ├─ aggregate_components.py
│  │  └─ run_pipeline.py
│  ├─ llm
│  │  ├─ llm_payloads.py
│  │  ├─ run_llm_payloads.py
│  │  └─ run_pipeline.py
│  ├─ finalize
│  │  ├─ finalize_jsons.py
│  │  └─ run_pipeline.py
│  └─ qdrant
│     ├─ qdrant_ingest.py
│     ├─ qdrant_qa.py
│     └─ run_pipeline.py
├─ output
│  ├─ chandra/        # 입력 MD + components
│  ├─ sanitize/       # *_rule_sanitized.md, *_placeholders.md, *_cleaned.md, components.json, components/
│  ├─ extract/        # split JSON (tables/images/texts)
│  ├─ llm/            # *_payload.json / *_result.json
│  └─ final/          # *_final.json
├─ logs/              # components_total.json 등 보조 로그
├─ .models/           # Qwen 모델 등 LLM 로컬 모델
└─ docs/              # 문서들 (overview, run_guide, handover_guide 등)
```
