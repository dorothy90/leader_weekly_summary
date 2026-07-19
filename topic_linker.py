from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from knowledge_models import (
    CategoryPath,
    ClassificationItem,
    StrictModel,
    TopicAssignment,
    TopicCandidate,
    TopicRelation,
    WeekRelationReviewEvent,
    WikiReview,
    WikiReviewResolution,
    WikiTopic,
)
from wiki_store import JsonWikiStore


class TopicLinkDecision(StrictModel):
    action: Literal["attach", "create", "review"]
    topic_id: str | None = None
    title: str | None = None
    rationale: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class TopicLinkProposal(StrictModel):
    action: Literal["attach", "create", "review"]
    topic_id: str | None = None
    title: str | None = None
    rationale: str
    confidence: float = Field(ge=0, le=1)
    candidates: list[TopicCandidate] = Field(default_factory=list)


DecisionFn = Callable[[ClassificationItem, Sequence[WikiTopic]], TopicLinkDecision]


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[0-9a-zA-Z가-힣]+", value.casefold()))


def _path_contains(parent: CategoryPath, child: CategoryPath) -> bool:
    return all(
        expected is None or expected == actual
        for expected, actual in (
            (parent.domain, child.domain),
            (parent.tech, child.tech),
            (parent.lotcd, child.lotcd),
        )
    )


def _taxonomy_compatible(
    target_path: CategoryPath | None,
    topic_paths: Sequence[CategoryPath],
) -> bool:
    if target_path is None:
        return False
    return any(
        _path_contains(target_path, topic_path)
        or _path_contains(topic_path, target_path)
        for topic_path in topic_paths
    )


def rank_topic_candidates(
    item: ClassificationItem,
    topics: Sequence[WikiTopic],
    limit: int = 5,
) -> list[TopicCandidate]:
    if limit <= 0:
        return []
    item_tokens = _tokens(
        " ".join((item.summary, item.topic_hint, item.classification_context))
    )
    ranked: list[TopicCandidate] = []
    for topic in topics:
        if not _taxonomy_compatible(item.decision.target_path, topic.target_paths):
            continue
        topic_tokens = _tokens(f"{topic.title} {topic.primary_area}")
        overlap = len(item_tokens & topic_tokens) / max(
            1, len(item_tokens | topic_tokens)
        )
        exact_path = item.decision.target_path in topic.target_paths
        hint_match = bool(
            item.topic_hint
            and item.topic_hint.casefold() in topic.title.casefold()
        )
        reasons = []
        if exact_path:
            reasons.append("exact taxonomy path")
        if hint_match:
            reasons.append("topic hint in title")
        if overlap:
            reasons.append(f"lexical overlap {overlap:.3f}")
        ranked.append(
            TopicCandidate(
                topic_id=topic.topic_id,
                score=(4.0 if exact_path else 0.0)
                + (2.0 if hint_match else 0.0)
                + 4.0 * overlap,
                rank_reasons=reasons,
            )
        )
    return sorted(ranked, key=lambda value: (-value.score, value.topic_id))[:limit]


def _stable_id(prefix: str, agenda_id: str) -> str:
    digest = hashlib.sha256(agenda_id.encode("utf-8")).hexdigest()[:12].upper()
    return f"{prefix}-{digest}"


def _review(
    candidates: list[TopicCandidate],
    decision: TopicLinkDecision,
    rationale: str | None = None,
) -> TopicLinkProposal:
    return TopicLinkProposal(
        action="review",
        topic_id=decision.topic_id,
        title=decision.title,
        rationale=rationale or decision.rationale,
        confidence=decision.confidence,
        candidates=candidates,
    )


