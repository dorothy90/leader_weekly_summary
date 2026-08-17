# Multi-Source Agentic RAG 개발 지시서

## 1. 프로젝트 목표

현재 시스템은 사용자의 질문을 받아 OpenSearch를 1회 검색한 뒤 LLM이 답변하는 구조에 가깝다.

최종 목표는 다음과 같다.

- 사용자의 질문을 분석한다.
- 질문 해결에 필요한 데이터 소스를 자동으로 판단한다.
- 여러 OpenSearch 인덱스를 순차 또는 반복 검색한다.
- 검색 결과를 관찰하고 정보가 충분한지 판단한다.
- 부족하면 다른 인덱스 또는 다른 검색어로 재검색한다.
- 충분하면 검색 근거를 기반으로 최종 답변을 생성한다.
- 멀티턴 대화에서는 이전 검색 결과와 엔티티를 활용한다.
- 로컬 LLM 환경에서도 안정적으로 동작하도록 LLM의 자유도를 제한하고 결정 가능한 로직은 코드로 처리한다.

최종 구조는 아래와 같다.

```text
User
 ↓
Query Analyzer
 ↓
Source Discovery
 ↓
Search Planner
 ↓
Tool Executor
 ↓
Observation
 ↓
Judge
 ├── insufficient → Replanner → Tool Executor
 └── sufficient   → Final Answer
 ↓
Conversation Memory
```

---

## 2. 현재 데이터 소스

### 2.1 Domain Knowledge

OpenSearch index:

```text
syld_gpt
```

역할:

- 반도체 수율 관련 Domain Knowledge
- 공정 지식
- Defect / Yield / Process 관련 기술 지식
- 기술 용어 설명
- 원인 및 메커니즘 해석
- 메일/회의에서 발견한 내용을 기술적으로 보완

Agent Tool:

```text
search_domain_knowledge
```

---

## 2.2 Outlook Mail

Alias:

```text
ews-mail-active
```

Physical index:

```text
ews-mail-v1
```

Agent와 검색 서비스에서는 반드시 alias인 `ews-mail-active`를 사용한다.

Physical index 이름을 Agent 또는 비즈니스 로직에 직접 노출하지 않는다.

향후 아래와 같이 변경되어도 Agent 코드를 수정하지 않아야 한다.

```text
ews-mail-active
    ↓
ews-mail-v1

→ reindex

ews-mail-active
    ↓
ews-mail-v2
```

### 주요 필드

```text
employee_id          keyword   사용자/ACL
subscription_id      long      EWS 구독 ID
root_folder_id       keyword   루트 폴더 ID
root_folder_name               루트 폴더명
root_folder_path               루트 폴더 경로
folder_id                      메일 폴더 ID
folder_name                    메일 폴더명
folder_path                    메일 폴더 경로
source_id                      원본문서 ID
source_url                     원본 접근 URL
ews_item_id                    Exchange ID
change_key                     변경 버전
content_kind                   body / attachment
attachment_name                첨부파일명
content_hash                   콘텐츠 해시
generation                     문서 세대
chunk_index                    청크 순번
text                           메일/첨부파일 본문
embedding                      벡터 검색용, 기본 차원 4096
is_active                      최신 문서 여부
received_at                    수신일
modified_at                    수정일
sent_at                        발신일
```

Agent Tool:

```text
search_mail
```

메일 본문과 첨부파일은 별도 index가 아니라 `content_kind`로 구분한다.

```text
content_kind = body
content_kind = attachment
```

---

## 2.3 Outlook Calendar / Meeting

Alias:

```text
ews-calendar-active
```

Physical index:

```text
ews-calendar-v1
```

Agent와 검색 서비스에서는 반드시 alias인 `ews-calendar-active`를 사용한다.

### 주요 필드

```text
employee_id                 사용자/ACL
calendar_folder_id          일정 폴더 ID
calendar_item_id            일정 아이템 ID
source_id                   원본 ID
source_url                  원본 URL
change_key                  변경 버전
parent_event_id             이벤트 ↔ 첨부파일 연결
source_type                 항상 calendar
content_kind                event / attachment
start_at_utc                시작 시간
end_at_utc                  종료 시간
timezone                    일정 시간대
all_day                     종일 일정 여부
is_active                   활성 여부
is_cancelled                취소 여부
subject                     일정 제목, korean analyzer
location                    장소, korean analyzer
organizer_email             주최자
attendee_emails             참석자
attachment_id               첨부파일 ID
attachment_name             첨부파일명
content_hash                콘텐츠 해시
occurrence_id               반복 일정 발생 ID
series_master_id            반복 일정 원본 ID
generation                  문서 세대
status                      처리 상태
partial_processing_state    부분 처리 상태
chunk_index                 청크 순번
text                        일정/첨부파일 요약 텍스트
embedding                   벡터 검색용
modified_at                 수정일
```

