from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, TypeVar

from knowledge_models import (
    ArchivedApprovedEvidence,
    ArchivedApprovedWeek,
    StrictModel,
    TopicAssignment,
    TopicRelation,
    TopicRevision,
    WeekRelationReviewEvent,
    WeekWikiView,
    WikiBuildRun,
    WikiReview,
    WikiTopic,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_WIKI_DATA_DIR = ROOT / "wiki_data"
DEFAULT_RECOVERY_THRESHOLD = timedelta(hours=1)
TRANSIENT_BUILD_STATES = {"linking", "generating", "validating"}

ModelT = TypeVar("ModelT", bound=StrictModel)


class WikiStoreLockError(RuntimeError):
    pass


def _safe_id(value: str) -> str:
    if not value or any(part in value for part in ("/", "\\", "..")):
        raise ValueError(f"Invalid identifier: {value}")
    return value


def _load(path: Path, model_type: type[ModelT]) -> ModelT:
    if not path.exists():
        raise KeyError(path.stem)
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))


class JsonWikiStore:
    def __init__(
        self,
        root: Path = DEFAULT_WIKI_DATA_DIR,
        recovery_threshold: timedelta = DEFAULT_RECOVERY_THRESHOLD,
    ) -> None:
        self.root = Path(root)
        self.recovery_threshold = recovery_threshold
        self._lock_owner: int | None = None
        for name in (
            "topics",
            "assignments",
            "relations",
            "reviews",
            "builds",
            "weeks",
            "history/topics",
            "history/weeks",
            "history/evidence",
            "transitions",
        ):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        if any(self.root.joinpath("transitions").glob("*.json")):
            self.recover_review_transitions()

    def _atomic_replace(self, path: Path, payload: str) -> None:
        if self._lock_owner != threading.get_ident():
            raise RuntimeError("Wiki mutation requires the build lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            text=True,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _atomic_write(self, path: Path, model: StrictModel) -> None:
        validated = type(model).model_validate(model.model_dump(mode="json"))
        self._atomic_replace(path, validated.model_dump_json(indent=2))

    @contextmanager
    def build_lock(self) -> Iterator[None]:
        lock = self.root / ".build.lock"
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise WikiStoreLockError("Wiki build is already running") from exc
        try:
            try:
                os.write(descriptor, str(os.getpid()).encode())
            finally:
                os.close(descriptor)
            self._lock_owner = threading.get_ident()
            yield
        finally:
            self._lock_owner = None
            lock.unlink(missing_ok=True)

    @contextmanager
    def _mutation_lock(self) -> Iterator[None]:
        if self._lock_owner == threading.get_ident():
            yield
            return
        with self.build_lock():
            yield

    def topics(self) -> list[WikiTopic]:
        return sorted(
            (
                _load(path, WikiTopic)
                for path in self.root.joinpath("topics").glob("*.json")
            ),
            key=lambda item: item.topic_id,
        )

    def topic(self, topic_id: str) -> WikiTopic:
        return _load(
            self.root / "topics" / f"{_safe_id(topic_id)}.json", WikiTopic
        )

    def topic_revision(self, topic_id: str, revision_id: str) -> TopicRevision:
        return _load(
            self.root
            / "history"
            / "topics"
            / _safe_id(topic_id)
            / f"{_safe_id(revision_id)}.json",
            TopicRevision,
        )

    def publish_topic(self, topic: WikiTopic, revision: TopicRevision) -> None:
        if (
            topic.topic_id != revision.topic_id
            or topic.current_revision_id != revision.revision_id
        ):
            raise ValueError("Topic and revision identity mismatch")
        topic_id = _safe_id(topic.topic_id)
        revision_id = _safe_id(revision.revision_id)
        with self._mutation_lock():
            revision_path = (
                self.root
                / "history"
                / "topics"
                / topic_id
                / f"{revision_id}.json"
            )
            if revision_path.exists():
                if _load(revision_path, TopicRevision) != revision:
                    raise ValueError("Topic revision history is immutable")
            else:
                self._atomic_write(revision_path, revision)
            self._atomic_write(self.root / "topics" / f"{topic_id}.json", topic)

    def assignment(self, agenda_id: str) -> TopicAssignment | None:
        path = self.root / "assignments" / f"{_safe_id(agenda_id)}.json"
        return _load(path, TopicAssignment) if path.exists() else None

    def save_assignment(self, value: TopicAssignment) -> None:
        with self._mutation_lock():
            self._atomic_write(
                self.root / "assignments" / f"{_safe_id(value.agenda_id)}.json",
                value,
            )

    def archive_evidence(self, value: ArchivedApprovedEvidence) -> ArchivedApprovedEvidence:
        parts = value.evidence_ref.split("/")
        if len(parts) != 3:
            raise ValueError("Evidence ref must be week/run/agenda")
        week, run_id, agenda_id = map(_safe_id, parts)
        if (week, run_id, agenda_id) != (
            value.week, value.classification_run_id, value.item.agenda_id
        ):
            raise ValueError("Evidence ref identity mismatch")
        path = self.root / "history" / "evidence" / week / run_id / f"{agenda_id}.json"
        with self._mutation_lock():
            if path.exists():
                return _load(path, ArchivedApprovedEvidence)
            self._atomic_write(path, value)
        return value

    def archived_evidence(self, evidence_ref: str) -> ArchivedApprovedEvidence:
        parts = evidence_ref.split("/")
        if len(parts) != 3:
            raise ValueError("Evidence ref must be week/run/agenda")
        week, run_id, agenda_id = map(_safe_id, parts)
        return _load(
            self.root / "history" / "evidence" / week / run_id / f"{agenda_id}.json",
            ArchivedApprovedEvidence,
        )

    def archive_approved_week(self, value: ArchivedApprovedWeek) -> ArchivedApprovedWeek:
        path = (
            self.root / "history" / "evidence" / _safe_id(value.week)
            / _safe_id(value.classification_run_id) / "_week.json"
        )
        with self._mutation_lock():
            if path.exists():
                return _load(path, ArchivedApprovedWeek)
            self._atomic_write(path, value)
        return value

    def archived_week(self, week: str, classification_run_id: str) -> ArchivedApprovedWeek:
        return _load(
            self.root / "history" / "evidence" / _safe_id(week)
            / _safe_id(classification_run_id) / "_week.json",
            ArchivedApprovedWeek,
        )

    def apply_review_transition(
        self,
        review: WikiReview,
        *,
        assignment: TopicAssignment | None = None,
        relation: TopicRelation | None = None,
        relation_event: WeekRelationReviewEvent | None = None,
    ) -> None:
        if (assignment is None) == (relation is None):
            raise ValueError("Review transition requires one target")
        target_kind = "assignment" if assignment is not None else "relation"
        target = assignment or relation
        journal = self.root / "transitions" / f"{_safe_id(review.review_id)}.json"
        payload = json.dumps(
            {
                "target_kind": target_kind,
                "target": target.model_dump(mode="json"),
                "review": review.model_dump(mode="json"),
                "relation_event": (
                    relation_event.model_dump(mode="json")
                    if relation_event is not None
                    else None
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        with self._mutation_lock():
            self._atomic_replace(journal, payload)
            self._apply_transition_payload(json.loads(payload))
            journal.unlink(missing_ok=True)

    def _apply_transition_payload(self, payload: dict[str, Any]) -> None:
        review = WikiReview.model_validate(payload["review"])
        if payload.get("relation_event") is not None:
            self._merge_relation_event(
                WeekRelationReviewEvent.model_validate(payload["relation_event"])
            )
        if payload["target_kind"] == "assignment":
            target = TopicAssignment.model_validate(payload["target"])
            self._atomic_write(
                self.root / "assignments" / f"{_safe_id(target.agenda_id)}.json",
                target,
            )
        elif payload["target_kind"] == "relation":
            target = TopicRelation.model_validate(payload["target"])
            self._atomic_write(
                self.root / "relations" / f"{_safe_id(target.relation_id)}.json",
                target,
            )
        else:
            raise ValueError("Unknown review transition target")
        self._atomic_write(
            self.root / "reviews" / f"{_safe_id(review.review_id)}.json", review
        )

    def _merge_relation_event(self, event: WeekRelationReviewEvent) -> None:
        if not event.origin_week:
            return
        current_path = (
            self.root / "weeks" / f"{_safe_id(event.origin_week)}.json"
        )
        if not current_path.exists():
            return
        current = _load(current_path, WeekWikiView)
        if any(
            value.relation_id == event.relation_id
            and value.action == event.action
            for value in current.relation_review_events
        ):
            return
        events = sorted(
            [*current.relation_review_events, event],
            key=lambda value: (
                value.reviewed_at, value.relation_id, value.action
            ),
        )
        relation_ids = sorted({
            *current.new_relation_ids,
            *([event.relation_id] if event.action == "accepted" else []),
        })
        contradictions = sorted({
            *current.contradictions,
            *(
                [event.relation_id]
                if event.action == "accepted"
                and event.relation_kind == "contradicts"
                else []
            ),
        })
        hash_payload = current.model_dump(
            mode="json", exclude={"revision_id", "published_at"}
        )
        hash_payload.update({
            "new_relation_ids": relation_ids,
            "contradictions": contradictions,
            "relation_review_events": [
                value.model_dump(mode="json") for value in events
            ],
        })
        revision_id = "WREV-" + hashlib.sha256(
            json.dumps(hash_payload, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16].upper()
        updated = current.model_copy(update={
            "revision_id": revision_id,
            "published_at": max(current.published_at, event.reviewed_at),
            "new_relation_ids": relation_ids,
            "contradictions": contradictions,
            "relation_review_events": events,
        })
        history_path = (
            self.root / "history" / "weeks" / _safe_id(event.origin_week)
            / f"{_safe_id(current.revision_id)}.json"
        )
        if not history_path.exists():
            self._atomic_write(history_path, current)
        self._atomic_write(current_path, updated)

    def recover_review_transitions(self) -> list[str]:
        recovered: list[str] = []
        with self._mutation_lock():
            for path in sorted(self.root.joinpath("transitions").glob("*.json")):
                payload = json.loads(path.read_text(encoding="utf-8"))
                self._apply_transition_payload(payload)
                recovered.append(path.stem)
                path.unlink(missing_ok=True)
        return recovered

    def save_relation(self, value: TopicRelation) -> None:
        with self._mutation_lock():
            self._atomic_write(
                self.root / "relations" / f"{_safe_id(value.relation_id)}.json",
                value,
            )

    def relation(self, relation_id: str) -> TopicRelation:
        return _load(
            self.root / "relations" / f"{_safe_id(relation_id)}.json",
            TopicRelation,
        )

    def relations(self) -> list[TopicRelation]:
        return sorted(
            (
                _load(path, TopicRelation)
                for path in self.root.joinpath("relations").glob("*.json")
            ),
            key=lambda item: item.relation_id,
        )

    def reviews(self, status: str | None = None) -> list[WikiReview]:
        values = [
            _load(path, WikiReview)
            for path in self.root.joinpath("reviews").glob("*.json")
        ]
        return sorted(
            (item for item in values if status is None or item.status == status),
            key=lambda item: item.review_id,
        )

    def save_review(self, value: WikiReview) -> None:
        with self._mutation_lock():
            self._atomic_write(
                self.root / "reviews" / f"{_safe_id(value.review_id)}.json", value
            )

    def save_build(self, value: WikiBuildRun) -> None:
        with self._mutation_lock():
            self._atomic_write(
                self.root / "builds" / f"{_safe_id(value.run_id)}.json", value
            )

    def build(self, run_id: str) -> WikiBuildRun:
        return _load(
            self.root / "builds" / f"{_safe_id(run_id)}.json", WikiBuildRun
        )

    def successful_build(self, input_hash: str) -> WikiBuildRun | None:
        builds = (
            _load(path, WikiBuildRun)
            for path in self.root.joinpath("builds").glob("*.json")
        )
        return next(
            (
                item
                for item in builds
                if item.input_hash == input_hash and item.status == "published"
            ),
            None,
        )

    def assignment_digest(self) -> str:
        payload = []
        for directory in ("assignments", "relations", "reviews"):
            payload.extend(
                path.read_text(encoding="utf-8")
                for path in sorted(self.root.joinpath(directory).glob("*.json"))
            )
        return hashlib.sha256("\n".join(payload).encode("utf-8")).hexdigest()

    def start_build(
        self,
        week: str,
        classification_run_id: str,
        input_hash: str,
        model: str,
        *,
        taxonomy_version: int,
    ) -> WikiBuildRun:
        run = WikiBuildRun(
            run_id=uuid.uuid4().hex,
            week=week,
            classification_run_id=classification_run_id,
            taxonomy_version=taxonomy_version,
            status="linking",
            input_hash=input_hash,
            model=model,
            started_at=datetime.now(UTC),
        )
        self.save_build(run)
        return run

    def finish_build(
        self,
        run: WikiBuildRun,
        status: str,
        failed_topic_ids: list[str] | None = None,
    ) -> WikiBuildRun:
        finished = run.model_copy(
            update={
                "status": status,
                "failed_topic_ids": failed_topic_ids or [],
                "completed_at": datetime.now(UTC),
            }
        )
        self.save_build(finished)
        return finished

    def save_week(self, value: WeekWikiView) -> None:
        week = _safe_id(value.week)
        current = self.root / "weeks" / f"{week}.json"
        with self._mutation_lock():
            if current.exists():
                previous = _load(current, WeekWikiView)
                self._atomic_write(
                    self.root
                    / "history"
                    / "weeks"
                    / week
                    / f"{_safe_id(previous.revision_id)}.json",
                    previous,
                )
            self._atomic_write(current, value)

    def week(self, week: str) -> WeekWikiView:
        return _load(
            self.root / "weeks" / f"{_safe_id(week)}.json", WeekWikiView
        )

    def weeks(self) -> list[str]:
        return sorted(path.stem for path in self.root.joinpath("weeks").glob("*.json"))

    def rebuild_catalog(self) -> list[dict[str, Any]]:
        with self._mutation_lock():
            catalog = [topic.model_dump(mode="json") for topic in self.topics()]
            self._atomic_replace(
                self.root / "catalog.json",
                json.dumps(catalog, ensure_ascii=False, indent=2),
            )
        return catalog

    def recover_incomplete_builds(
        self, now: datetime | None = None
    ) -> list[WikiBuildRun]:
        recovered: list[WikiBuildRun] = []
        recovered_at = now or datetime.now(UTC)
        cutoff = recovered_at - self.recovery_threshold
        with self._mutation_lock():
            for path in sorted(self.root.joinpath("builds").glob("*.json")):
                run = _load(path, WikiBuildRun)
                if (
                    run.status not in TRANSIENT_BUILD_STATES
                    or run.started_at >= cutoff
                ):
                    continue
                failed = run.model_copy(
                    update={
                        "status": "failed",
                        "completed_at": recovered_at,
                        "error": "interrupted build",
                    }
                )
                self.save_build(failed)
                recovered.append(failed)
        return recovered
