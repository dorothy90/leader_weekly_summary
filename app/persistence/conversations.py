import re
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.evidence import Evidence, RetrievalFilters
from app.domain.errors import AppError, ErrorCode
from app.domain.policy import PolicyContext
from app.security.redaction import opaque_identifier, sanitize_text

_CONVERSATION_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_OWNER_ERROR = "대화를 찾을 수 없습니다."


def _validate_conversation_id(conversation_id: str) -> str:
    if not _CONVERSATION_ID.fullmatch(conversation_id):
        raise ValueError("invalid conversation id")
    return conversation_id


def _validate_memory_owner(
    memory: "ConversationMemory", policy: PolicyContext
) -> "ConversationMemory":
    if any(item.user_id != policy.user_id for item in memory.cited_evidence):
        raise AppError(ErrorCode.UNAUTHORIZED_RESOURCE, _OWNER_ERROR)
    return memory


def sanitize_filters_for_memory(filters: RetrievalFilters) -> RetrievalFilters:
    teams = []
    for team in filters.teams:
        safe = sanitize_text(team)
        if safe and safe not in teams:
            teams.append(safe)
    weeks = []
    for week in filters.weeks:
        safe = sanitize_text(week)
        if safe and safe not in weeks:
            weeks.append(safe)
    mail_type = sanitize_text(filters.mail_type) if filters.mail_type else None
    return RetrievalFilters(teams=teams, weeks=weeks, mail_type=mail_type)


def sanitize_evidence_for_memory(item: Evidence, policy: PolicyContext) -> Evidence:
    if item.user_id != policy.user_id:
        raise AppError(ErrorCode.UNAUTHORIZED_RESOURCE, _OWNER_ERROR)
    return item.model_copy(
        update={
            "evidence_id": opaque_identifier(item.evidence_id),
            "document_id": opaque_identifier(item.document_id),
            "parent_id": (
                opaque_identifier(item.parent_id) if item.parent_id else None
            ),
            "title": sanitize_text(item.title),
            "excerpt": sanitize_text(item.excerpt) or "[REDACTED]",
            "team": sanitize_text(item.team) if item.team else None,
            "week": sanitize_text(item.week) if item.week else None,
            "source_locator": (
                sanitize_text(item.source_locator) if item.source_locator else None
            ),
            "user_id": policy.user_id,
            "acl_decision_id": opaque_identifier(item.acl_decision_id),
            "content_hash": opaque_identifier(item.content_hash),
        }
    )


def _sanitize_memory(
    memory: "ConversationMemory", policy: PolicyContext
) -> "ConversationMemory":
    validated = _validate_memory_owner(memory, policy)
    messages = []
    for message in validated.messages:
        safe = sanitize_text(message["content"]) or "[REDACTED]"
        messages.append({"role": message["role"], "content": safe})
    return ConversationMemory(
        messages=messages,
        filters=sanitize_filters_for_memory(validated.filters),
        cited_evidence=[
            sanitize_evidence_for_memory(item, policy)
            for item in validated.cited_evidence
        ],
    )


class ConversationMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[dict[str, str]] = Field(default_factory=list, max_length=20)
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    cited_evidence: list[Evidence] = Field(default_factory=list, max_length=8)

    @field_validator("messages")
    @classmethod
    def validate_messages(cls, messages: list[dict[str, str]]):
        for message in messages:
            if set(message) != {"role", "content"}:
                raise ValueError("messages require role and content")
            if message["role"] not in {"user", "assistant"}:
                raise ValueError("invalid message role")
            if not message["content"] or len(message["content"]) > 8000:
                raise ValueError("invalid message content")
        return messages


class ConversationStore(Protocol):
    async def load(
        self, conversation_id: str, policy: PolicyContext
    ) -> ConversationMemory | None: ...

    async def save(
        self,
        conversation_id: str,
        policy: PolicyContext,
        memory: ConversationMemory,
    ) -> None: ...


class InMemoryConversationStore:
    def __init__(self):
        self.records: dict[str, tuple[str, ConversationMemory]] = {}

    async def load(
        self, conversation_id: str, policy: PolicyContext
    ) -> ConversationMemory | None:
        _validate_conversation_id(conversation_id)
        record = self.records.get(conversation_id)
        if record is None:
            return None
        if record[0] != policy.user_id:
            raise AppError(ErrorCode.UNAUTHORIZED_RESOURCE, _OWNER_ERROR)
        return _sanitize_memory(record[1], policy).model_copy(deep=True)

    async def save(
        self,
        conversation_id: str,
        policy: PolicyContext,
        memory: ConversationMemory,
    ) -> None:
        _validate_conversation_id(conversation_id)
        existing = self.records.get(conversation_id)
        if existing is not None and existing[0] != policy.user_id:
            raise AppError(ErrorCode.UNAUTHORIZED_RESOURCE, _OWNER_ERROR)
        validated = _sanitize_memory(ConversationMemory.model_validate(memory), policy)
        self.records[conversation_id] = (
            policy.user_id,
            validated.model_copy(deep=True),
        )


class MongoConversationStore:
    def __init__(self, collection):
        self.collection = collection

    async def load(
        self, conversation_id: str, policy: PolicyContext
    ) -> ConversationMemory | None:
        _validate_conversation_id(conversation_id)
        document = await self.collection.find_one(
            {"_id": conversation_id, "user_id": policy.user_id}
        )
        if document is not None:
            memory = ConversationMemory.model_validate(document.get("memory") or {})
            return _sanitize_memory(memory, policy)

        exists = await self.collection.find_one({"_id": conversation_id}, {"_id": 1})
        if exists is not None:
            raise AppError(ErrorCode.UNAUTHORIZED_RESOURCE, _OWNER_ERROR)
        return None

    async def save(
        self,
        conversation_id: str,
        policy: PolicyContext,
        memory: ConversationMemory,
    ) -> None:
        _validate_conversation_id(conversation_id)
        memory = _sanitize_memory(ConversationMemory.model_validate(memory), policy)
        existing = await self.collection.find_one(
            {"_id": conversation_id}, {"user_id": 1}
        )
        if existing is not None and existing.get("user_id") != policy.user_id:
            raise AppError(ErrorCode.UNAUTHORIZED_RESOURCE, _OWNER_ERROR)

        try:
            result = await self.collection.update_one(
                {"_id": conversation_id, "user_id": policy.user_id},
                {
                    "$set": {
                        "memory": memory.model_dump(mode="json"),
                        "updated_at": datetime.now(UTC),
                    },
                    "$setOnInsert": {
                        "conversation_id": conversation_id,
                        "user_id": policy.user_id,
                    },
                },
                upsert=True,
            )
        except Exception as error:
            # Mongo's unique _id turns an ownership race into a duplicate-key
            # failure. Re-check ownership without exposing it.
            raced = await self.collection.find_one(
                {"_id": conversation_id}, {"user_id": 1}
            )
            if raced is not None and raced.get("user_id") != policy.user_id:
                raise AppError(ErrorCode.UNAUTHORIZED_RESOURCE, _OWNER_ERROR) from None
            raise error

        if result.matched_count == 0 and result.upserted_id is None:
            raise AppError(ErrorCode.UNAUTHORIZED_RESOURCE, _OWNER_ERROR)