일정 이벤트 문서와 첨부파일 요약 문서는 같은 index에 저장한다.

```text
content_kind = event
content_kind = attachment
```

이벤트와 첨부파일은 `parent_event_id`로 연결한다.

Agent Tool:

```text
search_calendar
```

추가 deterministic helper:

```text
expand_calendar_event
```

---

# 3. 핵심 설계 원칙

## 3.1 Physical index를 Agent에 노출하지 않는다

사용:

```text
ews-mail-active
ews-calendar-active
```

금지:

```text
ews-mail-v1
ews-calendar-v1
```

Physical index 교체가 Agent 코드 변경으로 이어지지 않게 한다.

---

## 3.2 ACL은 LLM이 결정하지 않는다

`employee_id`는 인증된 사용자 정보에서 backend가 강제로 주입한다.

Agent 또는 LLM이 검색 대상 employee_id를 임의로 지정할 수 없어야 한다.

모든 Mail / Calendar 검색에 공통으로 다음 filter를 적용한다.

```text
employee_id = authenticated_user.employee_id
```

Mail:

```text
is_active = true
```

Calendar:

```text
is_active = true
is_cancelled = false
```

사용자 prompt나 LLM 출력으로 이 조건이 제거되거나 변경되어서는 안 된다.

---

## 3.3 단일 Router 구조를 사용하지 않는다

금지 구조:

```text
Question
 ↓
LLM Router
 ↓
one index
 ↓
one search
 ↓
Answer
```

구현 구조:

```text
Question
 ↓
Analyze
 ↓
Plan
 ↓
Search
 ↓
Observe
 ↓
Judge
 ↓
필요 시 다른 Search
 ↓
Answer
```

복합 질문에서는 여러 Tool을 호출할 수 있어야 한다.

---

## 3.4 LLM보다 deterministic 로직을 우선한다

LLM이 할 일:

- 질문 의도 분석
- 정보 요구사항 파악
- 검색 계획 생성
- 검색 결과 관찰
- 다음 검색 필요 여부 판단
- 검색어 재작성
- 최종 답변 생성

코드가 할 일:

- ACL
- alias 선택
- 날짜 변환
- OpenSearch query 생성
- metadata filtering
- parent_event_id 연결
- chunk grouping
- chunk sorting
- 중복 제거
- max iteration
- 동일 검색 반복 방지
- event/attachment 확장

---

# 4. Agent Tool 설계

최초 구현에서는 3개의 Agent Tool과 1개의 deterministic helper를 사용한다.

```text
search_domain_knowledge
search_mail
search_calendar

expand_calendar_event
```

---

## 4.1 search_domain_knowledge

대상:

```text
syld_gpt
```

예상 interface:

```python
search_domain_knowledge(
    query: str,
    top_k: int = 5,
    filters: dict | None = None,
) -> SearchResult
```

검색 방식:

```text
BM25
+
Vector Search
+
Hybrid Fusion
+
Optional Reranking
```

용도 예:

```text
"Cell Leakage가 뭐야?"
"NAND 수율 저하의 기술적 원인은?"
"이 현상이 공정상 어떤 의미야?"
```

---

## 4.2 search_mail

대상:

```text
ews-mail-active
```

예상 interface:

```python
search_mail(
    query: str,
    employee_id: str,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    content_kinds: list[str] | None = None,
    attachment_name: str | None = None,
    top_k: int = 10,
) -> SearchResult
```

단, 실제 호출부에서는 `employee_id`를 LLM이 넘기지 않고 인증 context에서 backend가 주입하도록 구현한다.

기본 필터:

```text
employee_id = current_user
is_active = true
```

필요 시:

```text
content_kind = body
content_kind = attachment
```

검색 방식:

```text
Metadata Filter
+
BM25(text)
+
Vector Search(embedding)
+
Hybrid Fusion
+
Reranking
```

### 검색 결과 후처리

동일 `source_id`의 chunk가 여러 개 검색될 수 있다.

아래 순서로 문서를 재구성한다.