def link_agenda(
    item: ClassificationItem,
    topics: Sequence[WikiTopic],
    decider: DecisionFn,
) -> TopicLinkProposal:
    candidates = rank_topic_candidates(item, topics)
    topics_by_id = {topic.topic_id: topic for topic in topics}
    if item.item_kind == "aggregate" and any(
        any(path.lotcd is not None for path in topics_by_id[value.topic_id].target_paths)
        for value in candidates
    ):
        ranked_topics = [topics_by_id[value.topic_id] for value in candidates]
        decision = TopicLinkDecision.model_validate(decider(item, ranked_topics))
        return _review(
            candidates,
            decision,
            "Aggregate Agenda cannot be auto-attached to a LOTCD-specific Topic.",
        )

    plausible = [candidate for candidate in candidates if candidate.score >= 2.0]
    if not plausible:
        return TopicLinkProposal(
            action="create",
            topic_id=_stable_id("T", item.agenda_id),
            title=item.topic_hint or item.summary,
            rationale="No existing Topic met the deterministic candidate threshold.",
            confidence=1.0,
            candidates=candidates,
        )

    ranked_topics = [topics_by_id[candidate.topic_id] for candidate in candidates]
    decision = TopicLinkDecision.model_validate(decider(item, ranked_topics))

    top = candidates[0]
    runner_up_score = candidates[1].score if len(candidates) > 1 else 0.0
    if (
        decision.action == "attach"
        and decision.topic_id == top.topic_id
        and top.score >= 5.0
        and top.score - runner_up_score >= 2.0
    ):
        return TopicLinkProposal(
            action="attach",
            topic_id=top.topic_id,
            rationale=decision.rationale,
            confidence=decision.confidence,
            candidates=candidates,
        )
    if decision.action == "create" and decision.topic_id is None:
        return TopicLinkProposal(
            action="create",
            topic_id=_stable_id("T", item.agenda_id),
            title=decision.title or item.topic_hint or item.summary,
            rationale=decision.rationale,
            confidence=decision.confidence,
            candidates=candidates,
        )
    return _review(candidates, decision)


def persist_link_proposal(
    store: JsonWikiStore,
    item: ClassificationItem,
    proposal: TopicLinkProposal,
) -> TopicAssignment | WikiReview:
    if proposal.action == "review":
        review = WikiReview(
            review_id=_stable_id("R", item.agenda_id),
            kind="assignment",
            agenda_id=item.agenda_id,
            candidates=proposal.candidates,
            rationale=proposal.rationale,
        )
        store.save_review(review)
        return review
    if proposal.topic_id is None:
        raise ValueError(f"{proposal.action} proposal requires topic_id")
    assignment = TopicAssignment(
        agenda_id=item.agenda_id,
        topic_id=proposal.topic_id,
        decision=proposal.action,
        confidence=proposal.confidence,
        rationale=proposal.rationale,
        decision_source="auto",
        decided_by="topic-linker",
        decided_at=datetime.now(UTC),
    )
    store.save_assignment(assignment)
    return assignment


