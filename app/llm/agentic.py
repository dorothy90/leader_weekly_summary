import asyncio
import json
from typing import Protocol

from app.domain.agentic import IntentDecision, QueryAnalysis


INTENT_SYSTEM_PROMPT = """
질문과 제한된 대화 메모리를 하나의 IntentDecision으로 구조화하세요.

의미 책임:
- intent: 질문의 목적을 짧게 요약합니다.
- source_requests: 답변에 꼭 필요한 최소 논리 소스와 각 소스용 의미 검색어를
  지정합니다. 허용 source enum은 domain_knowledge, mail, calendar입니다.
- entities: 질문에 명시되거나 안전한 메모리에 있는 의미 엔터티만 지정합니다.
- time_scope: none, yesterday, previous_week, current_week, previous_month,
  exact_date 중 하나입니다. exact_date일 때만 exact_date를 지정합니다.
- event_reference: none 또는 previous_event입니다.
- calendar_detail_required: calendar의 상세/첨부 확장이 필요한지 나타냅니다.
- information_needs: 사용자에게 표시할 설명/추적용 문구일 뿐이며 source, 도구,
  날짜, 필터 또는 실행 정책을 선택하는 입력이 아닙니다.

서버가 소유하는 owner/user/employee/tenant ID, 물리 index/alias, filter, ACL,
도구 이름, 쿼리 DSL을 출력하지 마세요. source_requests에는 질문에 답하는 데
필요한 가장 작은 논리 source 집합만 포함하세요. 출력 스키마 밖의 필드를
추가하지 마세요.
""".strip()


class AgentAnalyzer(Protocol):
    async def analyze(
        self, question: str, memory: object, timezone_name: str
    ) -> QueryAnalysis: ...


class StructuredAgentModel:
    def __init__(self, llm, *, timeout_seconds=150, now=None):
        self.llm = llm
        self.timeout_seconds = max(0.001, float(timeout_seconds))
        self.now = now

    async def analyze(self, question, memory, timezone_name):
        safe_memory = {
            "entities": dict(
                list((getattr(memory, "entities", {}) or {}).items())[-16:]
            ),
            "current_topic": getattr(memory, "current_topic", None),
        }
        user = json.dumps(
            {"question": question, "memory": safe_memory},
            ensure_ascii=False,
        )
        for _attempt in range(2):
            try:
                decision = await asyncio.wait_for(
                    self.llm.complete_model(
                        INTENT_SYSTEM_PROMPT,
                        user,
                        IntentDecision,
                    ),
                    timeout=self.timeout_seconds,
                )
                validated = IntentDecision.model_validate(decision)
                return QueryAnalysis.from_intent(
                    validated,
                    now=self.now,
                    timezone_name=timezone_name,
                )
            except Exception:
                continue
        return QueryAnalysis.unavailable()