```text
Search Hits
 ↓
group by source_id
 ↓
deduplicate
 ↓
sort by chunk_index
 ↓
reconstruct source context
```

동일 첨부파일에 대해서도 가능한 한 중복 chunk를 제거한다.

---

## 4.3 search_calendar

대상:

```text
ews-calendar-active
```

예상 interface:

```python
search_calendar(
    query: str,
    employee_id: str,
    start_at_utc: datetime | None = None,
    end_at_utc: datetime | None = None,
    content_kinds: list[str] | None = None,
    organizer_email: str | None = None,
    attendee_emails: list[str] | None = None,
    top_k: int = 10,
) -> SearchResult
```

기본 필터:

```text
employee_id = current_user
is_active = true
is_cancelled = false
```

검색 대상:

```text
subject
location
text
organizer_email
attendee_emails
embedding
```

검색 유형:

```text
Event Search
Attachment Search
Hybrid Search
Metadata Filter
Date Filter
```

---

## 4.4 expand_calendar_event

이 기능은 LLM reasoning tool이 아니라 deterministic lookup에 가깝게 구현한다.

목적:

검색된 이벤트 또는 첨부파일의 `parent_event_id`를 기준으로 해당 회의 전체 context를 복원한다.

예상 interface:

```python
expand_calendar_event(
    event_id: str,
    employee_id: str,
) -> CalendarEventBundle
```

결과 예:

```json
{
  "event": {
    "calendar_item_id": "...",
    "subject": "...",
    "start_at_utc": "...",
    "end_at_utc": "...",
    "organizer_email": "...",
    "attendee_emails": []
  },
  "attachments": [
    {
      "attachment_id": "...",
      "attachment_name": "...",
      "text": "..."
    }
  ]
}
```

조회 규칙은 실제 schema 관계를 확인해서 구현하되 기본 개념은 다음과 같다.

```text
Event
  │
  └── parent_event_id
         │
         ├── Attachment A
         ├── Attachment B
         └── Attachment C
```

첨부파일 검색에서 먼저 hit가 발생한 경우에도 `parent_event_id`를 통해 부모 Event를 함께 가져온다.

이 기능은 Agent의 semantic 검색보다 우선해서 사용한다.

---

# 5. Query Analyzer

Node:

```text
query_analyzer
```

목적:

사용자의 질문을 검색 가능한 구조로 변환한다.

출력 예시:

```json
{
  "intent": "knowledge_query",
  "question_type": "multi_source",
  "entities": {
    "product": "NAND",
    "issue": "Cell Leakage",
    "person": "kim@example.com"
  },
  "time_range": {
    "expression": "지난주",
    "start": null,
    "end": null
  },
  "information_needs": [
    "관련 메일",
    "관련 회의",
    "회의에서 결정된 action"
  ]
}
```

질문 유형 예:

```text
general_chat
domain_knowledge
mail_search
calendar_search
multi_source
follow_up
```

---

# 6. 날짜 처리

LLM이 날짜를 최종 UTC timestamp로 직접 확정하지 않도록 한다.

예:

```text
"지난주"
"어제"
"이번주 월요일"
"지난달"
```

Query Analyzer는 시간 표현을 구조화하고 backend date resolver가 실제 범위를 계산한다.

```text
Natural Language Time
 ↓
Date Resolver
 ↓
User Timezone
 ↓
UTC
 ↓
OpenSearch Range Query
```

Calendar 필드:

```text
start_at_utc
end_at_utc
timezone
```

Mail 필드:

```text
received_at
sent_at
modified_at
```

---

# 7. Source Discovery

Node:

```text
source_discovery
```

초기 source catalog:

```yaml
sources:
  - name: domain_knowledge
    tool: search_domain_knowledge
    index: syld_gpt
    description: 반도체 수율, 공정, defect 및 사내 domain knowledge 검색

  - name: mail
    tool: search_mail
    index_alias: ews-mail-active
    description: 사용자가 받은 Outlook 메일 본문과 첨부파일 검색

  - name: calendar
    tool: search_calendar
    index_alias: ews-calendar-active
    description: Outlook 일정, 회의 이벤트 및 회의 첨부파일 요약 검색
```

현재 source 수가 적기 때문에 초기 버전에서는 별도의 `rag_index_catalog` OpenSearch index를 만들지 않아도 된다.

우선 static registry로 구현한다.

향후 source가 증가하면 `rag_index_catalog`를 별도 구축한다.

---

# 8. Search Planner

Node:

```text
planner
```