def resolve_wiki_review(
    store: JsonWikiStore,
    review_id: str,
    resolution: WikiReviewResolution,
    user_id: str,
) -> WikiReview:
    store.recover_review_transitions()
    review = next(
        (value for value in store.reviews() if value.review_id == review_id),
        None,
    )
    if review is None:
        raise KeyError(review_id)
    if review.status == "resolved" and review.resolution_action == resolution.action:
        return review
    if review.status != "pending":
        raise ValueError(f"Review is not pending: {review_id}")
    if review.kind == "relation":
        if resolution.action not in {"accept", "reject"}:
            raise ValueError(f"Invalid relation resolution: {resolution.action}")
        if review.relation_id is None:
            raise ValueError("Relation review requires relation_id")
        relation = store.relation(review.relation_id)
        if relation.review_state != "pending":
            raise ValueError(f"Relation is not pending: {review.relation_id}")
        now = datetime.now(UTC)
        updated_relation = relation.model_copy(update={
            "review_state": "accepted" if resolution.action == "accept" else "rejected",
            "reviewed_by": user_id,
            "reviewed_at": now,
        })
        resolved = review.model_copy(update={
            "status": "resolved", "resolved_by": user_id,
            "resolved_at": now, "resolution_action": resolution.action,
        })
        relation_event = _relation_review_event(
            store, updated_relation, resolution.action, user_id, now
        )
        store.apply_review_transition(
            resolved, relation=updated_relation, relation_event=relation_event
        )
        return resolved
    if review.agenda_id is None:
        raise ValueError("Assignment review requires agenda_id")
    if resolution.action == "hold":
        held = review.model_copy(update={
            "status": "held", "resolved_by": user_id,
            "resolved_at": datetime.now(UTC), "resolution_action": "hold",
        })
        store.save_review(held)
        return held
    if resolution.action not in {"attach", "create"}:
        raise ValueError(f"Invalid assignment resolution: {resolution.action}")
    if resolution.action == "attach":
        if resolution.topic_id is None:
            raise ValueError("Attach resolution requires topic_id")
        if resolution.topic_id not in {
            candidate.topic_id for candidate in review.candidates
        }:
            raise ValueError("Attach resolution must select a reviewed candidate")
        topic_id = resolution.topic_id
    else:
        if not resolution.title:
            raise ValueError("Create resolution requires title")
        if resolution.topic_id is not None:
            raise ValueError("Create resolution must not supply topic_id")
        topic_id = _stable_id("T", review.agenda_id)
    now = datetime.now(UTC)
    assignment = TopicAssignment(
            agenda_id=review.agenda_id,
            topic_id=topic_id,
            decision=resolution.action,
            confidence=1.0,
            rationale=f"Manual resolution of {review.review_id}",
            decision_source="manual",
            decided_by=user_id,
            decided_at=now,
        )
    resolved = review.model_copy(update={
        "status": "resolved", "resolved_by": user_id,
        "resolved_at": now, "resolution_action": resolution.action,
    })
    store.apply_review_transition(resolved, assignment=assignment)
    return resolved


def _relation_review_event(
    store: JsonWikiStore,
    relation: TopicRelation,
    action: Literal["accept", "reject"],
    actor: str,
    reviewed_at: datetime,
) -> WeekRelationReviewEvent | None:
    origin_week = relation.creation_week
    if not origin_week and relation.created_build_run_id:
        try:
            origin_week = store.build(relation.created_build_run_id).week
        except KeyError:
            return None
    if not origin_week:
        return None
    event_action = "accepted" if action == "accept" else "rejected"
    return WeekRelationReviewEvent(
        relation_id=relation.relation_id,
        relation_kind=relation.kind,
        origin_week=origin_week,
        action=event_action,
        actor=actor,
        reviewed_at=reviewed_at,
    )


def build_link_decider() -> DecisionFn:
    from langchain_openai import ChatOpenAI

    from agenda_extract import llm_connection

    connection = llm_connection()
    llm = ChatOpenAI(
        model=connection.model,
        api_key=connection.api_key.get_secret_value(),
        base_url=connection.base_url,
        temperature=0,
    )
    structured = llm.with_structured_output(TopicLinkDecision)
    system_prompt = """You decide whether one semiconductor Agenda belongs to an existing Topic.
Return attach only when the Agenda and Topic clearly share the same issue identity.
Shared taxonomy or team alone is never sufficient. Return create for a clearly distinct
identity, and review for ambiguity or any equipment, defect, customer, experiment, or
decision identity contradiction. Use only the supplied Agenda and candidate Topics."""

    def decide(
        item: ClassificationItem,
        candidates: Sequence[WikiTopic],
    ) -> TopicLinkDecision:
        payload = {
            "agenda": item.model_dump(mode="json"),
            "candidates": [value.model_dump(mode="json") for value in candidates],
        }
        response = structured.invoke(
            [
                ("system", system_prompt),
                ("human", json.dumps(payload, ensure_ascii=False)),
            ]
        )
        return TopicLinkDecision.model_validate(response)

    return decide
