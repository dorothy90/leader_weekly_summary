"""Hybrid agenda extraction: LLM segmentation plus canonical category resolution."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from dotenv import load_dotenv

from classification_workbench import classify_context
from knowledge_models import (
    AliasRecord,
    CategoryPath,
    ClassificationDecision,
    Mail,
    TaxonomyDocument,
)
from knowledge_store import DEFAULT_DB_PATH, SQLiteKnowledgeStore


load_dotenv(Path(__file__).resolve().parent / ".env")


QUOTE_MARKERS = (
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.IGNORECASE),
    re.compile(r"^\s*-{2,}\s*Forwarded message\s*-{2,}\s*$", re.IGNORECASE),
    re.compile(r"^\s*보낸 사람\s*:", re.IGNORECASE),
)
UNKNOWN_LOTCD_PATTERN = re.compile(r"(?<![A-Z0-9])[46][A-Z][A-Z0-9]{1,4}(?![A-Z0-9])")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgendaDraft(StrictModel):
    source_quote: str = Field(min_length=1)
    classification_context: str = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=240)
    topic: str = Field(min_length=1, max_length=80)
    state: str = Field(min_length=1, max_length=80)
    item_kind: Literal["lotcd_specific", "aggregate", "unknown"]

    @model_validator(mode="after")
    def quote_must_be_inside_context(self):
        if self.source_quote not in self.classification_context:
            raise ValueError("source_quote must be inside classification_context")
        return self


class AgendaDraftList(StrictModel):
    agendas: list[AgendaDraft]


class ResolvedCategory(StrictModel):
    scope: Literal[
        "domain", "tech", "lotcd", "multi_lotcd", "cross_domain", "unknown"
    ]
    target_paths: list[CategoryPath]
    candidate_paths: list[CategoryPath] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    review_required: bool


class ExtractedAgenda(StrictModel):
    id: str
    mail_id: str
    source_quote: str
    source_start: int
    source_end: int
    classification_context: str
    summary: str
    scope: str
    target_paths: list[CategoryPath]
    candidate_paths: list[CategoryPath]
    topic: str
    state: str
    item_kind: Literal["lotcd_specific", "aggregate", "unknown"]
    decision: ClassificationDecision
    confidence: float
    review_required: bool


class MailExtractionResult(StrictModel):
    mail_id: str
    agendas: list[ExtractedAgenda]
    excluded_quoted_text: str = ""


class LLMConnection(StrictModel):
    api_key: SecretStr
    base_url: str = Field(min_length=1)
    model: str = Field(min_length=1)


def strip_quoted_history(text: str) -> tuple[str, str]:
    """Split new mail content from common reply/forward history markers."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if any(pattern.match(line) for pattern in QUOTE_MARKERS):
            active = "\n".join(lines[:index]).rstrip()
            quoted = "\n".join(lines[index:]).strip()
            return active, quoted
    return text.strip(), ""


def _contains_term(text: str, term: str) -> bool:
    pattern = re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    return bool(pattern.search(text))


def _deduplicate_paths(paths: list[CategoryPath]) -> list[CategoryPath]:
    seen: set[tuple[str, str | None, str | None]] = set()
    result: list[CategoryPath] = []
    for path in paths:
        key = (path.domain, path.tech, path.lotcd)
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