Planner는 모든 검색을 한 번에 고정하지 않는다.

최초 검색 계획만 만들고 Observation 이후 Replanner가 다음 단계를 정한다.

출력 예:

```json
{
  "goal": "NAND 수율 저하 관련 메일과 이후 회의 Action 확인",
  "next_action": {
    "tool": "search_mail",
    "query": "NAND 수율 저하",
    "reason": "관련 이슈가 언급된 메일부터 식별"
  }
}
```

잘못된 방식:

```json
{
  "steps": [
    "mail",
    "calendar",
    "domain",
    "calendar",
    "mail"
  ]
}
```

처럼 처음부터 모든 단계를 고정하지 않는다.

---

# 9. Tool Executor

Node:

```text
tool_executor
```

역할:

- Planner 또는 Replanner의 action 검증
- 허용된 Tool인지 검사
- 인증 context에서 employee_id 추가
- 날짜 범위 적용
- OpenSearch 검색 실행
- 결과 정규화
- 검색 기록 저장

허용 Tool 목록은 코드로 제한한다.

```python
ALLOWED_TOOLS = {
    "search_domain_knowledge",
    "search_mail",
    "search_calendar",
    "expand_calendar_event",
}
```

LLM이 임의의 index 이름이나 OpenSearch DSL을 직접 실행할 수 없게 한다.

---

# 10. Observation

Node:

```text
observation
```

검색 결과를 LLM이 판단하기 쉬운 구조로 정규화한다.

예:

```json
{
  "tool": "search_mail",
  "query": "NAND 수율 저하",
  "hit_count": 3,
  "results": [
    {
      "source_id": "...",
      "content_kind": "body",
      "received_at": "...",
      "text": "...",
      "score": 0.91
    }
  ]
}
```

Raw OpenSearch response 전체를 LLM에게 넘기지 않는다.

필요한 field만 축약하여 전달한다.

---

# 11. Judge

Node:

```text
judge
```

Judge가 판단할 것:

1. 현재 정보만으로 사용자 질문에 답할 수 있는가?
2. 질문에서 요구한 정보 항목이 모두 확보되었는가?
3. 다른 source 검색이 필요한가?
4. 동일 검색을 반복하려는 것은 아닌가?
5. Calendar hit가 있다면 event attachment 확장이 필요한가?
6. Domain Knowledge가 기술적 해석에 필요한가?
7. 검색 근거가 너무 약한가?

출력 예:

```json
{
  "sufficient": false,
  "reason": "관련 메일은 찾았지만 회의 결정사항이 없음",
  "missing_information": [
    "관련 회의",
    "회의 Action Item"
  ],
  "recommended_action": {
    "tool": "search_calendar",
    "query": "NAND Cell Leakage Yield"
  }
}
```

---

# 12. Replanner

Node:

```text
replanner
```

입력:

```text
original_question
query_analysis
search_history
retrieved_documents
judge_result
```

출력:

```json
{
  "next_action": {
    "tool": "search_calendar",
    "query": "NAND Cell Leakage Yield",
    "reason": "메일에서 언급된 이슈와 관련된 회의를 찾기 위함"
  }
}
```

같은 Tool + 같은 Query 조합을 반복하지 않도록 코드 레벨에서 block한다.

---

# 13. Calendar Event 자동 확장

`search_calendar` 결과에서 아래 상황이면 deterministic하게 `expand_calendar_event`를 실행한다.

### Case A

```text
content_kind = event
```

이고 사용자가:

```text
회의 내용
결정사항
Action
첨부자료
발표자료
무슨 얘기
```

를 요구한 경우.

### Case B

```text
content_kind = attachment
```

검색 hit가 나온 경우.

첨부파일 hit에서:

```text
parent_event_id
```

를 추출하고 부모 event 및 같은 event의 다른 attachment를 확장한다.

가능하면 이 판단은 간단한 규칙 + Query Analyzer의 information_need를 이용해서 LLM 호출 없이 처리한다.

---

# 14. LangGraph State

예상 State:

```python
class AgentState(TypedDict):
    question: str
    conversation_id: str

    employee_id: str

    intent: str
    query_analysis: dict

    candidate_sources: list[str]

    current_action: dict | None
    search_history: list[dict]

    retrieved_documents: list[dict]
    current_observation: dict | None

    judge_result: dict | None

    iteration_count: int

    entities: dict
    unresolved_information: list[str]

    final_answer: str | None
```

`employee_id`는 LLM prompt에 불필요하면 직접 노출하지 않는다.

