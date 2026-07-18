# Category Wiki Builder

`category_wiki_builder.py`는 기존 주간·월간 `wiki_builder.py`와 분리된 분류축 Wiki 생성기다.

## 분류 우선 운영 절차

먼저 한 주차의 메일을 LOTCD Workbench에 적재한다. 외부 LLM을 사용하므로
데이터 전송 정책을 명시적으로 승인해야 하며, 별도 설정이 없으면
`z-ai/glm-4.7-flash` 모델을 사용한다.

```bash
KNOWLEDGE_LLM_DATA_POLICY_ACK=true python process_agendas.py \
  --week 2026-01 \
  --allow-external-llm
```

이어서 로컬 Workbench API와 화면을 실행한다.

```bash
python -m uvicorn knowledge_preview:app --host 127.0.0.1 --port 8002
```

위 두 명령은 분류 실행과 검토 화면만 준비한다. Wiki 생성과 embedding은
실행하지 않는다. 분류 결과를 검토·수정하고 주차를 승인한 뒤에만 아래의
Category Wiki Builder 절차를 별도로 실행한다.

## 데이터 흐름

```text
combined.txt
  → embed_vectordb.py (chunk + embedding 1회)
  → weekly_mail
  → category_wiki_builder.py
      → mail_agendas (구조화 문서, vector 없음)
      → category_wiki_pages (Markdown 문서, vector 없음)
  → /api/knowledge/wiki/pages/...
  → Web Wiki Reader
```

Builder는 `weekly_mail.embedding`을 조회하거나 다시 생성하지 않는다. `_source`에서 `text`, `week`, `team`, `mail_id`, `subject`, `part_index`만 읽는다.

## 실행

실제 taxonomy와 승인된 LLM endpoint를 사용하는 운영 실행:

```bash
python category_wiki_builder.py \
  --week 2026-28 \
  --allow-external-llm
```

더미 taxonomy로 구조 검증:

```bash
python category_wiki_builder.py \
  --week 2026-28 \
  --allow-external-llm \
  --allow-dummy-taxonomy
```

이미 `mail_agendas`가 준비된 경우 LLM 없이 결정적 Markdown 생성:

```bash
python category_wiki_builder.py \
  --week 2026-28 \
  --skip-agenda-extraction \
  --deterministic \
  --allow-dummy-taxonomy
```

## 주간 파이프라인 연결

```env
ENABLE_CATEGORY_WIKI=true
KNOWLEDGE_LLM_DATA_POLICY_ACK=true
KNOWLEDGE_TAXONOMY_PATH=/path/to/taxonomy.json
```

`run_pipeline.py`는 `embed_vectordb.process_all()` 완료 후 Category Wiki Builder를 실행한다. 기존 주간·월간 요약과 `wiki_summaries`는 변경하지 않는다.

## 상태 누적

- 업무 `pending/open/investigating/planned/monitoring`은 진행 이슈로 다음 주에도 유지한다.
- `resolved/closed/completed/stable/positive/normal`만 해결 상태로 처리한다.
- 해결 후 비종료 상태가 다시 들어오면 `reopened`로 판단한다.
- 분류 `review_status=pending`은 이슈 상태에 합치지 않고 문서의 `분류 검토 필요`에 표시한다.
- 최신 문서 ID는 category ID이고, `{category_id}:{week}` ID로 주차 snapshot을 함께 보존한다.

현재 MVP의 `issue_id` fallback은 `target_paths + topic` 해시다. 운영에서 동일 LOTCD·동일 topic의 서로 다른 이슈가 자주 병행되면 별도 issue-linking review 단계를 추가해야 한다.