class CanonicalResolver:
    def __init__(
        self,
        taxonomy: TaxonomyDocument,
        aliases: list[AliasRecord] | None = None,
    ) -> None:
        self.taxonomy = taxonomy
        self.aliases = aliases or []
        self.lotcd_terms: list[tuple[str, list[CategoryPath]]] = []
        self.tech_terms: list[tuple[str, CategoryPath]] = []
        self.known_lotcds: set[str] = set()

        lotcd_lookup: dict[str, CategoryPath] = {}
        for domain in taxonomy.domains:
            for tech in domain.techs:
                tech_path = CategoryPath(
                    domain=domain.name, tech=tech.name, lotcd=None
                )
                self.tech_terms.append((tech.name, tech_path))
                self.tech_terms.extend((alias, tech_path) for alias in tech.aliases)
                for lotcd in tech.lotcds:
                    path = CategoryPath(
                        domain=domain.name, tech=tech.name, lotcd=lotcd.code
                    )
                    self.known_lotcds.add(lotcd.code)
                    lotcd_lookup[lotcd.code] = path
                    self.lotcd_terms.append((lotcd.code, [path]))
                    self.lotcd_terms.extend((alias, [path]) for alias in lotcd.aliases)

        for group in taxonomy.group_aliases:
            self.lotcd_terms.append(
                (
                    group.alias,
                    [lotcd_lookup[code] for code in group.target_lotcds],
                )
            )

        self.lotcd_terms.sort(key=lambda item: len(item[0]), reverse=True)
        self.tech_terms.sort(key=lambda item: len(item[0]), reverse=True)

    @staticmethod
    def _scope(paths: list[CategoryPath]) -> str:
        if not paths:
            return "unknown"
        if len({path.domain for path in paths}) > 1:
            return "cross_domain"
        lotcd_paths = [path for path in paths if path.lotcd]
        if len(lotcd_paths) > 1:
            return "multi_lotcd"
        if len(lotcd_paths) == 1:
            return "lotcd"
        tech_paths = [path for path in paths if path.tech]
        if len(tech_paths) == 1:
            return "tech"
        return "domain"

    def _fallback_domain(self, text: str, sender_team: str) -> str | None:
        combined = f"{text}\n{sender_team}"
        for domain in self.taxonomy.domains:
            if _contains_term(combined, domain.name):
                return domain.name
        return None

    def resolve(self, text: str, sender_team: str = "") -> ResolvedCategory:
        lotcd_paths: list[CategoryPath] = []
        for term, paths in self.lotcd_terms:
            if _contains_term(text, term):
                lotcd_paths.extend(paths)
        lotcd_paths = _deduplicate_paths(lotcd_paths)
        if lotcd_paths:
            return ResolvedCategory(
                scope=self._scope(lotcd_paths),
                target_paths=lotcd_paths,
                confidence=0.98,
                review_required=False,
            )

        unknown_codes = [
            code
            for code in UNKNOWN_LOTCD_PATTERN.findall(text.upper())
            if code not in self.known_lotcds
        ]
        if unknown_codes:
            fallback_domain = self._fallback_domain(text, sender_team)
            candidate_paths = (
                [
                    CategoryPath(
                        domain=fallback_domain, tech=None, lotcd=unknown_codes[0]
                    )
                ]
                if fallback_domain
                else []
            )
            return ResolvedCategory(
                scope="unknown",
                target_paths=[],
                candidate_paths=candidate_paths,
                confidence=0.42,
                review_required=True,
            )

        tech_paths = _deduplicate_paths(
            [path for term, path in self.tech_terms if _contains_term(text, term)]
        )
        if tech_paths:
            return ResolvedCategory(
                scope=self._scope(tech_paths),
                target_paths=tech_paths,
                confidence=0.97,
                review_required=False,
            )

        domain_paths = [
            CategoryPath(domain=domain.name, tech=None, lotcd=None)
            for domain in self.taxonomy.domains
            if _contains_term(text, domain.name)
        ]
        if domain_paths:
            return ResolvedCategory(
                scope=self._scope(domain_paths),
                target_paths=domain_paths,
                confidence=0.97,
                review_required=False,
            )

        return ResolvedCategory(
            scope="unknown",
            target_paths=[],
            confidence=0.25,
            review_required=True,
        )


def prior_sentence_context(body: str, quote: str) -> str:
    """Add one prior sentence for phrases such as '해당 LOT' or '오늘 조건'."""
    quote_start = body.find(quote)
    if quote_start <= 0:
        return quote
    prefix = body[:quote_start].rstrip()
    last_boundary = max(prefix.rfind(marker) for marker in (".", "!", "?", "\n"))
    earlier = prefix[:last_boundary].rstrip() if last_boundary >= 0 else ""
    prior_start = max(earlier.rfind(marker) for marker in (".", "!", "?", "\n"))
    previous = prefix[prior_start + 1 :].strip()
    return f"{previous} {quote}".strip() if previous else quote


def llm_connection() -> LLMConnection:
    knowledge_base_url = os.getenv("KNOWLEDGE_LLM_BASE_URL")
    base_url = knowledge_base_url or os.getenv("OPENROUTER_BASE_URL")
    if not base_url:
        raise RuntimeError(
            "KNOWLEDGE_LLM_BASE_URL or OPENROUTER_BASE_URL is required"
        )
    is_loopback = urlparse(base_url).hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not is_loopback
        and os.getenv("KNOWLEDGE_LLM_DATA_POLICY_ACK", "").casefold() != "true"
    ):
        raise RuntimeError(
            "External LLM use requires KNOWLEDGE_LLM_DATA_POLICY_ACK=true"
        )
    api_key = (
        os.getenv("KNOWLEDGE_LLM_API_KEY")
        or os.getenv("OPENROUTER_API_KEY")
        or "local-no-key"
    )
    model = (
        os.getenv("KNOWLEDGE_LLM_MODEL")
        or os.getenv("LLM_MODEL")
        or "z-ai/glm-4.7-flash"
    )
    return LLMConnection(
        api_key=SecretStr(api_key),
        base_url=base_url,
        model=model,
    )