---

# 15. LangGraph 최종 구조

```text
START
  │
  ▼
query_analyzer
  │
  ├── general_chat
  │       │
  │       ▼
  │     answer
  │       │
  │       ▼
  │      END
  │
  ▼
source_discovery
  │
  ▼
planner
  │
  ▼
tool_executor
  │
  ▼
observation
  │
  ▼
judge
  │
  ├── sufficient
  │       │
  │       ▼
  │    final_answer
  │       │
  │       ▼
  │    save_memory
  │       │
  │       ▼
  │      END
  │
  └── insufficient
          │
          ▼
      replanner
          │
          ▼
      tool_executor
```

조건:

```text
MAX_ITERATIONS = 4
```

초기 구현은 4회로 제한한다.

MAX_ITERATIONS 도달 시:

- 확보한 정보로 답변
- 확인되지 않은 부분을 명확하게 표시
- 검색 실패를 사실처럼 생성하지 않기

---

# 16. Conversation Memory

현재 대화 저장 구조가 있다면 아래 정보까지 확장한다.

```json
{
  "conversation_id": "...",
  "recent_messages": [],
  "entities": {
    "product": "NAND",
    "issue": "Cell Leakage",
    "meeting": "NAND Yield Review"
  },
  "current_topic": "NAND Cell Leakage",
  "search_history": [],
  "retrieved_source_refs": [],
  "unresolved_information": []
}
```

목표 follow-up:

```text
User:
지난주 NAND 관련 회의 찾아줘.

Assistant:
NAND Yield Review를 찾음.

User:
그 회의에서 Action 뭐였어?
```

두 번째 질문에서 `그 회의`를 이전 turn의 event와 연결할 수 있어야 한다.

이때 가능하면 semantic 재검색보다 이전 event ID를 우선 사용한다.

---

# 17. 검색 전략

## 17.1 Domain Knowledge

```text
BM25
+
Vector
+
Rerank
```

---

## 17.2 Mail

```text
ACL Filter
+
is_active=true
+
Date Filter
+
BM25(text)
+
Vector(embedding)
+
content_kind filter
+
Rerank
```

---

## 17.3 Calendar

```text
ACL Filter
+
is_active=true
+
is_cancelled=false
+
Date Filter
+
subject match
+
text match
+
organizer / attendee filter
+
Vector
+
Rerank
```

---

# 18. Mail schema 개선 권장사항

현재 schema에서 아래 metadata field가 없다면 추가를 검토한다.

```text
sender_email
recipient_emails
cc_emails
subject
thread_id
```

이유:

아래와 같은 질문을 embedding만으로 처리하는 것은 불안정하다.

```text
"김OO이 보낸 메일"
"제목이 XXX인 메일"
"이 메일의 답장 thread"
```

사람, 제목, thread는 structured metadata 검색을 우선한다.

단, 이번 구현에서 schema 변경이 위험하면 기존 `text` 기반 검색으로 먼저 구현하고 TODO로 남긴다.

---

# 19. Calendar schema 활용

반드시 적극적으로 사용:

```text
subject
organizer_email
attendee_emails
start_at_utc
end_at_utc
parent_event_id
calendar_item_id
content_kind
occurrence_id
series_master_id
```

반복 일정은 가능하면:

```text
series_master_id
occurrence_id
```

를 이용해서 동일 시리즈의 다른 이벤트와 혼동하지 않게 한다.

---

# 20. 대표 검색 Flow

## Case 1. Domain 단일 검색

질문:

```text
Cell Leakage가 뭐야?
```

Flow:

```text
query_analyzer
 ↓
search_domain_knowledge
 ↓
judge = sufficient
 ↓
answer
```

---

## Case 2. Mail 단일 검색

질문:

```text
지난주 NAND 관련해서 받은 메일 찾아줘.
```

Flow:

```text
query_analyzer
 ↓
search_mail
 ↓
judge
 ↓
answer
```

---

## Case 3. Calendar 단일 검색

질문:

```text
지난주 NAND 회의 언제 했어?
```

Flow:

```text
query_analyzer
 ↓
search_calendar(content_kind=event)
 ↓
judge
 ↓
answer
```

---

## Case 4. 회의 내용 검색

질문:

```text
지난주 NAND 회의에서 무슨 얘기했어?
```

Flow:

```text
search_calendar(event)
 ↓
related event 발견
 ↓
expand_calendar_event
 ↓
event + attachments
 ↓
judge
 ↓
answer
```

