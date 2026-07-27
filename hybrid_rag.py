"""Pure helpers and serializable schemas for the Hybrid Agentic RAG graph."""

from __future__ import annotations

import re
import json
from hashlib import sha256
from typing import Any, Literal, Sequence, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator


Route = Literal["general", "statistics", "search"]
FollowUpType = Literal[
    "new_topic",
    "refine",
    "compare",
    "expand",
    "evidence",
    "continue",
    "clarify",
]
FINAL_DOCUMENT_LIMIT = 8
MAX_FINAL_CONTEXT_TOKENS = 16_000
MAX_CHUNKS_PER_PARENT = 2
MAX_SEARCH_ATTEMPTS = 2
MAX_QUERY_REWRITES = 2
MAX_SUB_QUESTIONS = 6
MAX_PARALLEL_SEARCH_TASKS = 12
MAX_ANSWER_REVISIONS = 1
AUTO_WIKI_SAVE_ENABLED = False
MIN_WIKI_GROUNDEDNESS = 0.85
MIN_WIKI_SOURCE_COUNT = 2


class CitedEvidenceMetadata(BaseModel):
    """Bounded citation metadata retained between conversation turns."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    document_id: str = Field(min_length=1, max_length=200)
    source_type: str = Field(min_length=1, max_length=50)
    title: str = Field(default="", max_length=300)
    team: str | None = Field(default=None, max_length=100)
    week: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    snippet: str = Field(default="", max_length=500)
    mail_id: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=2_000)
    html_path: str | None = Field(default=None, max_length=1_000)
    part_index: int | None = Field(default=None, ge=0)
    total_parts: int | None = Field(default=None, ge=1)


class ConversationMemory(BaseModel):
    """Serializable, bounded state that may be carried across chat turns."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    schema_version: int = Field(default=1, ge=1)
    active_topic: str | None = Field(default=None, max_length=500)
    standalone_question: str | None = Field(default=None, max_length=2_000)
    search_query: str | None = Field(default=None, max_length=1_000)
    route: Route | None = None
    follow_up_type: FollowUpType | None = None
    teams: list[str] = Field(default_factory=list, max_length=10)
    weeks: list[str] = Field(default_factory=list, max_length=12)
    mail_type: Literal["daily_report", "weekly_report"] | None = None
    rolling_summary: str = Field(default="", max_length=2_000)
    cited_evidence: list[CitedEvidenceMetadata] = Field(
        default_factory=list,
        max_length=FINAL_DOCUMENT_LIMIT,
    )

    @field_validator("teams")
    @classmethod
    def deduplicate_teams(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))

    @field_validator("weeks")
    @classmethod
    def validate_weeks(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            match = re.fullmatch(r"\s*(\d{4})-(\d{1,2})\s*", value)
            if not match or not 1 <= int(match.group(2)) <= 53:
                raise ValueError("weeks must contain valid YYYY-WW values")
            week = f"{match.group(1)}-{int(match.group(2)):02d}"
            if week not in normalized:
                normalized.append(week)
        return normalized


class ContextualizedTurn(BaseModel):
    """Validated interpretation of one raw user turn."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    schema_version: int = Field(default=1, ge=1)
    active_topic: str | None = Field(default=None, max_length=500)
    standalone_question: str = Field(min_length=1, max_length=2_000)
    search_query: str = Field(min_length=1, max_length=1_000)
    route: Literal["general", "statistics", "search", "clarify"]
    follow_up_type: FollowUpType
    teams: list[str] = Field(default_factory=list, max_length=10)
    weeks: list[str] = Field(default_factory=list, max_length=12)
    mail_type: Literal["daily_report", "weekly_report"] | None = None
    rolling_summary: str = Field(default="", max_length=2_000)
    cited_evidence: list[CitedEvidenceMetadata] = Field(
        default_factory=list,
        max_length=FINAL_DOCUMENT_LIMIT,
    )
    clarification_answer: str | None = Field(default=None, max_length=500)

    @field_validator("teams")
    @classmethod
    def deduplicate_teams(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))

    @field_validator("weeks")
    @classmethod
    def validate_weeks(cls, values: list[str]) -> list[str]:
        return ConversationMemory.validate_weeks(values)


class RetrievedDocument(TypedDict, total=False):
    document_id: str
    source_type: str
    title: str
    content: str
    team: str | None
    week: str | None
    original_score: float | None
    rerank_score: float | None
    citation_id: str
    metadata: dict[str, Any]


class RetrievalGrade(BaseModel):
    relevant: bool
    sufficient: bool
    score: float = Field(ge=0.0, le=1.0)
    missing_information: list[str]
    reason: str


class RetrievalPlan(BaseModel):
    intent: Literal["general", "single_search", "comparison", "trend", "statistics"]
    sources: list[Literal["mail", "wiki", "technical_document", "statistics"]]
    sub_questions: list[str] = Field(default_factory=list, max_length=MAX_SUB_QUESTIONS)
    filters: dict[str, Any] = Field(default_factory=dict)
    search_strategy: Literal["semantic", "keyword", "hybrid"] = "hybrid"
    vector_weight: float = Field(default=0.7, ge=0.0, le=1.0)
    keyword_weight: float = Field(default=0.3, ge=0.0, le=1.0)


class SearchTask(BaseModel):
    query: str
    sub_question: str
    source: Literal["mail", "wiki", "technical_document", "statistics"]
    team: str | None = None
    weeks: list[str] | None = None
    vector_weight: float = Field(ge=0.0, le=1.0)
    keyword_weight: float = Field(ge=0.0, le=1.0)
    search_key: str


class AnswerEvaluation(BaseModel):
    groundedness_score: float = Field(ge=0.0, le=1.0)
    completeness_score: float = Field(ge=0.0, le=1.0)
    citation_valid: bool
    unsupported_claims: list[str] = Field(default_factory=list)
    missing_answers: list[str] = Field(default_factory=list)
    passed: bool
    reason: str


def fallback_route(question: str) -> Route:
    """Choose a conservative route without an LLM."""
    normalized = " ".join(question.strip().lower().split())

    general_patterns = (
        r"^(안녕|안녕하세요|반가워|반갑습니다)[.!? ]*$",
        r"^(고마워|감사해|감사합니다)[.!? ]*$",
        r"^(도움말|사용법)( 알려줘| 설명해줘)?[.!? ]*$",
        r"^(이 )?(시스템|챗봇)(은|이)? .*?(뭐|무엇|하는|기능).*[.!? ]*$",
    )
    if any(re.fullmatch(pattern, normalized) for pattern in general_patterns):
        return "general"

    statistics_keywords = (
        "미제출",
        "안 낸",
        "안낸",
        "안 보낸",
        "안보낸",
        "제출 현황",
        "제출 수",
        "제출 건수",
        "제출했",
        "제출한 팀",
        "몇 개",
        "몇개",
        "몇 건",
        "몇건",
        "집계",
        "통계",
    )
    if any(keyword in normalized for keyword in statistics_keywords):
        return "statistics"

    return "search"


def normalize_weeks(value: str | Sequence[str] | None) -> list[str] | None:
    """Normalize one or more ISO week values while preserving order."""
    if value is None:
        return None

    raw_values = value.split(",") if isinstance(value, str) else value
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        match = re.fullmatch(r"\s*(\d{4})-(\d{1,2})\s*", str(raw))
        if not match or not 1 <= int(match.group(2)) <= 53:
            continue
        week = f"{match.group(1)}-{int(match.group(2)):02d}"
        if week not in seen:
            seen.add(week)
            normalized.append(week)
    return normalized or None


def append_trace(
    trace: Sequence[dict[str, Any]] | None,
    event: str,
    **data: Any,
) -> list[dict[str, Any]]:
    """Return a new trace list with one compact structured event appended."""
    return [*(trace or []), {"event": event, **data}]


def _fingerprint(value: str) -> str:
    normalized = " ".join(value.lower().split())
    return sha256(normalized.encode("utf-8")).hexdigest()


def normalize_result(
    source_type: str,
    raw: dict[str, Any],
    task: dict[str, Any] | None,
) -> RetrievedDocument:
    """Convert a source-specific result to the common evidence schema."""
    task = task or {}
    content = str(raw.get("content") or raw.get("text") or raw.get("page_content") or "")
    if source_type == "technical_document":
        team = raw.get("team")
        week = raw.get("week")
    else:
        team = raw.get("team") or task.get("team")
        week = raw.get("week") or task.get("week")
    score = raw.get("original_score", raw.get("score"))

    if source_type == "mail":
        mail_id = str(raw.get("mail_id") or _fingerprint(content)[:16])
        part_index = raw.get("part_index")
        part_index = 0 if part_index is None else part_index
        document_id = f"mail:{mail_id}:{part_index}"
        title = str(raw.get("title") or f"{team or 'unknown'} {week or 'unknown'} 메일")
        parent_id = f"mail:{mail_id}"
    elif source_type == "wiki":
        title = str(raw.get("title") or "Wiki 요약")
        identity = "|".join([title, str(team or ""), str(week or ""), content])
        document_id = f"wiki:{_fingerprint(identity)[:20]}"
        parent_id = document_id
    else:
        title = str(raw.get("title") or "기술 문서")
        source_id = raw.get("document_id") or raw.get("id") or _fingerprint(content)[:20]
        document_id = f"technical_document:{source_id}"
        parent_id = document_id

    metadata = {
        "query": task.get("query"),
        "sub_question": task.get("sub_question"),
        "parent_id": parent_id,
    }
    for key in (
        "mail_id",
        "html_path",
        "part_index",
        "total_parts",
        "summary_type",
        "topic",
    ):
        if raw.get(key) is not None:
            metadata[key] = raw[key]

    return RetrievedDocument(
        document_id=document_id,
        source_type=source_type,
        title=title,
        content=content,
        team=team,
        week=week,
        original_score=float(score) if score is not None else None,
        rerank_score=None,
        metadata=metadata,
    )


def deduplicate_documents(
    documents: Sequence[RetrievedDocument],
) -> tuple[list[RetrievedDocument], int]:
    """Remove duplicate content and cap repeated chunks from one parent."""
    unique: list[RetrievedDocument] = []
    seen_ids: set[str] = set()
    seen_content: set[str] = set()
    parent_counts: dict[str, int] = {}

    for document in documents:
        document_id = document.get("document_id", "")
        content_key = _fingerprint(document.get("content", ""))
        parent_id = str(document.get("metadata", {}).get("parent_id") or document_id)
        if document_id in seen_ids or content_key in seen_content:
            continue
        if parent_counts.get(parent_id, 0) >= MAX_CHUNKS_PER_PARENT:
            continue

        unique.append(dict(document))
        seen_ids.add(document_id)
        seen_content.add(content_key)
        parent_counts[parent_id] = parent_counts.get(parent_id, 0) + 1

    return unique, len(documents) - len(unique)


def _terms(text: str) -> set[str]:
    return set(re.findall(r"[0-9a-zA-Z가-힣_-]{2,}", text.lower()))


def _grounding_terms(text: str) -> set[str]:
    """Normalize common Korean particles for conservative claim comparison."""
    normalized: set[str] = set()
    suffixes = (
        "입니다",
        "했습니다",
        "되었습니다",
        "에서는",
        "으로",
        "에서",
        "에게",
        "까지",
        "부터",
        "과",
        "와",
        "은",
        "는",
        "이",
        "가",
        "을",
        "를",
    )
    for term in _terms(text):
        for suffix in suffixes:
            if term.endswith(suffix) and len(term) > len(suffix) + 1:
                term = term[: -len(suffix)]
                break
        if term not in {"요약", "상세", "설명", "핵심", "결론"}:
            normalized.add(term)
    return normalized


def rerank_documents(
    question: str,
    documents: Sequence[RetrievedDocument],
    filters: dict[str, Any] | None = None,
    limit: int = FINAL_DOCUMENT_LIMIT,
) -> list[RetrievedDocument]:
    """Apply stable source-score, lexical, and metadata ranking."""
    if not documents:
        return []

    filters = filters or {}
    query_terms = _terms(question)
    raw_scores = [float(doc.get("original_score") or 0.0) for doc in documents]
    minimum, maximum = min(raw_scores), max(raw_scores)
    score_range = maximum - minimum
    requested_team = filters.get("team")
    requested_weeks = set(filters.get("weeks") or [])
    ranked: list[RetrievedDocument] = []

    for document, raw_score in zip(documents, raw_scores):
        normalized_score = (raw_score - minimum) / score_range if score_range else 1.0
        document_terms = _terms(
            " ".join(
                [
                    document.get("title", ""),
                    document.get("content", ""),
                    str(document.get("team") or ""),
                    str(document.get("week") or ""),
                ]
            )
        )
        overlap = len(query_terms & document_terms) / max(1, len(query_terms))
        team_boost = 0.15 if requested_team and document.get("team") == requested_team else 0.0
        week_boost = 0.10 if requested_weeks and document.get("week") in requested_weeks else 0.0
        rerank_score = 0.60 * normalized_score + 0.25 * overlap + team_boost + week_boost
        ranked.append({**document, "rerank_score": round(rerank_score, 6)})

    ranked.sort(key=lambda doc: (-float(doc.get("rerank_score") or 0.0), doc["document_id"]))
    return ranked[: max(0, limit)]


def build_context(
    documents: Sequence[RetrievedDocument],
    token_limit: int = MAX_FINAL_CONTEXT_TOKENS,
) -> tuple[str, list[RetrievedDocument]]:
    """Build citation-stable context without exceeding an approximate token budget."""
    blocks: list[str] = []
    selected: list[RetrievedDocument] = []
    used_tokens = 0

    def estimate_tokens(text: str) -> int:
        return max(1, (len(text.encode("utf-8")) + 2) // 3)

    document_count = max(1, min(len(documents), FINAL_DOCUMENT_LIMIT))
    per_document_budget = max(128, token_limit // document_count)

    for document in documents:
        citation_id = f"S{len(selected) + 1}"
        header = (
            f"[{citation_id}] {document.get('title', '')}\n"
            f"출처: {document.get('source_type', '')}\n"
            f"팀: {document.get('team') or '해당 없음'}\n"
            f"주차: {document.get('week') or '해당 없음'}\n"
            "내용:\n"
        )
        separator_tokens = estimate_tokens("\n\n---\n\n") if blocks else 0
        remaining = token_limit - used_tokens - separator_tokens
        target_budget = min(per_document_budget, remaining)
        header_tokens = estimate_tokens(header)
        if target_budget <= header_tokens:
            continue

        content = str(document.get("content", ""))
        low, high = 0, len(content)
        while low < high:
            middle = (low + high + 1) // 2
            if estimate_tokens(header + content[:middle]) <= target_budget:
                low = middle
            else:
                high = middle - 1
        block = header + content[:low]
        estimated_tokens = estimate_tokens(block)
        if used_tokens + separator_tokens + estimated_tokens > token_limit:
            continue
        selected_document = {**document, "citation_id": citation_id}
        selected.append(selected_document)
        blocks.append(block)
        used_tokens += separator_tokens + estimated_tokens

    return "\n\n---\n\n".join(blocks), selected


def _team_names(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z가-힣]+팀", text))


def fallback_grade_retrieval(
    question: str,
    documents: Sequence[RetrievedDocument],
    filters: dict[str, Any] | None,
    sub_questions: Sequence[str] | None,
) -> RetrievalGrade:
    """Deterministically grade relevance and coverage when LLM grading fails."""
    if not documents:
        return RetrievalGrade(
            relevant=False,
            sufficient=False,
            score=0.0,
            missing_information=["질문에 답할 검색 근거 부족"],
            reason="검색 결과가 없습니다.",
        )

    filters = filters or {}
    sub_questions = sub_questions or []
    corpus = " ".join(
        f"{document.get('title', '')} {document.get('content', '')}"
        for document in documents
    )
    query_terms = _terms(question)
    query_terms.update(_team_names(question))
    corpus_terms = _terms(corpus)
    corpus_terms.update(
        str(document.get("team"))
        for document in documents
        if document.get("team")
    )
    overlap_count = len(query_terms & corpus_terms)
    overlap = overlap_count / max(1, len(query_terms))
    meaningful_coverage = overlap_count >= min(2, len(query_terms)) and overlap >= 0.25
    team_match = bool(
        filters.get("team")
        and any(document.get("team") == filters["team"] for document in documents)
    )
    relevant = meaningful_coverage or (team_match and overlap_count >= 1)

    missing: list[str] = []
    requested_teams = _team_names(" ".join([question, *sub_questions]))
    if filters.get("team"):
        requested_teams.add(str(filters["team"]))
    available_teams = {str(document.get("team")) for document in documents if document.get("team")}
    for team in sorted(requested_teams - available_teams):
        missing.append(f"{team} 관련 근거 부족")

    requested_weeks = set(filters.get("weeks") or [])
    available_weeks = {str(document.get("week")) for document in documents if document.get("week")}
    for week in sorted(requested_weeks - available_weeks):
        missing.append(f"{week} 주차 근거 부족")

    numeric_question = any(
        keyword in question
        for keyword in ("몇", "건수", "개수", "수치", "통계", "합계")
    )
    if numeric_question and not re.search(r"\d", corpus):
        missing.append("숫자 근거 부족")

    numeric_claims: dict[str, set[str]] = {}
    for document in documents:
        for sentence in re.split(r"[.!?\n]+", document.get("content", "")):
            numbers = set(re.findall(r"\d+(?:\.\d+)?", sentence))
            if not numbers:
                continue
            normalized_sentence = " ".join(
                re.sub(r"\d+(?:\.\d+)?", "#", sentence.lower()).split()
            )
            signature = "|".join(
                [
                    str(document.get("team") or ""),
                    str(document.get("week") or ""),
                    normalized_sentence,
                ]
            )
            numeric_claims.setdefault(signature, set()).update(numbers)
    if any(len(numbers) > 1 for numbers in numeric_claims.values()):
        missing.append("동일 주장에 상충하는 숫자 근거 확인 필요")

    sufficient = relevant and meaningful_coverage and not missing
    coverage_penalty = min(0.6, len(missing) * 0.2)
    score = max(0.0, min(1.0, 0.4 + overlap - coverage_penalty)) if relevant else 0.0
    return RetrievalGrade(
        relevant=relevant,
        sufficient=sufficient,
        score=round(score, 4),
        missing_information=missing,
        reason=(
            "질문 조건을 충족하는 근거가 확보됐습니다."
            if sufficient
            else "검색 근거의 조건 또는 범위가 부족합니다."
        ),
    )


def make_search_key(
    query: str,
    source: str,
    filters: dict[str, Any] | None,
) -> str:
    """Create a stable key used to suppress repeated searches."""
    normalized_filters = dict(filters or {})
    if normalized_filters.get("weeks"):
        normalized_filters["weeks"] = sorted(set(normalized_filters["weeks"]))
    payload = {
        "query": " ".join(query.lower().split()),
        "source": source,
        "filters": normalized_filters,
    }
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def can_retry_search(
    search_attempts: int,
    rewrite_count: int,
    search_key: str,
    executed_search_keys: Sequence[str] | None,
) -> bool:
    return (
        search_attempts < MAX_SEARCH_ATTEMPTS
        and rewrite_count < MAX_QUERY_REWRITES
        and search_key not in set(executed_search_keys or [])
    )


def build_rewritten_query(
    question: str,
    current_query: str,
    missing_information: Sequence[str] | None,
) -> str | None:
    """Add only missing team/week constraints to the current query."""
    del question  # Reserved for future domain-specific expansion.
    additions: list[str] = []
    for missing in missing_information or []:
        additions.extend(re.findall(r"[A-Za-z가-힣]+팀", missing))
        additions.extend(re.findall(r"\d{4}-\d{1,2}", missing))

    current_terms = current_query.split()
    seen = set(current_terms)
    for addition in additions:
        if addition not in seen:
            current_terms.append(addition)
            seen.add(addition)

    rewritten = " ".join(current_terms)
    return rewritten if rewritten != " ".join(current_query.split()) else None


def _base_search_topic(question: str) -> str:
    topic = question
    for team in _team_names(question):
        topic = topic.replace(team, " ")
    topic = re.sub(r"최근\s*\d+주(?:간)?", " ", topic)
    topic = re.sub(r"\d{4}-\d{1,2}", " ", topic)
    for phrase in (
        "비교해줘",
        "비교해서",
        "비교",
        "알려줘",
        "설명해줘",
        "정리해줘",
        "최근 추이",
        "추이",
        "이번 주",
        "이번주",
        "주요",
    ):
        topic = topic.replace(phrase, " ")
    topic = re.sub(r"(^|\s)[과와의]\s*", " ", topic)
    tokens = [token for token in topic.split() if token not in {"과", "와", "의"}]
    return " ".join(tokens).strip(" ?.!은는이가을를")


def _search_weights(question: str) -> tuple[str, float, float]:
    exact_identifier = bool(
        re.search(r"\b[A-Z][A-Z0-9_-]*\d[A-Z0-9_-]*\b", question)
        or _team_names(question)
    )
    concept_question = any(
        keyword in question.lower()
        for keyword in ("원리", "원인", "유사 사례", "개념", "뭐야", "무엇", "why")
    )
    if exact_identifier:
        return "hybrid", 0.35, 0.65
    if concept_question:
        return "semantic", 0.8, 0.2
    return "hybrid", 0.7, 0.3


def fallback_retrieval_plan(
    question: str,
    filters: dict[str, Any] | None,
) -> RetrievalPlan:
    """Build a bounded plan when structured LLM planning is unavailable."""
    filters = dict(filters or {})
    filters["weeks"] = normalize_weeks(filters.get("weeks"))
    route = fallback_route(question)
    if route == "statistics":
        return RetrievalPlan(
            intent="statistics",
            sources=["statistics"],
            filters=filters,
            search_strategy="keyword",
            vector_weight=0.0,
            keyword_weight=1.0,
        )
    if route == "general":
        return RetrievalPlan(
            intent="general",
            sources=[],
            filters=filters,
            search_strategy="hybrid",
            vector_weight=0.5,
            keyword_weight=0.5,
        )

    teams = sorted(_team_names(question))
    weeks = filters.get("weeks") or []
    topic = _base_search_topic(question) or question
    strategy, vector_weight, keyword_weight = _search_weights(question)
    technical_terms = (
        "ald",
        "cvd",
        "etch",
        "deposition",
        "cleaning",
        "pulsed",
        "wads",
    )
    technical_question = any(term in question.lower() for term in technical_terms) and any(
        keyword in question.lower()
        for keyword in ("원리", "개념", "뭐야", "무엇", "설명")
    )

    if len(teams) >= 2:
        sub_questions = [f"{team} {topic}".strip() for team in teams]
        if len(weeks) >= 4:
            sub_questions.extend(
                [
                    f"{topic} 주차별 변화 {' '.join(weeks)}".strip(),
                    f"{topic} 공통 원인 후보".strip(),
                    f"{' '.join(teams)} {topic} 비교".strip(),
                ]
            )
        sub_questions = sub_questions[:MAX_SUB_QUESTIONS]
        return RetrievalPlan(
            intent="comparison",
            sources=["mail", "wiki"],
            sub_questions=sub_questions,
            filters=filters,
            search_strategy=strategy,
            vector_weight=vector_weight,
            keyword_weight=keyword_weight,
        )

    if len(weeks) >= 2:
        sub_questions = [f"{topic} {week}" for week in weeks][:MAX_SUB_QUESTIONS]
        return RetrievalPlan(
            intent="trend",
            sources=["mail", "wiki"],
            sub_questions=sub_questions,
            filters=filters,
            search_strategy=strategy,
            vector_weight=vector_weight,
            keyword_weight=keyword_weight,
        )

    if technical_question:
        sources = ["technical_document"]
    elif filters.get("team") or teams:
        sources = ["mail", "wiki"]
    else:
        sources = ["mail", "wiki", "technical_document"]

    return RetrievalPlan(
        intent="single_search",
        sources=sources,
        sub_questions=[question],
        filters=filters,
        search_strategy=strategy,
        vector_weight=vector_weight,
        keyword_weight=keyword_weight,
    )


def build_search_tasks(plan: RetrievalPlan) -> list[SearchTask]:
    """Expand a plan into unique, bounded source-specific search tasks."""
    tasks: list[SearchTask] = []
    seen: set[str] = set()
    sub_questions = plan.sub_questions or []
    requested_teams = list(plan.filters.get("allowed_teams") or [])
    if not requested_teams:
        requested_teams = sorted(_team_names(" ".join(sub_questions)))
    for sub_question in sub_questions[:MAX_SUB_QUESTIONS]:
        sub_teams = sorted(_team_names(sub_question))
        if plan.filters.get("team"):
            teams = [str(plan.filters["team"])]
        elif plan.filters.get("allowed_teams"):
            teams = [team for team in sub_teams if team in requested_teams]
            if not teams:
                teams = requested_teams
        else:
            teams = sub_teams or requested_teams or [None]
        for team in teams:
            task_filters = {
                "team": team,
                "weeks": plan.filters.get("weeks"),
            }
            for source in plan.sources:
                search_key = make_search_key(sub_question, source, task_filters)
                if search_key in seen:
                    continue
                seen.add(search_key)
                tasks.append(
                    SearchTask(
                        query=sub_question,
                        sub_question=sub_question,
                        source=source,
                        team=team,
                        weeks=plan.filters.get("weeks"),
                        vector_weight=plan.vector_weight,
                        keyword_weight=plan.keyword_weight,
                        search_key=search_key,
                    )
                )
                if len(tasks) >= MAX_PARALLEL_SEARCH_TASKS:
                    return tasks
    return tasks


def validate_citations(
    answer: str,
    documents: Sequence[RetrievedDocument],
) -> tuple[bool, list[str]]:
    """Validate that a search answer cites only evidence available in state."""
    cited_ids = re.findall(r"\[S(\d+)\]", answer)
    available = {
        str(document.get("citation_id", "")).upper()
        for document in documents
        if document.get("citation_id")
    }
    invalid = sorted(
        {f"S{citation}" for citation in cited_ids if f"S{citation}" not in available}
    )
    return bool(cited_ids) and not invalid, invalid


def fallback_evaluate_answer(
    question: str,
    answer: str,
    documents: Sequence[RetrievedDocument],
    filters: dict[str, Any] | None = None,
) -> AnswerEvaluation:
    """Apply deterministic citation, numeric, and condition coverage checks."""
    citation_valid, invalid_citations = validate_citations(answer, documents)
    evidence_text = " ".join(
        " ".join(
            [
                document.get("title", ""),
                document.get("content", ""),
                str(document.get("team") or ""),
                str(document.get("week") or ""),
            ]
        )
        for document in documents
    )
    answer_without_citations = re.sub(r"\[S\d+\]", "", answer)
    evidence_numbers = set(re.findall(r"\d+(?:\.\d+)?", evidence_text))
    answer_numbers = set(re.findall(r"\d+(?:\.\d+)?", answer_without_citations))
    unsupported_numbers = sorted(answer_numbers - evidence_numbers)
    unsupported_claims = [f"근거에 없는 숫자: {number}" for number in unsupported_numbers]
    unsupported_claims.extend(
        f"존재하지 않는 citation: {citation}" for citation in invalid_citations
    )

    documents_by_citation = {
        str(document.get("citation_id", "")).upper(): document
        for document in documents
        if document.get("citation_id")
    }
    for claim in re.split(r"(?<=[.!?])\s+|\n+", answer):
        citation_ids = [f"S{value}" for value in re.findall(r"\[S(\d+)\]", claim)]
        cited_documents = [
            documents_by_citation[citation_id]
            for citation_id in citation_ids
            if citation_id in documents_by_citation
        ]
        if not cited_documents:
            plain_claim = " ".join(claim.replace("•", " ").split())
            exempt = bool(
                not plain_claim
                or plain_claim.startswith("((")
                or plain_claim.endswith("?")
                or any(
                    phrase in plain_claim
                    for phrase in (
                        "자료가 부족",
                        "근거가 부족",
                        "확인되지 않",
                        "찾지 못했습니다",
                    )
                )
            )
            if not exempt and not citation_ids and _grounding_terms(plain_claim):
                unsupported_claims.append(
                    f"핵심 사실 citation 누락: {plain_claim[:120]}"
                )
            continue
        cited_text = " ".join(
            " ".join(
                [
                    document.get("title", ""),
                    document.get("content", ""),
                    str(document.get("team") or ""),
                    str(document.get("week") or ""),
                ]
            )
            for document in cited_documents
        )
        claim_without_citations = re.sub(r"\[S\d+\]", "", claim)
        claim_teams = _team_names(claim_without_citations)
        claim_weeks = set(re.findall(r"\d{4}-\d{1,2}", claim_without_citations))
        claim_numbers = set(re.findall(r"\d+(?:\.\d+)?", claim_without_citations))
        cited_numbers = set(re.findall(r"\d+(?:\.\d+)?", cited_text))
        claim_codes = set(
            re.findall(
                r"\b[A-Z][A-Z0-9_-]*\d[A-Z0-9_-]*\b",
                claim_without_citations,
            )
        )
        mismatches = [
            *[team for team in claim_teams if team not in cited_text],
            *[week for week in claim_weeks if week not in cited_text],
            *[number for number in claim_numbers if number not in cited_numbers],
            *[code for code in claim_codes if code not in cited_text],
        ]
        if mismatches:
            unsupported_claims.append(
                f"{','.join(citation_ids)} 근거와 불일치: "
                f"{', '.join(sorted(set(mismatches)))}"
            )
        claim_terms = _grounding_terms(claim_without_citations)
        cited_terms = _grounding_terms(cited_text)
        lexical_overlap = len(claim_terms & cited_terms)
        if (
            claim_terms
            and (
                lexical_overlap < min(2, len(claim_terms))
                or lexical_overlap / len(claim_terms) < 0.6
            )
        ):
            unsupported_claims.append(
                f"{','.join(citation_ids)} citation 내용 불일치"
            )

    filters = filters or {}
    requested_teams = _team_names(question)
    if filters.get("team"):
        requested_teams.add(str(filters["team"]))
    requested_weeks = set(re.findall(r"\d{4}-\d{1,2}", question))
    requested_weeks.update(filters.get("weeks") or [])
    missing_answers = [
        f"{team}에 대한 답변 누락" for team in sorted(requested_teams) if team not in answer
    ]
    missing_answers.extend(
        f"{week} 주차 조건 답변 누락"
        for week in sorted(requested_weeks)
        if week not in answer
    )
    requested_condition_count = len(requested_teams) + len(requested_weeks)
    completeness_score = (
        1.0
        if not requested_condition_count
        else (requested_condition_count - len(missing_answers))
        / requested_condition_count
    )
    groundedness_score = max(
        0.0,
        1.0 - (0.4 * len(unsupported_claims)) - (0.3 if not citation_valid else 0.0),
    )
    passed = (
        citation_valid
        and groundedness_score >= 0.8
        and completeness_score >= 0.8
        and not unsupported_claims
        and not missing_answers
    )
    return AnswerEvaluation(
        groundedness_score=round(groundedness_score, 4),
        completeness_score=round(completeness_score, 4),
        citation_valid=citation_valid,
        unsupported_claims=unsupported_claims,
        missing_answers=missing_answers,
        passed=passed,
        reason=(
            "답변이 근거와 질문 조건을 충족합니다."
            if passed
            else "답변의 근거 또는 질문 조건 충족 여부를 수정해야 합니다."
        ),
    )


def wiki_save_eligible(state: dict[str, Any]) -> bool:
    """Return whether a validated answer may become a review candidate."""
    if not AUTO_WIKI_SAVE_ENABLED:
        return False
    evaluation = state.get("answer_evaluation") or {}
    answer = str(state.get("answer") or state.get("final_answer") or "")
    uncertain = any(
        phrase in answer
        for phrase in ("확인되지 않", "자료가 부족", "근거가 부족", "추정", "불확실")
    )
    return bool(
        state.get("route") == "search"
        and evaluation.get("passed")
        and state.get("groundedness_score", 0.0) >= MIN_WIKI_GROUNDEDNESS
        and state.get("citation_valid")
        and len(state.get("reranked_results", [])) >= MIN_WIKI_SOURCE_COUNT
        and state.get("wiki_duplicate") is False
        and not state.get("limited_answer")
        and not uncertain
    )
