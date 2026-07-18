from __future__ import annotations

import hashlib
import json
import os
import uuid
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TYPE_CHECKING

from pydantic import Field

from knowledge_models import (
    AliasRecord,
    CategoryPath,
    ClassificationDecision,
    ClassificationItem,
    ClassificationRun,
    ItemSplitPart,
    LearnedAliasRule,
    RunComparison,
    RunItemChange,
    StrictModel,
    TaxonomyDocument,
    WeekClassificationSummary,
)

if TYPE_CHECKING:
    from agenda_extract import MailExtractionResult
    from knowledge_models import Mail


ROOT = Path(__file__).resolve().parent
DEFAULT_RULES_PATH = ROOT / "config" / "classification_rules.json"
DEFAULT_DATA_DIR = ROOT / "classification_data"
UNRESOLVED = {"unclassified", "conflict", "review_required"}


class WeekDocument(StrictModel):
    week: str
    workflow_state: str
    active_run_id: str | None = None
    approved_at: datetime | None = None
    approved_by: str | None = None
    runs: dict[str, ClassificationRun] = Field(default_factory=dict)
    items: dict[str, ClassificationItem] = Field(default_factory=dict)
    revisions: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


class JsonClassificationStore:
    def __init__(
        self,
        data_dir: Path = DEFAULT_DATA_DIR,
        rules_path: Path = DEFAULT_RULES_PATH,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.rules_path = Path(rules_path)
        self.history_dir = self.data_dir / "history"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        if not self.rules_path.exists():
            raise FileNotFoundError(self.rules_path)
        self._load_rules()

    @staticmethod
    def _atomic_write(path: Path, payload: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, path)

    def _week_path(self, week: str) -> Path:
        if not week or any(part in week for part in ("/", "\\", "..")):
            raise ValueError(f"Invalid week: {week}")
        return self.data_dir / f"{week}.json"

    def _history_path(self, week: str, run_id: str) -> Path:
        return self.history_dir / week / f"{run_id}.json"

    def _load_rules(self) -> TaxonomyDocument:
        return TaxonomyDocument.model_validate_json(
            self.rules_path.read_text(encoding="utf-8")
        )

    def _save_rules(self, rules: TaxonomyDocument) -> None:
        validated = TaxonomyDocument.model_validate(rules.model_dump(mode="json"))
        self._atomic_write(
            self.rules_path,
            json.dumps(validated.model_dump(mode="json"), ensure_ascii=False, indent=2),
        )

    @property
    def taxonomy(self) -> TaxonomyDocument:
        return self._load_rules()

    def _load_week(self, week: str) -> WeekDocument:
        path = self._week_path(week)
        if not path.exists():
            raise KeyError(week)
        return WeekDocument.model_validate_json(path.read_text(encoding="utf-8"))

    def _save_week(self, document: WeekDocument) -> None:
        validated = WeekDocument.model_validate(document.model_dump(mode="json"))
        self._atomic_write(
            self._week_path(document.week),
            json.dumps(validated.model_dump(mode="json"), ensure_ascii=False, indent=2),
        )

    def _archive(self, document: WeekDocument) -> None:
        if not document.active_run_id:
            return
        self._atomic_write(
            self._history_path(document.week, document.active_run_id),
            json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2),
        )

    def aliases(self) -> list[AliasRecord]:
        rules = self.taxonomy
        records: list[AliasRecord] = []
        next_id = 1
        lotcd_paths: dict[str, CategoryPath] = {}
        for domain in rules.domains:
            for tech in domain.techs:
                for lotcd in tech.lotcds:
                    path = CategoryPath(
                        domain=domain.name, tech=tech.name, lotcd=lotcd.code
                    )
                    lotcd_paths[lotcd.code] = path
                    for value in lotcd.aliases:
                        records.append(
                            AliasRecord(id=next_id, value=value, target_paths=[path])
                        )
                        next_id += 1
        for group in rules.group_aliases:
            records.append(
                AliasRecord(
                    id=next_id,
                    value=group.alias,
                    target_paths=[lotcd_paths[code] for code in group.target_lotcds],
                )
            )
            next_id += 1
        for learned in rules.learned_aliases:
            path = lotcd_paths.get(learned.lotcd)
            if path is None:
                continue
            records.append(
                AliasRecord(
                    id=zlib.crc32(
                        f"{learned.value}:{learned.lotcd}".encode("utf-8")
                    ),
                    value=learned.value,
                    target_paths=[path],
                    origin_agenda_id=learned.origin_agenda_id,
                    context_domain=learned.context_domain,
                    context_tech=learned.context_tech,
                )
            )
        return records

    def week_summary(self, week: str) -> WeekClassificationSummary:
        document = self._load_week(week)
        counts: dict[str, int] = {}
        for item in document.items.values():
            status = item.decision.status
            counts[status] = counts.get(status, 0) + 1
        return WeekClassificationSummary(
            week=week,
            workflow_state=document.workflow_state,
            active_run_id=document.active_run_id,
            counts=counts,
        )

    def week_summaries(self) -> list[WeekClassificationSummary]:
        summaries = []
        for path in sorted(self.data_dir.glob("*.json")):
            summaries.append(self.week_summary(path.stem))
        return summaries

    def classification_items(self, week: str) -> list[ClassificationItem]:
        document = self._load_week(week)
        return sorted(document.items.values(), key=lambda item: item.agenda_id)

    def start_classification_run(
        self,
        week: str,
        prompt_version: str,
        classifier_version: str,
        *,
        rerun: bool = False,
    ) -> ClassificationRun:
        try:
            current = self._load_week(week)
        except KeyError:
            current = None
        if current and current.workflow_state == "approved":
            raise ValueError(f"Week {week} cannot start while approved")
        if current and current.workflow_state == "revalidation_required" and not rerun:
            raise ValueError(f"Week {week} cannot start while revalidation_required")
        if current and current.active_run_id:
            active = current.runs.get(current.active_run_id)
            if active and active.status == "processing":
                raise ValueError(f"Week {week} is already processing")
            self._archive(current)
        prior_run_id = current.active_run_id if current else None
        rules = self.taxonomy
        run = ClassificationRun(
            id=uuid.uuid4().hex,
            week=week,
            status="processing",
            prompt_version=prompt_version,
            classifier_version=classifier_version,
            taxonomy_version=rules.version,
            alias_version=rules.version,
            prior_run_id=prior_run_id,
            started_at=datetime.now(UTC),
        )
        self._save_week(
            WeekDocument(
                week=week,
                workflow_state="processing",
                active_run_id=run.id,
                runs={run.id: run},
            )
        )
        return run

    def _active(self, run_id: str) -> tuple[WeekDocument, ClassificationRun]:
        for summary in self.week_summaries():
            document = self._load_week(summary.week)
            run = document.runs.get(run_id)
            if run is None:
                continue
            if run.status != "processing" or document.active_run_id != run_id:
                raise ValueError(f"Classification run is not active: {run_id}")
            return document, run
        raise KeyError(run_id)

    def save_classified_extraction(
        self, run_id: str, mail: "Mail", result: "MailExtractionResult"
    ) -> dict[str, int]:
        document, _run = self._active(run_id)
        for agenda in result.agendas:
            document.items[agenda.id] = ClassificationItem(
                agenda_id=agenda.id,
                mail_id=mail.id,
                summary=agenda.summary,
                source_quote=agenda.source_quote,
                classification_context=agenda.classification_context,
                item_kind=agenda.item_kind,
                decision=agenda.decision,
                revision_count=len(document.revisions.get(agenda.id, [])),
            )
        self._save_week(document)
        return {"saved": len(result.agendas)}

    def finish_classification_run(self, run_id: str) -> WeekClassificationSummary:
        document, run = self._active(run_id)
        unresolved = any(
            item.decision.status in UNRESOLVED for item in document.items.values()
        )
        state = (
            "review_in_progress"
            if not document.items or unresolved
            else "ready_for_approval"
        )
        document.runs[run_id] = run.model_copy(
            update={
                "status": "completed",
                "completed_at": datetime.now(UTC),
                "error": None,
            }
        )
        document.workflow_state = state
        self._save_week(document)
        return self.week_summary(document.week)

    def fail_classification_run(
        self,
        run_id: str,
        error: str | None = None,
        *,
        stage: str | None = None,
        mail_directory: str | None = None,
        exception_type: str | None = None,
        message: str | None = None,
    ) -> WeekClassificationSummary:
        document, run = self._active(run_id)
        if stage is not None:
            error = json.dumps(
                {
                    "stage": stage,
                    "mail_directory": mail_directory,
                    "exception_type": exception_type,
                    "message": message,
                },
                ensure_ascii=False,
            )
        if error is None:
            raise ValueError("Classification failure requires error details")
        document.runs[run_id] = run.model_copy(
            update={
                "status": "failed",
                "completed_at": datetime.now(UTC),
                "error": error,
            }
        )
        document.workflow_state = "failed"
        self._save_week(document)
        return self.week_summary(document.week)

    def _find_item(self, agenda_id: str) -> tuple[WeekDocument, ClassificationItem]:
        for summary in self.week_summaries():
            document = self._load_week(summary.week)
            item = document.items.get(agenda_id)
            if item is not None:
                return document, item
        raise KeyError(agenda_id)

    @staticmethod
    def _ensure_mutable(document: WeekDocument) -> None:
        if document.workflow_state not in {"review_in_progress", "ready_for_approval"}:
            raise ValueError(
                f"Cannot modify item in {document.workflow_state} week"
            )

    def _lotcd_path(self, lotcd: str) -> CategoryPath:
        for domain in self.taxonomy.domains:
            for tech in domain.techs:
                for item in tech.lotcds:
                    if item.code == lotcd:
                        return CategoryPath(
                            domain=domain.name, tech=tech.name, lotcd=item.code
                        )
        raise ValueError(f"Unknown LOTCD: {lotcd}")

    def _record_revision(
        self,
        document: WeekDocument,
        before: ClassificationItem,
        after: ClassificationItem,
        changed_by: str,
        reason: str,
    ) -> None:
        revisions = document.revisions.setdefault(before.agenda_id, [])
        revisions.append(
            {
                "before": before.model_dump(mode="json"),
                "after": after.model_dump(mode="json"),
                "changed_at": datetime.now(UTC).isoformat(),
                "changed_by": changed_by,
                "reason": reason,
            }
        )
        document.items[after.agenda_id] = after.model_copy(
            update={"revision_count": len(revisions)}
        )

    def _recalculate(self, document: WeekDocument) -> None:
        document.workflow_state = (
            "review_in_progress"
            if any(i.decision.status in UNRESOLVED for i in document.items.values())
            else "ready_for_approval"
        )

    def correct_classification(
        self, agenda_id: str, lotcd: str, changed_by: str, reason: str
    ) -> ClassificationItem:
        document, before = self._find_item(agenda_id)
        self._ensure_mutable(document)
        path = self._lotcd_path(lotcd)
        after = before.model_copy(
            update={
                "item_kind": "lotcd_specific",
                "decision": ClassificationDecision(
                    status="manually_corrected",
                    target_path=path,
                    matches=[],
                    diagnostics=[],
                    confidence=1.0,
                ),
            }
        )
        self._record_revision(document, before, after, changed_by, reason)
        self._recalculate(document)
        self._save_week(document)
        return document.items[agenda_id]

    def set_item_disposition(
        self, agenda_id: str, status: str, changed_by: str, reason: str
    ) -> ClassificationItem:
        if status not in {"aggregate", "excluded"}:
            raise ValueError(f"Unknown disposition: {status}")
        document, before = self._find_item(agenda_id)
        self._ensure_mutable(document)
        after = before.model_copy(
            update={
                "item_kind": "aggregate" if status == "aggregate" else "unknown",
                "decision": ClassificationDecision(
                    status=status,
                    matches=[],
                    diagnostics=["AGGREGATE_METRIC"] if status == "aggregate" else [],
                    confidence=1.0,
                ),
            }
        )
        self._record_revision(document, before, after, changed_by, reason)
        self._recalculate(document)
        self._save_week(document)
        return document.items[agenda_id]

    def split_classification_item(
        self,
        agenda_id: str,
        parts: list[ItemSplitPart],
        changed_by: str,
        reason: str,
    ) -> list[ClassificationItem]:
        if len(parts) < 2:
            raise ValueError("Split requires at least two parts")
        document, original = self._find_item(agenda_id)
        self._ensure_mutable(document)
        ranges = []
        cursor = 0
        for part in parts:
            start = original.classification_context.find(part.source_quote, cursor)
            if start < 0:
                raise ValueError(f"Split quote not found: {part.source_quote}")
            end = start + len(part.source_quote)
            ranges.append((start, end))
            cursor = end
        if any(left[1] > right[0] for left, right in zip(ranges, ranges[1:])):
            raise ValueError("Split quotes overlap")
        children = []
        for part in parts:
            path = self._lotcd_path(part.lotcd)
            child_id = hashlib.sha256(
                f"{agenda_id}\0{part.source_quote}\0{part.lotcd}".encode("utf-8")
            ).hexdigest()[:24]
            if child_id in document.items:
                raise ValueError(f"Duplicate split item: {child_id}")
            child = ClassificationItem(
                agenda_id=child_id,
                mail_id=original.mail_id,
                summary=part.summary,
                source_quote=part.source_quote,
                classification_context=part.source_quote,
                item_kind="lotcd_specific",
                decision=ClassificationDecision(
                    status="manually_corrected",
                    target_path=path,
                    matches=[],
                    diagnostics=[],
                    confidence=1.0,
                ),
                revision_count=0,
            )
            document.items[child_id] = child
            children.append(child)
        excluded = original.model_copy(
            update={
                "item_kind": "unknown",
                "decision": ClassificationDecision(
                    status="excluded", matches=[], diagnostics=[], confidence=1.0
                ),
            }
        )
        self._record_revision(document, original, excluded, changed_by, reason)
        self._recalculate(document)
        self._save_week(document)
        return children

    def approve_week(self, week: str, approved_by: str) -> WeekClassificationSummary:
        document = self._load_week(week)
        if document.workflow_state != "ready_for_approval":
            raise ValueError(f"Week {week} is not ready for approval")
        document.workflow_state = "approved"
        document.approved_at = datetime.now(UTC)
        document.approved_by = approved_by
        self._save_week(document)
        return self.week_summary(week)

    def create_learned_alias(
        self,
        value: str,
        lotcd: str,
        origin_agenda_id: str,
        changed_by: str,
        context_domain: str | None = None,
        context_tech: str | None = None,
    ) -> AliasRecord:
        path = self._lotcd_path(lotcd)
        rules = self.taxonomy
        normalized = value.strip().casefold()
        if any(alias.value.casefold() == normalized for alias in self.aliases()):
            raise ValueError(f"Alias already exists: {value.strip()}")
        rules.learned_aliases.append(
            LearnedAliasRule(
                value=value.strip(),
                lotcd=lotcd,
                origin_agenda_id=origin_agenda_id,
                context_domain=context_domain,
                context_tech=context_tech,
            )
        )
        rules.version += 1
        self._save_rules(rules)
        for summary in self.week_summaries():
            if summary.workflow_state == "approved":
                document = self._load_week(summary.week)
                document.workflow_state = "revalidation_required"
                self._save_week(document)
        return AliasRecord(
            id=zlib.crc32(f"{value}:{lotcd}".encode("utf-8")),
            value=value.strip(),
            target_paths=[path],
            origin_agenda_id=origin_agenda_id,
            context_domain=context_domain,
            context_tech=context_tech,
        )

    def _run_document(self, run_id: str) -> WeekDocument:
        for summary in self.week_summaries():
            document = self._load_week(summary.week)
            if run_id in document.runs:
                return document
        for path in self.history_dir.glob(f"*/{run_id}.json"):
            return WeekDocument.model_validate_json(path.read_text(encoding="utf-8"))
        raise KeyError(run_id)

    def compare_runs(self, old_run_id: str, new_run_id: str) -> RunComparison:
        old = self._run_document(old_run_id).items
        new = self._run_document(new_run_id).items
        changed = []
        unchanged = 0
        for agenda_id in sorted(old.keys() | new.keys()):
            before = old.get(agenda_id)
            after = new.get(agenda_id)
            before_value = (
                (before.decision.status, before.decision.target_path.lotcd if before.decision.target_path else None)
                if before else None
            )
            after_value = (
                (after.decision.status, after.decision.target_path.lotcd if after.decision.target_path else None)
                if after else None
            )
            if before_value == after_value:
                unchanged += 1
                continue
            changed.append(
                RunItemChange(
                    agenda_id=agenda_id,
                    before_status=before_value[0] if before_value else None,
                    after_status=after_value[0] if after_value else None,
                    before_lotcd=before_value[1] if before_value else None,
                    after_lotcd=after_value[1] if after_value else None,
                )
            )
        return RunComparison(
            old_run_id=old_run_id,
            new_run_id=new_run_id,
            changed=changed,
            unchanged_count=unchanged,
        )