---

## Case 5. Mail → Calendar Multi-hop

질문:

```text
김OO이 지난주 메일에서 얘기한 NAND 문제는 회의에서 어떻게 됐어?
```

Flow:

```text
search_mail
 ↓
Observation
 "NAND / Cell Leakage / 회의 예정"
 ↓
Judge = insufficient
 ↓
search_calendar
 ↓
관련 event
 ↓
expand_calendar_event
 ↓
Action / 첨부자료 확인
 ↓
judge
 ↓
answer
```

---

## Case 6. Mail → Calendar → Domain Knowledge

질문:

```text
김OO이 메일에서 이야기한 NAND 수율 문제가
회의에서 어떻게 결론났고 기술적으로 어떤 의미야?
```

Flow:

```text
search_mail
 ↓
search_calendar
 ↓
expand_calendar_event
 ↓
search_domain_knowledge
 ↓
judge
 ↓
answer
```

---

# 21. 최종 Answer 구조

답변은 검색 출처별 내용을 뒤섞어 hallucination하지 않도록 한다.

가능하면 내부적으로 provenance를 유지한다.

예:

```json
{
  "claims": [
    {
      "text": "메일에서 Cell Leakage 증가가 보고됨",
      "source": "ews-mail",
      "source_id": "..."
    },
    {
      "text": "회의에서 A 장비 FDC 확인을 Action으로 결정",
      "source": "ews-calendar",
      "calendar_item_id": "..."
    }
  ]
}
```

최종 자연어 답변은 위 evidence를 기반으로 생성한다.

---

# 22. Logging / Observability

각 질문마다 최소 아래 로그를 남긴다.

```text
conversation_id
question
intent
selected_sources
tool_calls
tool_queries
search_filters
hit_count
top_scores
iteration_count
judge_decisions
latency
final_sources
error
```

보안상 메일/회의 원문 전체를 로그에 그대로 남기지 않도록 주의한다.

---

# 23. 평가 데이터셋

실제 사내 질문을 기반으로 최소 100~200개 평가셋을 만든다.

## Level 1: Domain

```text
Cell Leakage가 뭐야?
```

## Level 2: Mail

```text
지난주 NAND 관련 메일 찾아줘.
```

## Level 3: Calendar

```text
지난주 NAND Yield Review 언제 했어?
```

## Level 4: Meeting Content

```text
지난 NAND Yield Review에서 Action이 뭐였어?
```

## Level 5: Cross Source

```text
지난주 메일에서 언급된 NAND 문제를 회의에서 어떻게 처리하기로 했어?
```

## Level 6: Multi-hop + Domain

```text
김OO이 지난주 메일에서 이야기한 NAND 수율 문제가
어떤 회의에서 논의됐고,
회의에서 어떤 Action을 하기로 했으며,
기술적으로 어떤 의미인지 설명해줘.
```

---

# 24. 평가 Metric

최소 아래를 측정한다.

```text
Intent Accuracy
Source Selection Accuracy
Tool Selection Accuracy
Retrieval Recall
Multi-hop Completion Rate
Answer Faithfulness
Evidence Coverage
Follow-up Resolution Accuracy
Average Tool Calls
Average Latency
Loop Failure Rate
```

특히 중요:

```text
Multi-hop Completion Rate
Answer Faithfulness
Tool Selection Accuracy
```

---

# 25. 구현 Phase

## Phase 1 — 기존 코드 분석

Codex가 먼저 현재 repository를 분석한다.

확인할 것:

- FastAPI entry point
- 기존 LangGraph graph
- Router node
- Retrieval node
- OpenSearch client
- MongoDB conversation memory
- embedding 호출부
- 기존 hybrid retrieval 구현
- 인증 사용자 / employee_id 전달 경로
- current index 이름 하드코딩 여부
- prompt 위치
- 테스트 구조

분석 후 현재 코드 구조를 최대한 유지해서 변경한다.

불필요한 전체 rewrite는 하지 않는다.

---

## Phase 2 — Search Tool Layer

구현:

```text
search_domain_knowledge
search_mail
search_calendar
expand_calendar_event
```

공통 SearchResult schema를 만든다.

예:

```python
class SearchDocument(BaseModel):
    source_type: str
    source_id: str | None
    parent_event_id: str | None
    content_kind: str | None
    text: str
    metadata: dict
    score: float | None

class SearchResult(BaseModel):
    tool: str
    query: str
    documents: list[SearchDocument]
    total_hits: int
```

