# Category Wiki Builder

`category_wiki_builder.py`는 기존 주간·월간 `wiki_builder.py`와 분리된 분류축 Wiki 생성기다.

## 데이터 흐름

```text
combined.txt
  → embed_vectordb.py (chunk + embedding 1회)
  → weekly_mail
  → mail_agendas (기존 agenda 추출 단계가 생성, vector 없음)
  → integrated_wiki_builder.py
      → LOTCD 서술형 Wiki
      → Tech 서술형 Wiki
      → Domain 서술형 Wiki
      → category_wiki_pages (Markdown 문서, vector 없음)
  → /api/knowledge/wiki/pages/...
  → Web Wiki Reader
```

통합 Builder는 기존 `mail_agendas`만 읽으며 agenda를 추출하지 않는다. 인용 근거의 `source_doc_ids`가 `weekly_mail`에 존재하는지는 저장 전에 검증하지만, 메일을 chunk하거나 embedding을 조회·생성하지 않는다. 임베딩은 계속 `embed_vectordb.py`만 담당한다.

## 실행

실제 taxonomy와 승인된 LLM endpoint를 사용하는 운영 실행:

```bash
python integrated_wiki_builder.py \
  --week 2026-W28 \
  --allow-external-llm
```

더미 taxonomy로 구조 검증:

```bash
python integrated_wiki_builder.py \
  --week 2026-W28 \
  --allow-external-llm \
  --allow-dummy-taxonomy
```

저장 없이 같은 생성·검증 경로를 확인하려면 `--dry-run`을 사용한다:

```bash
python integrated_wiki_builder.py \
  --week 2026-W28 \
  --allow-external-llm \
  --dry-run
```

기존 `category_wiki_builder.py` 명령과 결정적 생성 경로는 전환 기간 동안 계속 사용할 수 있다.

## 주간 파이프라인 연결

```env
ENABLE_CATEGORY_WIKI=true
KNOWLEDGE_LLM_DATA_POLICY_ACK=true
KNOWLEDGE_TAXONOMY_PATH=/path/to/taxonomy.json
```

`run_pipeline.py`는 `embed_vectordb.process_all()` 완료 후 통합 Category Wiki Builder를 실행한다. 생성 순서는 하위 근거를 먼저 확정하는 **LOTCD → Tech → Domain**이다. 기존 주간·월간 요약과 `wiki_summaries`는 변경하지 않는다.

## 상태 누적

- 업무 `pending/open/investigating/planned/monitoring`은 진행 이슈로 다음 주에도 유지한다.
- `resolved/closed/completed/stable/positive/normal`만 해결 상태로 처리한다.
- 해결 후 비종료 상태가 다시 들어오면 `reopened`로 판단한다.
- 분류 `review_status=pending`은 이슈 상태에 합치지 않고 문서의 `분류 검토 필요`에 표시한다.
- 최신 문서 ID는 category ID이고, `{category_id}:{week}` ID로 주차 snapshot을 함께 보존한다.

현재 MVP의 `issue_id` fallback은 `target_paths + topic` 해시다. 운영에서 동일 LOTCD·동일 topic의 서로 다른 이슈가 자주 병행되면 별도 issue-linking review 단계를 추가해야 한다.
