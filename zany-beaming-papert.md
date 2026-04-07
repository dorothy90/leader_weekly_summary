# Deep Mining PPT 점진적 개선 계획

## Context

현재 `deep_mining_ppt.py`는 카드 패킹 + 하드코딩 빌더 방식으로 PPT를 생성한다.
presenton 프로젝트 분석 결과, **테마 토큰화 + JSON 기반 레이아웃 + Pydantic IR**을 도입하면
현재 python-pptx 구조를 유지하면서도 확장성과 디자인 품질을 높일 수 있다.

## Phase 1: 테마 토큰화

### 변경 사항
- `deep_mining_ppt.py`의 `THEME` dict를 외부 JSON으로 분리
- `themes/` 디렉토리에 테마 파일 저장
- `ThemeResolver` 클래스로 테마 로드/적용

### 파일 변경
- **새 파일**: `themes/default.json`, `themes/dark_report.json` (예시 테마)
- **새 파일**: `deep_mining_theme.py` — ThemeResolver 클래스
- **수정**: `deep_mining_ppt.py` — THEME 상수 참조를 ThemeResolver로 교체
- **수정**: `deep_mining_schemas.py` — DeepMineRequest에 `theme` 필드 추가 (optional)

### 테마 JSON 구조 예시
```json
{
  "name": "Dark Report",
  "colors": {
    "bg_dark": "#1a1a2e",
    "bg_light": "#ffffff",
    "bg_section": "#f7f8fa",
    "accent": "#e94560",
    "text_primary": "#ffffff",
    "text_dark": "#2d3748",
    "text_muted": "#718096",
    "header_bg": "#2d3748",
    "header_font": "#ffffff"
  },
  "typography": {
    "font_title": 36,
    "font_subtitle": 20,
    "font_body": 16,
    "font_small": 12,
    "font_metric": 28,
    "font_name": "맑은 고딕"
  },
  "spacing": {
    "margin": 0.8,
    "line_spacing_body": 6,
    "line_spacing_bullet": 4
  }
}
```

## Phase 2: 레이아웃 JSON Schema

### 변경 사항
- 8개의 `_build_*_slide()` 함수를 JSON layout 정의 + 범용 렌더러로 교체
- 각 layout은 "어떤 요소를 어디에 배치할지" JSON으로 정의
- 새 레이아웃 추가 시 JSON 파일만 추가하면 됨

### 파일 변경
- **새 파일**: `layouts/title.json`, `layouts/summary.json`, `layouts/team_single.json`, `layouts/team_compare.json`, `layouts/topic.json`, `layouts/trend.json`, `layouts/metric.json`, `layouts/recommendation.json`, `layouts/appendix.json`
- **새 파일**: `deep_mining_layout.py` — LayoutRegistry + 범용 SlideRenderer
- **수정**: `deep_mining_ppt.py` — `_build_*_slide()` 함수들을 LayoutRegistry.render()로 교체, `cards_to_slide_defs()` 단순화