---

## Phase 3 — Security / Filter Layer

공통 middleware 또는 search service layer 구현.

반드시:

```text
employee_id
is_active
is_cancelled
```

filter를 강제한다.

Agent/LLM에서 수정 불가하게 구현한다.

---

## Phase 4 — Calendar Event Expansion

구현:

```text
event hit → attachments 확장
attachment hit → parent event 확장
```

`parent_event_id`를 핵심 link key로 사용한다.

---

## Phase 5 — Agent State / LangGraph

구현 Node:

```text
query_analyzer
source_discovery
planner
tool_executor
observation
judge
replanner
final_answer
save_memory
```

기존 Router는 필요하면 general_chat 분기만 담당하도록 축소한다.

---

## Phase 6 — Loop

구현:

```text
Search
 ↓
Observation
 ↓
Judge
 ↓
Replan
```

`MAX_ITERATIONS = 4`

동일 Tool + 동일 Query 재호출 방지.

---

## Phase 7 — Memory

기존 MongoDB history와 연결한다.

추가:

```text
entities
current_topic
search_history
previous_event_reference
unresolved_information
```

follow-up에서 이전 event/source ID를 재사용한다.

---

## Phase 8 — Tests

최소 테스트:

```text
test_domain_only
test_mail_only
test_calendar_event_only
test_calendar_attachment_expand
test_mail_to_calendar
test_mail_calendar_domain
test_acl_enforced
test_cancelled_event_filtered
test_inactive_document_filtered
test_max_iterations
test_duplicate_search_block
test_follow_up_event_resolution
```

---

# 26. 완료 조건

다음 조건을 만족하면 1차 구현 완료로 본다.

- [ ] Agent가 physical index가 아니라 alias를 사용한다.
- [ ] `employee_id` ACL이 모든 Mail/Calendar 검색에서 backend 레벨로 강제된다.
- [ ] Mail body와 attachment 검색이 모두 가능하다.
- [ ] Calendar event와 attachment 검색이 모두 가능하다.
- [ ] Calendar attachment에서 `parent_event_id`를 통해 부모 event를 복원할 수 있다.
- [ ] Calendar event에서 같은 event의 attachment를 확장할 수 있다.
- [ ] 단일 질문에서 여러 Tool을 순차적으로 호출할 수 있다.
- [ ] 검색 결과가 부족하면 Judge가 추가 검색을 요청할 수 있다.
- [ ] 동일 Tool + 동일 Query 무한 반복을 방지한다.
- [ ] 최대 iteration을 초과하지 않는다.
- [ ] 멀티턴에서 이전 event/topic/entity를 활용할 수 있다.
- [ ] 검색 근거가 없는 내용을 사실처럼 생성하지 않는다.
- [ ] 기존 FastAPI API와 Frontend 호환성을 최대한 유지한다.
- [ ] 주요 노드와 Tool에 unit/integration test가 존재한다.

---

# 27. 구현 시 참고할 오픈소스 순서

## 1순위 — OpenSearch Agentic Search

우선 분석할 개념:

```text
ListIndexTool
IndexMappingTool
QueryPlanningTool
SearchIndexTool
Conversational Agent
Memory
```

특히 참고할 것:

```text
Index/Source Discovery
Query Planning
Tool Calling
Conversational Search
```

단, 현재 프로젝트에서는 OpenSearch Agentic Search를 그대로 도입하기보다 개념과 패턴을 현재 LangGraph 구조에 맞게 적용한다.

---

## 2순위 — LangGraph RAG Research Agent

참고:

```text
Research Plan
Research Step
Retrieval
Observation
Next Research Step
```

현재 프로젝트의:

```text
Planner
Tool Executor
Observation
Judge
Replanner
```

구현에 참고한다.

---

## 3순위 — OpenSearch Agent Server

참고:

```text
Agent
OpenSearch Tool
MCP
Local LLM
Multi-Agent
```

현재 구현 이후 Tool layer를 MCP로 분리할 때 참고한다.

초기 버전에서 MCP 도입은 필수 아님.

---

## 4순위 — RAGFlow

참고 영역:

```text
Document Parsing
Chunking
Retrieval
Reranking
Document Understanding
```

Mail/Calendar 첨부파일 검색 품질 개선 시 참고한다.

---

## 5순위 — LibreChat / Letta

참고 영역:

```text
Conversation
Memory
Stateful Agent
Tool
Subagent
```

