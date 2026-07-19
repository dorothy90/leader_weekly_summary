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
    StrictModel,
    TopicAssignment,
    TopicRelation,
    TopicRevision,
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
        ):
            (self.root / name).mkdir(parents=True, exist_ok=True)

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
            self._atomic_write(
                self.root
                / "history"
                / "topics"
                / topic_id
                / f"{revision_id}.json",
                revision,
            )
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
        payload = [
            path.read_text(encoding="utf-8")
            for path in sorted(self.root.joinpath("assignments").glob("*.json"))
        ]
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
