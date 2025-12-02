# step1. math/heading sanitizing

`script/rule_cleanup.py`는 `<math>...</math>` 영역을 정규화한 뒤 `_rule_sanitized.md`를 `output/sanitize/<원본경로>/`에 생성합니다. 실행: `python3 script/rule_cleanup.py` (기본 대상 `output/chandra`).

## step 1-1. math 정규화 파이프라인 (핵심)

- 파서 기반 변환
  - `\frac{...}{...}` 중첩/미완성 → `[ (...)/(...) ]`
  - `\sqrt{...}` 중첩 → `√(...)`
  - `\left`/`\right` 제거(구분자 유지)
  - `\sum(...)` → `Σ(...)`, `\sum` → `Σ`
  - 숫자 아래/윗첨자 `_2` → `₂`, `^3`/`^{3}`/`^(3)` → `³`, `^-3`/`^{-3}`/`^(-3)` → `⁻³` (0~9, -1~-9)
- 라텍스 → 기호/평문 매핑 (발췌)
  - 섭씨/각도: `^{\circ}C` 변형 → `°C`, `^∘` → `°`, `^ \to C` → `℃`
  - 수학/그리스: `\alpha`~`σ`, `\uparrow`/`\downarrow`/`\leftrightarrow`/`\rightarrow`/`rightarrow`/`arrow`/`ightarrow`/`nightarrow`, `\pm`, `\times`/`times`/`imes`, `\cdot`, `\div`, `\approx`→`≒`, `\sim`, `\circ`, `\triangle`, `\square`, `\quad`→공백5
  - 화학식: `\text{CO}_2`→`CO₂`, `\text{H}_2\text{O}`→`H₂O`, `\mathrm{O}`→`O`, `_숫자` 아래첨자 자동
- 단위/기호 정규화 (공백 허용, `\text{}`/`\mathrm{}` 포함)
  - 길이/면적/체적: `\text{ m}^3`, `\text{m}^2`, `\mathrm{cm}^2`, `\text{Nm}^3` 등 → `m³`, `m²`, `cm²`, `Nm³`
  - 무게/조합: `\text{kg}`, `\text{kg/t-p}`, `\text{kg/cm}^2`, `\text{kg/cm}`, `\text{ton}`, `\text{Ton}`, `\text{ton-pig}`, `\text{Ton-Slag}`
  - 기타: `\text{kcal}`, `\text{kcal/g}`, `\text{cal/mol}`, `\text{g/1000}`, `\text{g/cm}^2`, `\text{g/Nm}^3`, `\text{Kcal/m}`, `\text{Gcal}`, `\text{KW}`, `\text{mmAq}`, `\text{Max}`, `\text{cm}`, `\text{Nm}` 등
  - 시간/속도: `\text{m/s}`, `\text{m/sec}`, `\text{sec}`, `\text{min}`, `\text{min/day}`, `\text{C/Hr}`, `\text{Hr}`, `\text{h}`
  - 기타 보정: `\text{\textbraceleft}`→`{`, `\text{\textbraceright}`→`}`, 빈 `\text{}` 제거, 최종적으로 `\text{...}`는 내용만 남기고 trim
- 후처리(순서 보장)
  1) \frac 재적용, 탭/`\\begin{aligned}`/`\\begin{array}{l}`/`\end*`/`\\`/`&amp;` 제거
  2) `h₂`→`H₂`, `\gammaCO`→`ηCO`, `\bullet`/`\\ <<`/리터럴 `\n` 제거
  3) `\boxed{...}`/`\underlined{...}` → 내용만
  4) `^ \to C` → `℃`
  5) `times`/`imes` → `×`, `arrow`/`ightarrow` 등 → `→`
  6) `<begin{aligned}foo></begin{aligned}foo>` → `foo`
  7) 남은 역슬래시 전체 삭제
- 산출물
  - 전체: `math_occurrences_keyval.json`
  - 블록 수식만: `blockmath_occurrences.json`
  - 결과 md: `output/sanitize/<원본경로>/<파일명>_rule_sanitized.md`
- 후처리 완료 후, `copy_components.py`로 components를 `output/sanitize`로 복사/붙여넣기