Agent UX 및 장기 Memory 고도화 단계에서 참고한다.

---

# 28. 구현 우선순위

가장 중요한 순서는 아래와 같다.

```text
1. Search Tool 안정화
2. ACL / Filter 강제
3. Calendar parent_event_id 연결
4. Agent Loop
5. Multi-hop 검색
6. Memory
7. Retrieval 품질 개선
8. MCP / Multi-Agent
```

처음부터 Multi-Agent로 구현하지 않는다.

초기 목표:

```text
Single Agent
+
3 Search Tools
+
1 Event Expansion Helper
+
Judge/Replanner Loop
```

이 구조를 안정화한 뒤 필요하면 Multi-Agent/MCP로 확장한다.

---

# 29. Codex 개발 지시

1. 먼저 repository 전체 구조를 분석한다.
2. 기존 구현을 최대한 재사용한다.
3. 변경 전에 현재 retrieval flow를 문서화한다.
4. 한 번에 대규모 rewrite하지 않는다.
5. Phase 단위로 구현한다.
6. 각 Phase마다 test를 추가한다.
7. 기존 API contract를 가능하면 유지한다.
8. 보안 관련 `employee_id` 처리는 LLM prompt가 아닌 코드에서 강제한다.
9. OpenSearch physical index 이름을 새 코드에 하드코딩하지 않는다.
10. Alias는 설정 파일 또는 environment variable로 관리 가능하게 한다.
11. 모든 Tool output은 공통 schema로 정규화한다.
12. Raw OpenSearch hit 전체를 LLM context에 그대로 넣지 않는다.
13. 검색 결과마다 source provenance를 유지한다.
14. Calendar Event ↔ Attachment 관계는 semantic reasoning이 아니라 `parent_event_id`로 해결한다.
15. 동일 검색 반복 및 무한 loop 방지 로직을 반드시 포함한다.
16. Local LLM의 JSON 출력은 Pydantic schema 또는 structured parser로 검증한다.
17. JSON parsing 실패 시 fallback/retry 전략을 구현한다.
18. Tool 선택 실패 시 허용된 Tool 목록 안에서 fallback한다.
19. 검색 결과가 없으면 hallucination하지 않고 query rewrite 후 재검색한다.
20. 구현 완료 후 architecture, 변경 파일, 실행법, 테스트 결과를 README 또는 별도 문서로 정리한다.

---

# 30. 최종 목표 아키텍처

```text
                           USER
                             │
                             ▼
                    ┌────────────────┐
                    │ Query Analyzer │
                    └───────┬────────┘
                            │
               ┌────────────┴─────────────┐
               │                          │
         General Chat               Agentic RAG
                                           │
                                           ▼
                                  Source Discovery
                                           │
                                           ▼
                                     Search Planner
                                           │
                  ┌────────────────────────┼───────────────────────┐
                  │                        │                       │
                  ▼                        ▼                       ▼
       search_domain_knowledge        search_mail          search_calendar
                  │                        │                       │
              syld_gpt              ews-mail-active      ews-calendar-active
                                           │                       │
                                      body / attachment        event / attachment
                                                                   │
                                                                   ▼
                                                         expand_calendar_event
                                                                   │
                                                     parent_event_id relation
                  │                        │                       │
                  └────────────────────────┼───────────────────────┘
                                           ▼
                                      Observation
                                           │
                                           ▼
                                         Judge
                                           │
                              ┌────────────┴────────────┐
                              │                         │
                         insufficient                sufficient
                              │                         │
                              ▼                         ▼
                          Replanner                Final Answer
                              │                         │
                              └──────→ Tool             ▼
                                                    Memory
```

---

# 31. 핵심 성공 기준

최종적으로 아래 질문을 안정적으로 처리해야 한다.

```text
"김OO이 지난주 메일에서 이야기한 NAND 수율 문제가
어떤 회의에서 논의됐고,
회의에서 어떤 Action을 하기로 했으며,
기술적으로 어떤 의미인지 설명해줘."
```

기대 검색 흐름:

```text
search_mail
 ↓
메일에서 핵심 issue/entity 추출
 ↓
search_calendar
 ↓
관련 회의 event 발견
 ↓
expand_calendar_event
 ↓
회의 첨부파일 / Action 확인
 ↓
search_domain_knowledge
 ↓
기술적 해석
 ↓
Judge
 ↓
Final Answer
```

이 흐름이 자동으로 수행되면 1차 Agentic RAG 목표를 달성한 것으로 본다.