def build_splitter(taxonomy: TaxonomyDocument):
    connection = llm_connection()
    llm = ChatOpenAI(
        model=connection.model,
        api_key=connection.api_key.get_secret_value(),
        base_url=connection.base_url,
        temperature=0,
    )
    structured = llm.with_structured_output(AgendaDraftList)
    category_lines = []
    for domain in taxonomy.domains:
        for tech in domain.techs:
            codes = ", ".join(lotcd.code for lotcd in tech.lotcds)
            category_lines.append(f"- {domain.name} / {tech.name}: {codes}")

    system_prompt = f"""당신은 반도체 수율 메일을 독립 agenda로 분해합니다.

분류 기준:
{chr(10).join(category_lines)}

규칙:
1. 하나의 문제, 원인, 조치, 결과, 결정은 각각 하나의 agenda로 분해합니다.
2. source_quote는 입력 메일에 정확히 존재하는 연속 원문이어야 합니다.
3. classification_context도 입력 메일에 정확히 존재하는 연속 원문이어야 하며 source_quote를 포함해야 합니다.
4. '해당 LOT', '오늘 조건'처럼 앞 문장이 필요한 경우 classification_context에 필요한 앞 문장부터 포함합니다.
5. 인사말과 서명은 제외합니다.
6. category를 새로 만들거나 추측하지 않습니다. summary만 간결히 재작성합니다.
7. topic과 state는 짧은 영문 snake_case로 작성합니다.
8. summary는 원문과 같은 언어로 작성합니다. 한국어 메일은 한국어로 요약합니다.
9. LOTCD별 값이 나뉜 표나 목록은 LOTCD 행마다 별도 agenda로 분리합니다.
10. 여러 LOTCD를 합친 하나의 수율·품질 지수는 item_kind=aggregate로 둡니다.
11. 개별 LOTCD 사실은 item_kind=lotcd_specific, 불명확하면 item_kind=unknown입니다.
"""

    def split(mail_text: str) -> AgendaDraftList:
        return structured.invoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": mail_text},
            ]
        )

    return split


def extract_mail(
    mail: Mail,
    splitter,
    resolver: CanonicalResolver,
) -> MailExtractionResult:
    active_text, quoted_text = strip_quoted_history(mail.body)
    draft_list = splitter(active_text)
    extracted: list[ExtractedAgenda] = []
    for draft in draft_list.agendas:
        if draft.source_quote not in active_text:
            raise ValueError(f"source_quote not found in mail {mail.id}")
        if draft.classification_context not in active_text:
            raise ValueError(f"classification_context not found in mail {mail.id}")
        decision = classify_context(
            draft.classification_context,
            draft.item_kind,
            resolver.taxonomy,
            resolver.aliases,
            sender_team=mail.sender_team,
        )
        resolved = resolver.resolve(draft.classification_context, mail.sender_team)
        target_paths = [decision.target_path] if decision.target_path else []
        review_required = decision.status in {
            "unclassified", "conflict", "review_required"
        }
        scope = "lotcd" if decision.target_path else "unknown"
        source_start = active_text.index(draft.source_quote)
        agenda_suffix = hashlib.sha256(draft.source_quote.encode("utf-8")).hexdigest()[:12]
        extracted.append(
            ExtractedAgenda(
                id=f"{mail.id}_agenda_{agenda_suffix}",
                mail_id=mail.id,
                source_quote=draft.source_quote,
                source_start=source_start,
                source_end=source_start + len(draft.source_quote),
                classification_context=draft.classification_context,
                summary=draft.summary,
                scope=scope,
                target_paths=target_paths,
                candidate_paths=resolved.candidate_paths,
                topic=draft.topic,
                state=draft.state,
                item_kind=draft.item_kind,
                decision=decision,
                confidence=decision.confidence,
                review_required=review_required,
            )
        )
    return MailExtractionResult(
        mail_id=mail.id,
        agendas=extracted,
        excluded_quoted_text=quoted_text,
    )


def default_resolver(db_path: Path = DEFAULT_DB_PATH) -> CanonicalResolver:
    store = SQLiteKnowledgeStore(db_path)
    return CanonicalResolver(store.taxonomy, store.aliases())