### 레이아웃 JSON 구조 예시 (team_single.json)
```json
{
  "id": "team_single",
  "name": "팀별 분석 (단독)",
  "description": "1개 팀의 상세 분석. bullets + 선택적 metric table",
  "slots": [
    {
      "id": "header",
      "type": "text_box",
      "content_field": "_static",
      "static_value": "팀별 핵심 분석",
      "position": {"left": "$margin", "top": 0.5, "width": "$content_w", "height": 0.8},
      "style": {"font_size": "$font_subtitle+4", "font_bold": true, "font_color": "$text_dark"}
    },
    {
      "id": "accent_line",
      "type": "shape",
      "shape_type": "rectangle",
      "position": {"left": "$margin", "top": 1.3, "width": 1.5, "height": 0.06},
      "style": {"fill_color": "$accent"}
    },
    {
      "id": "team_headline",
      "type": "text_box",
      "content_field": "headline",
      "position": {"left": "$margin", "top": 1.5, "width": "$content_w", "height": 0.6},
      "style": {"font_size": "$font_subtitle", "font_bold": true, "font_color": "$accent"}
    },
    {
      "id": "bullets",
      "type": "text_box",
      "content_field": "key_bullets",
      "position": {"left": "$margin", "top": 2.1, "width": "$content_w", "height": 2.5},
      "style": {"font_size": "$font_body", "font_color": "$text_dark", "line_spacing": 4}
    },
    {
      "id": "metric_table",
      "type": "table",
      "content_field": "metrics",
      "optional": true,
      "condition": "has_metrics",
      "position": {"left": "$margin", "top": 3.5, "width": "$content_w", "height": 1.5},
      "columns": ["지표", "값", "팀"],
      "style": {"header_color": "$header_bg", "header_font_color": "$header_font", "font_size": "$font_small"}
    }
  ],
  "background": {"color": "$bg_light"}
}
```

`$margin`, `$accent` 등은 ThemeResolver가 런타임에 치환.

## Phase 3: Pydantic IR 모델 (선택적, Phase 1-2 이후)

### 변경 사항
- slide_def dict를 Pydantic 모델로 타입 안전하게
- presenton의 PptxFontModel, PptxFillModel 등 참고
- `export_native.py`에서 dict 대신 Pydantic 모델 소비

### 파일 변경
- **새 파일**: `pptx_ir_models.py` — SlideIR, TextBoxElement, ShapeElement, TableElement 등
- **수정**: `deep_mining_layout.py` — dict 대신 IR 모델 반환
- **수정**: `pptdaddy/utils/export_native.py` — IR 모델 소비 (하위 호환 유지)

## Phase 4: LLM 레이아웃 선택 (선택적)

### 변경 사항
- Stage 4(Synthesis)에서 각 슬라이드의 `preferred_layout` 반환
- 현재 `PresentationOutline.preferred_visual`을 활성화하여 layout 선택에 반영
- LayoutSelector가 LLM 추천 + 콘텐츠 크기를 고려해 최적 layout 선택

### 파일 변경
- **수정**: `deep_mining.py` — SYNTHESIS_PROMPT에 layout 선택 지시 강화
- **수정**: `deep_mining_ppt.py` — outline의 preferred_visual을 layout 선택에 반영

## 구현 순서

```
Phase 1 (테마)     → Phase 2 (레이아웃)     → Phase 3 (IR)      → Phase 4 (LLM 선택)
  2~3시간              3~4시간                  2~3시간              1~2시간
  변경 최소             핵심 리팩터링              타입 안전             기능 확장
```

## 검증 방법

1. **기존 테스트 PPTX 비교**: `exports/deep_mining/test_4slides.pptx` 등과 결과 비교
2. **단독 테스트 스크립트**: dummy DeepMiningAnalysis 객체로 각 Phase별 테스트
3. **실제 파이프라인 테스트**: OpenSearch + 더미 데이터로 end-to-end 검증

## 핵심 파일 목록

| 파일 | 역할 | Phase |
|------|------|-------|
| `deep_mining_ppt.py` | 카드 분해 + 패킹 + 슬라이드 빌드 (수정) | 1, 2 |
| `deep_mining_schemas.py` | Pydantic 스키마 (theme 필드 추가) | 1 |
| `deep_mining_theme.py` | ThemeResolver (신규) | 1 |
| `themes/*.json` | 테마 정의 파일 (신규) | 1 |
| `deep_mining_layout.py` | LayoutRegistry + SlideRenderer (신규) | 2 |
| `layouts/*.json` | 레이아웃 정의 파일 (신규) | 2 |
| `pptdaddy/utils/export_native.py` | PPTX 렌더러 (유지) | - |
| `deep_mining.py` | LangGraph 파이프라인 (Phase 4에서 수정) | 4 |
