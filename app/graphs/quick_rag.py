import asyncio
import logging
from openai import APIStatusError, APIConnectionError, APITimeoutError
from pydantic import ValidationError
from app.llm.gateway import LLMResponseError
import json
import re
import time
from datetime import datetime, timezone
from hashlib import sha256
from app.domain.quick_rag import QuickPlan, QuickMemory, QuickResultRef
from app.domain.chat import FastRAGResult, QualityStatus, ExecutionMetadata
from app.domain.evidence import Evidence
from app.domain.agentic import AgentTrace
from app.llm.quick_rag import (
    interpret,
    ANSWER_SYSTEM,
    GroundedAnswer,
)
from app.security.citations import CitationValidator
from app.security.redaction import sanitize_text
from app.observability.node_runs import (
    record_node,
    ensure_node_recorder,
    current_node_recorder,
)


class QuickRAGWorkflow:

    def __init__(
        self,
        retrieval,
        interpreter,
        generator,
        *,
        timezone_name="Asia/Seoul",
        deadline_seconds=20,
        preserve_sources_on_invalid_answer=False,
        evidence_agent=None,
    ):
        self.evidence_agent = evidence_agent
        self.retrieval = retrieval
        self.interpreter = interpreter
        self.generator = generator
        self.timezone_name = timezone_name
        self.deadline_seconds = deadline_seconds
        self.preserve_sources_on_invalid_answer = preserve_sources_on_invalid_answer

    async def invoke(self, request, policy, conversation=None):
        if request.user_id != policy.user_id:
            raise ValueError("request owner and policy owner must match exactly")
        start = time.monotonic()
        calls = []
        tools = []
        supplemental_searches = 0
        search_diagnostics = {}

        async def work():
            nonlocal tools, conversation, supplemental_searches, search_diagnostics
            memory = getattr(conversation, "quick_memory", None)
            if memory and memory.user_id != policy.user_id:
                memory = None
                conversation = None
            calls.append("interpret")
            async with record_node("quick_interpret"):
                if hasattr(self.interpreter, "interpret"):
                    plan = await self.interpreter.interpret(
                        request.message,
                        conversation,
                        self.timezone_name,
                        self.retrieval.capabilities(),
                    )
                else:
                    plan = await interpret(
                        self.interpreter,
                        request.message,
                        conversation,
                        self.timezone_name,
                        self.retrieval.capabilities(),
                    )
            plan = QuickPlan.model_validate(plan)
            if self.evidence_agent and plan.investigate and plan.intent == "list":
                plan.intent = "answer"
            # Require an actual temporal phrase before accepting newly invented dates.
            temporal_quote = plan.temporal_expression.strip()
            has_temporal_support = bool(
                temporal_quote
                and temporal_quote in request.message
                and re.search(
                    r"최근|요즘|오늘|어제|내일|이번|지난|다음|작년|올해|금년|금주|전주|금월|전월|기간|이후|이전|부터|까지|\d+\s*(?:년|월|일|주|분기)|\d{4}[-/.]\d|\b(?:today|yesterday|tomorrow|recent|last|this|next|since|before|after)\b",
                    temporal_quote,
                    re.I,
                )
            )
            if not has_temporal_support:
                for key in ("date_start", "date_end", "date_period"):
                    setattr(plan.conditions, key, None)
                plan.conditions.weeks = []
                if plan.recent is True:
                    plan.recent = None if plan.scope != "new" else False
            # A name inferred from retrieved body text must not override a resolved identity.
            if (
                memory
                and plan.scope != "new"
                and memory.plan.conditions.sender_email
                and plan.conditions.sender_name
                and plan.conditions.sender_name not in request.message
            ):
                plan.conditions.sender_name = None
            clear = set(plan.clear_conditions)
            temporal = {"date_start", "date_end", "date_period"}
            if plan.scope != "new" and memory:
                old = memory.plan.conditions.model_dump()
                new = plan.conditions.model_dump()
                replacing_dates = (
                    any(new[k] for k in temporal)
                    or bool(clear & temporal)
                    or "dates" in clear
                    or plan.recent is True
                )
                merged = {
                    k: new[k] if new[k] not in (None, []) else old[k] for k in old
                }
                if replacing_dates:
                    for key in temporal:
                        merged[key] = (
                            new[key]
                            if key not in clear and "dates" not in clear
                            else None
                        )
                for key in clear:
                    if key in merged:
                        merged[key] = [] if isinstance(old[key], list) else None
                plan.conditions = type(plan.conditions).model_validate(merged)
                if not plan.query and "query" not in clear:
                    plan.query = memory.plan.query
                if (
                    plan.recent is None
                    and "recent" not in clear
                    and not replacing_dates
                ):
                    plan.recent = memory.plan.recent
                if replacing_dates and any(new[k] for k in temporal):
                    plan.recent = False
                if plan.source_scope == "all":
                    plan.source_scope = memory.plan.source_scope
            elif plan.scope == "within_results":
                return finish(
                    "이전 결과가 없습니다. 먼저 검색해 주세요.",
                    limited=True,
                    code="NO_PRIOR_RESULTS",
                )
            for key in clear:
                if key in type(plan.conditions).model_fields:
                    setattr(
                        plan.conditions, key, [] if key in ("teams", "weeks") else None
                    )
            if "dates" in clear:
                for key in temporal:
                    setattr(plan.conditions, key, None)
                plan.recent = False
            if "query" in clear:
                plan.query = ""
            if "recent" in clear:
                plan.recent = False
            if plan.recent is None:
                plan.recent = False
            from app.domain.quick_rag import normalize_content_topic

            plan = normalize_content_topic(plan)
            # Content words and ordinary "mail" do not justify a category filter.
            if plan.conditions.mail_type:
                type_patterns = {
                    "weekly_report": r"주간\s*(?:업무\s*)?보고|주보|weekly[_ -]?report",
                    "daily_report": r"일일\s*(?:업무\s*)?보고|일보|daily[_ -]?report",
                    "other": r"기타\s*(?:유형|종류|메일)|other\s*(?:메일|mail|email|type|타입)|mail_type\s*[:=]\s*other",
                }
                type_pattern = type_patterns[plan.conditions.mail_type]
                explicit_type = re.search(type_pattern, request.message, re.I)
                inherited_type = bool(
                    memory
                    and plan.scope != "new"
                    and memory.plan.conditions.mail_type == plan.conditions.mail_type
                    and memory.plan.mail_type_expression
                    and re.search(type_pattern, memory.plan.mail_type_expression, re.I)
                )
                if explicit_type:
                    plan.mail_type_expression = explicit_type.group(0)
                elif inherited_type:
                    plan.mail_type_expression = memory.plan.mail_type_expression
                else:
                    plan.conditions.mail_type = None
                    plan.mail_type_expression = ""
            plan.clear_conditions = []
            literal_emails = {
                v.casefold()
                for v in re.findall(
                    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", request.message
                )
            }
            confirmed = (
                memory.plan.conditions if memory and plan.scope != "new" else None
            )
            for email_field in ("sender_email", "organizer_email", "attendee_email"):
                email = getattr(plan.conditions, email_field)
                if (
                    email
                    and email not in literal_emails
                    and (confirmed is None or email != getattr(confirmed, email_field))
                ):
                    return finish(
                        "인물의 이메일을 확인할 수 없습니다. 이름 또는 정확한 이메일을 알려 주세요.",
                        limited=True,
                        code="CLARIFICATION_REQUIRED",
                    )
            for field in ("teams", "weeks", "mail_type"):
                explicit = getattr(request.filters, field)
                if explicit:
                    setattr(plan.conditions, field, explicit)
                    if field == "mail_type":
                        plan.mail_type_expression = f"mail_type={explicit}"
            if plan.intent == "statistics":
                return finish(
                    "통계 집계는 지원하지 않습니다. 문서 검색 또는 목록을 요청해 주세요.",
                    limited=True,
                    code="STATISTICS_UNSUPPORTED",
                )
            if plan.intent == "clarify":
                return finish(
                    plan.clarification or "검색 조건을 구체적으로 알려 주세요.",
                    limited=True,
                    code="CLARIFICATION_REQUIRED",
                )
            if plan.intent == "general":
                answer = plan.general_answer
                if not answer:
                    calls.append("answer")
                    answer = await self.generator.complete_text(
                        "Answer briefly in Korean. Do not claim access to internal evidence.",
                        request.message,
                    )
                return finish(
                    sanitize_text(answer)[:4000],
                    mode="not_used",
                    quick_memory=(
                        QuickMemory(user_id=policy.user_id, plan=plan, result_ids=[])
                        if plan.scope == "new"
                        else None
                    ),
                )
            async with record_node("quick_search") as run:
                search_method = (
                    self.retrieval.search_expanded
                    if self.evidence_agent
                    and hasattr(self.retrieval, "search_expanded")
                    else self.retrieval.search
                )
                outcome = await search_method(
                    plan,
                    policy,
                    prior_ids=memory.result_ids if memory else [],
                    timezone_name=self.timezone_name,
                    **(
                        {"embedding_timeout_seconds": 7.5}
                        if self.evidence_agent
                        else {}
                    ),
                )
                run.update(
                    output_metrics={
                        "search_count": len(outcome.searched_sources),
                        "evidence_count": len(outcome.documents),
                    }
                )
            tools = list(outcome.searched_sources)
            if outcome.vector_required_failed:
                search_diagnostics = {
                    "vector_sources": outcome.vector_sources,
                    "search_queries": outcome.executed_queries[:12],
                }
                return finish(
                    "벡터 검색을 완료하지 못했습니다. 키워드 검색 결과로 대신 답하지 않았습니다. 잠시 후 다시 시도해 주세요.",
                    limited=True,
                    code="VECTOR_SEARCH_UNAVAILABLE",
                    disclosures=outcome.disclosures,
                )
            if outcome.clarification:
                return finish(
                    outcome.clarification,
                    limited=True,
                    code="CLARIFICATION_REQUIRED",
                    disclosures=outcome.disclosures,
                )
            agent_answer = None
            agent_queries = []
            if self.evidence_agent and plan.intent == "answer":
                agent_answer, agent_queries = await self.evidence_agent.run(
                    request.message,
                    outcome.resolved_plan or plan,
                    outcome,
                    self.retrieval,
                    policy,
                    memory,
                    calls,
                    tools,
                    self.timezone_name,
                    deadline=start + self.deadline_seconds,
                )
            supplemental_searches = outcome.supplemental_batches or int(
                bool(agent_queries)
            )
            search_diagnostics = {
                "search_queries": list(
                    dict.fromkeys([*outcome.executed_queries, *agent_queries])
                )[:12],
                "investigation_stop_reason": outcome.investigation_stop,
                "selected_passage_count": outcome.selected_passage_count,
                "vector_sources": outcome.vector_sources,
            }
            evidence = []
            for i, d in enumerate(outcome.documents, 1):
                metadata_text = json.dumps(
                    {
                        k: v
                        for k, v in d.metadata.items()
                        if k
                        in (
                            "received_at",
                            "sent_at",
                            "updated_at",
                            "created_at",
                            "start_at_utc",
                            "end_at_utc",
                            "sender_name",
                            "sender_email",
                            "organizer_email",
                            "attendee_emails",
                            "location",
                            "team",
                            "week",
                        )
                    },
                    ensure_ascii=False,
                )[:500]
                text = (
                    metadata_text
                    + "\n"
                    + sanitize_text(d.text)[: 6000 if self.evidence_agent else 1300]
                ).strip() or "[REDACTED]"
                title = sanitize_text(d.title)[:500]
                locator = (
                    f"{d.source}:{d.original_id}"
                    if re.fullmatch("[A-Za-z0-9_.:@+-]{1,200}", d.original_id)
                    else None
                )
                evidence.append(
                    Evidence(
                        evidence_id=f"S{i}",
                        source_type=(
                            "calendar"
                            if d.source == "ews_calendar"
                            else "wiki" if d.source == "wiki_summary" else "mail"
                        ),
                        document_id=f"{d.source}:{d.id}"[:256],
                        title=title,
                        excerpt=text,
                        source_locator=locator,
                        score=d.score,
                        user_id=policy.user_id,
                        acl_decision_id=policy.decision_id,
                        content_hash=sha256(text.encode()).hexdigest(),
                    )
                )
            if not evidence:
                text = (
                    "검색 가능한 소스에서 일치하는 자료를 찾지 못했습니다."
                    if not outcome.failed_sources
                    else "일부 소스 검색이 실패하여 자료 유무를 확인하지 못했습니다."
                )
                return finish(
                    text,
                    limited=True,
                    code=(
                        "SOURCE_UNAVAILABLE"
                        if outcome.failed_sources
                        else "NO_EVIDENCE"
                    ),
                    disclosures=outcome.disclosures,
                    mode=outcome.retrieval_mode,
                    quick_memory=(
                        QuickMemory(
                            user_id=policy.user_id,
                            plan=outcome.resolved_plan or plan,
                            result_ids=[],
                        )
                        if plan.scope == "new"
                        else None
                    ),
                )
            answer_status = "not_assessed"
            if plan.intent == "list":
                rows = []
                for e, d in zip(evidence, outcome.documents):
                    metadata = " · ".join(
                        (
                            sanitize_text(str(d.metadata[k]))[:150]
                            for k in (
                                "received_at",
                                "updated_at",
                                "start_at_utc",
                                "week",
                                "sender_name",
                                "sender_email",
                                "location",
                            )
                            if d.metadata.get(k)
                        )
                    )
                    rows.append(
                        f"- {e.title or '제목 없음'}"
                        + (f" · {metadata}" if metadata else "")
                        + f" [{e.evidence_id}]"
                    )
                answer = "\n".join(rows)
            else:
                if agent_answer is None:
                    calls.append("answer")
                context = [
                    {"id": e.evidence_id, "title": e.title, "text": e.excerpt[:700]}
                    for e in evidence
                ]
                async with record_node("quick_answer"):
                    structured = GroundedAnswer.model_validate(
                        agent_answer
                        if agent_answer is not None
                        else await self.generator.complete_model(
                            ANSWER_SYSTEM,
                            json.dumps(
                                {
                                    "question": request.message,
                                    "evidence": context,
                                    "search_conditions": (
                                        outcome.resolved_plan or plan
                                    ).conditions.model_dump(exclude_none=True),
                                    "search_scope": (
                                        outcome.resolved_plan or plan
                                    ).source_scope,
                                },
                                ensure_ascii=False,
                            ),
                            GroundedAnswer,
                        )
                    )
                    answer_status = structured.evidence_status
                    missing = CitationValidator().normalize(
                        sanitize_text(structured.missing), evidence
                    )
                    # Coverage explanations do not cite documents as proof of absence.
                    for item in evidence:
                        missing = missing.replace(f"[{item.evidence_id}]", "")
                    missing = missing.strip()
                    if answer_status == "none":
                        return finish(
                            missing,
                            evidence=[],
                            limited=True,
                            disclosures=outcome.disclosures,
                            valid=None,
                            mode=outcome.retrieval_mode,
                            evidence_status="none",
                            quick_memory=QuickMemory(
                                user_id=policy.user_id,
                                plan=outcome.resolved_plan or plan,
                                result_ids=(
                                    memory.result_ids
                                    if memory and plan.scope != "new"
                                    else []
                                ),
                                search_queries=list(
                                    dict.fromkeys(
                                        [
                                            *(
                                                memory.search_queries
                                                if memory and plan.scope != "new"
                                                else []
                                            ),
                                            *outcome.executed_queries,
                                            plan.query,
                                            *agent_queries,
                                        ]
                                    )
                                )[-12:],
                                last_answer=(
                                    memory.last_answer
                                    if memory and plan.scope != "new"
                                    else ""
                                ),
                                unresolved=sanitize_text(structured.missing)[:700],
                            ),
                        )
                    answer = sanitize_text(structured.answer)
                    extra_citations = " ".join(
                        f"[{key}]"
                        for key in structured.evidence_ids
                        if f"[{key}]" not in answer
                    )
                    if extra_citations:
                        answer += "\n" + extra_citations
                    if answer_status == "partial":
                        answer += "\n\n확인되지 않은 부분: " + missing
            validator = CitationValidator()
            answer = validator.normalize(answer, evidence)
            validation = validator.validate(answer, evidence, policy)
            valid = validation.valid
            answer_status = answer_status if valid else "not_assessed"
            if not valid and self.preserve_sources_on_invalid_answer:
                # Discard the model's claims, never repair unknown citations by guessing.
                answer = "생성된 설명의 인용을 확인하지 못했습니다. 질문에 대한 결론은 보류하고, 검색된 자료를 제공합니다. 이 목록 자체가 질문의 답을 뒷받침한다는 뜻은 아닙니다.\n"
                answer += "\n".join(
                    f"- 검색 자료 {i} [{e.evidence_id}]"
                    for i, e in enumerate(evidence, 1)
                )
                validation = validator.validate(answer, evidence, policy)
                valid = validation.valid
                outcome.disclosures.append(
                    "설명 생성 결과를 사용하지 않고 확인된 검색 자료 목록으로 대체했습니다."
                )
            if not valid:
                answer = "인용 검증을 통과하지 못해 답변을 제공할 수 없습니다. 확인된 검색 자료를 참고해 주세요."
            quick_memory = None
            if valid:
                quick_memory = QuickMemory(
                    user_id=policy.user_id,
                    plan=outcome.resolved_plan or plan,
                    result_ids=[
                        QuickResultRef(source=d.source, id=d.original_id)
                        for e, d in zip(evidence, outcome.documents)
                        if e.evidence_id in validation.cited_ids
                    ][:10],
                )
            if quick_memory and self.evidence_agent:
                previous = (
                    memory.search_queries if memory and plan.scope != "new" else []
                )
                quick_memory.search_queries = list(
                    dict.fromkeys(
                        [
                            *previous,
                            *outcome.executed_queries,
                            plan.query,
                            *agent_queries,
                        ]
                    )
                )[-12:]
                quick_memory.last_answer = sanitize_text(answer)[:3000]
                quick_memory.unresolved = agent_answer.missing if agent_answer else ""
            return finish(
                answer,
                evidence=evidence,
                limited=bool(outcome.disclosures)
                or not valid
                or answer_status == "partial",
                code=(
                    "CITATION_INVALID"
                    if not valid
                    else "SOURCE_UNAVAILABLE" if outcome.failed_sources else None
                ),
                disclosures=outcome.disclosures,
                valid=valid,
                evidence_status=answer_status,
                mode=outcome.retrieval_mode,
                quick_memory=quick_memory,
            )

        def finish(
            answer,
            *,
            evidence=None,
            limited=False,
            code=None,
            disclosures=None,
            valid=None,
            evidence_status="not_assessed",
            mode="deterministic",
            quick_memory=None,
        ):
            evidence = evidence or []
            disclosures = disclosures or []
            if disclosures:
                answer += "\n\n" + "\n".join(disclosures[:4])
            kwargs = dict(
                answer=answer,
                evidence=evidence,
                quality=QualityStatus(
                    citation_valid=valid,
                    limited_answer=limited,
                    retrieval_mode=mode,
                    evidence_status=evidence_status,
                ),
                disclosures=disclosures[:4],
                execution=ExecutionMetadata(
                    status="limited" if limited else "succeeded",
                    failure_stage=(
                        "citation_validation"
                        if code == "CITATION_INVALID"
                        else (
                            "retrieval"
                            if tools and code
                            else "planning" if code else None
                        )
                    ),
                    error_code=code,
                    retryable=code
                    in (
                        "SOURCE_UNAVAILABLE",
                        "DEADLINE_EXCEEDED",
                        "LLM_INVALID_FORMAT",
                        "LLM_EMPTY_RESPONSE",
                        "LLM_OUTPUT_TRUNCATED",
                        "LLM_UNAVAILABLE",
                        "LLM_RATE_LIMIT",
                        "VECTOR_SEARCH_UNAVAILABLE",
                    ),
                    additional_search_count=supplemental_searches,
                    **search_diagnostics,
                    search_count=len(tools),
                    evidence_count=len(evidence),
                    duration_ms=int((time.monotonic() - start) * 1000),
                    include_in_llm_history=not limited,
                ),
                agent_trace=AgentTrace(
                    llm_calls=calls,
                    tool_calls=tools[:8],
                    iteration_count=1 + int("agent_answer" in calls),
                    judge_decisions=[],
                ),
            )
            if "quick_memory" in FastRAGResult.model_fields:
                kwargs["quick_memory"] = quick_memory
            return FastRAGResult(**kwargs)

        with ensure_node_recorder() as recorder:
            try:
                result = await asyncio.wait_for(work(), self.deadline_seconds)
            except asyncio.TimeoutError:
                result = finish(
                    "요청 시간이 초과되었습니다. 다시 시도해 주세요.",
                    limited=True,
                    code="DEADLINE_EXCEEDED",
                )
            except (
                LLMResponseError,
                ValidationError,
                APIStatusError,
                APIConnectionError,
            ) as error:
                if isinstance(error, LLMResponseError):
                    code = error.code
                elif isinstance(error, ValidationError):
                    code = "LLM_INVALID_FORMAT"
                elif isinstance(error, APIStatusError):
                    code = (
                        "LLM_RATE_LIMIT"
                        if error.status_code == 429
                        else (
                            "LLM_CONFIGURATION"
                            if error.status_code in (400, 401, 403, 404)
                            else "LLM_UNAVAILABLE"
                        )
                    )
                else:
                    code = "LLM_UNAVAILABLE"
                messages = {
                    "LLM_INVALID_FORMAT": "질문 해석 모델이 올바른 응답 형식을 반환하지 못했습니다. 잠시 후 다시 시도해 주세요.",
                    "LLM_EMPTY_RESPONSE": "모델이 빈 응답을 반환했습니다. 잠시 후 다시 시도해 주세요.",
                    "LLM_OUTPUT_TRUNCATED": "모델 응답이 출력 한도에서 중단됐습니다. 서버의 모델 설정 확인이 필요합니다.",
                    "LLM_RATE_LIMIT": "모델 서비스의 요청 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.",
                    "LLM_CONFIGURATION": "모델 서비스 연결 설정에 문제가 있습니다. 서버 설정 확인이 필요합니다.",
                    "LLM_REFUSED": "모델이 이 요청에 응답하지 않았습니다.",
                    "LLM_UNAVAILABLE": "모델 서비스가 일시적으로 응답하지 못했습니다. 잠시 후 다시 시도해 주세요.",
                }
                logging.getLogger(__name__).warning(
                    "quick_rag_model_error code=%s type=%s", code, type(error).__name__
                )
                result = finish(messages[code], limited=True, code=code)
                result.execution.failure_stage = (
                    "generation" if calls and calls[-1] == "answer" else "planning"
                )
            except Exception:
                logging.getLogger(__name__).exception("quick_rag_internal_failure")
                result = finish(
                    "서버 내부 처리 중 오류가 발생했습니다. 서버 기록 확인이 필요합니다.",
                    limited=True,
                    code="QUICK_RAG_FAILED",
                )
            result.execution.node_runs = recorder.snapshot()
            return result
