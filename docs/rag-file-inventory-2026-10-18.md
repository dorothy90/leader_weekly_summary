# RAG 작업 파일 목록 — 2026-10-18 전달본

범위: 2026-10-17–18 누적 미커밋 RAG 변경분. Git 기준 신규/수정이며 파일 생성일의 증명은 아닙니다.

기반 커밋: `5343ecabe8aed124e73b76e9b7fef652f76ece00`. `.env`, 실제 메일/대화 데이터, 임시 검증 출력, 추적 중인 Python 캐시 6개 변경은 제외합니다.

## 실행 코드 (16개)

- `app/api/dependencies.py` — 수정
- `app/api/main.py` — 수정
- `app/api/routes/chat.py` — 수정
- `app/api/static/quick-chat.html` — 신규
- `app/config/ai.py` — 수정
- `app/config/settings.py` — 수정
- `app/domain/chat.py` — 수정
- `app/domain/quick_rag.py` — 신규
- `app/graphs/detailed_rag.py` — 신규
- `app/graphs/fast_agent.py` — 신규
- `app/graphs/quick_rag.py` — 신규
- `app/llm/gateway.py` — 수정
- `app/llm/quick_rag.py` — 신규
- `app/persistence/conversations.py` — 수정
- `app/retrieval/embedding.py` — 수정
- `app/retrieval/quick_rag.py` — 신규

## 테스트 (12개)

- `tests/mail_rag/test_adaptive_fast_agent.py` — 신규
- `tests/mail_rag/test_ai_dependencies.py` — 수정
- `tests/mail_rag/test_answer_sufficiency.py` — 신규
- `tests/mail_rag/test_detailed_rag.py` — 신규
- `tests/mail_rag/test_embedding_routing.py` — 신규
- `tests/mail_rag/test_embedding_stability.py` — 신규
- `tests/mail_rag/test_fast_agent_loop.py` — 신규
- `tests/mail_rag/test_multi_source_demo_api.py` — 수정
- `tests/mail_rag/test_quick_llm_failures.py` — 신규
- `tests/mail_rag/test_quick_rag.py` — 신규
- `tests/mail_rag/test_quick_rag_integration.py` — 신규
- `tests/mail_rag/test_research_api.py` — 수정

## 문서·계획 (10개)

- `docs/detailed-rag.md` — 신규
- `docs/multi_source_demo.md` — 수정
- `docs/operations.md` — 수정
- `docs/quick-rag.md` — 신규
- `docs/rag-file-inventory-2026-10-18.md` — 신규
- `docs/rag-migration-guide-2026-10-18.md` — 신규
- `docs/superpowers/plans/2026-10-17-adaptive-fast-agent.md` — 신규
- `docs/superpowers/plans/2026-10-17-detailed-rag.md` — 신규
- `docs/superpowers/plans/2026-10-17-fast-agent-loop.md` — 신규
- `docs/superpowers/plans/2026-10-17-speed-first-rag.md` — 신규

## 개발용 데이터 도구 (1개)

- `scripts/seed_multi_source_opensearch.py` — 수정

총 39개 파일. 설명서는 `docs/rag-migration-guide-2026-10-18.md`. ZIP의 runtime-baseline은 기존 의존 모듈 참고본이며 오늘 변경한 파일 목록과 구분합니다.
