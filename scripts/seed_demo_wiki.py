from __future__ import annotations

import argparse
import html
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from classification_store import WeekDocument
from knowledge_models import (
    ArchivedApprovedEvidence,
    ArchivedApprovedWeek,
    CategoryPath,
    ClassificationDecision,
    ClassificationItem,
    ClassificationRun,
    MailDocument,
    SupportedClaim,
    TopicAssignment,
    TopicCandidate,
    TopicRelation,
    TopicRevision,
    TopicSection,
    WeekWikiView,
    WikiBuildRun,
    WikiReview,
    WikiTopic,
)
from wiki_store import JsonWikiStore


FIXTURE_PATH = ROOT / "fixtures" / "knowledge" / "mails.json"
MANIFEST_NAME = ".demo-wiki-manifest.json"
STAMP = datetime(2026, 7, 20, 3, 0, tzinfo=UTC)


@dataclass(frozen=True)
class DemoSeedSummary:
    topic_count: int
    relation_count: int
    week_count: int
    generated_paths: tuple[str, ...]


@dataclass(frozen=True)
class TopicSpec:
    title: str
    week: str
    state: str
    importance: str
    area: str
    teams: tuple[str, ...]
    paths: tuple[tuple[str, str, str], ...]
    section_title: str


TOPIC_SPECS = (
    TopicSpec("4SA chamber A 편차와 수율 하락", "2026-W28", "investigating", "critical", "yield_defect", ("Spica수율", "공정기술PTE"), (("DRAM", "Spica", "4SA"),), "현재 상태"),
    TopicSpec("4SA 장비 조건 원복 검증", "2026-W28", "action_in_progress", "high", "process_equipment", ("공정기술PTE",), (("DRAM", "Spica", "4SA"),), "조치와 의사결정"),
    TopicSpec("Spica Edge defect 세정 recipe", "2026-W28", "monitoring", "high", "experiment_validation", ("Spica수율", "DRAM수율전략"), (("DRAM", "Spica", "4SA"), ("DRAM", "Spica", "6SA")), "LOTCD별 차이"),
    TopicSpec("검사 장비 calibration 영향", "2026-W28", "monitoring", "medium", "process_equipment", ("공정기술PTE", "NAND품질PTE"), (("DRAM", "Spica", "4SA"), ("NAND", "Heraion", "4H1")), "팀별 기여"),
    TopicSpec("Canopus 4SS Test fail 증가", "2026-W29", "reopened", "high", "quality_analysis", ("DRAM수율전략", "분석FA"), (("DRAM", "Canopus", "4SS"),), "재발 상태"),
    TopicSpec("Canopus 6SS 관리 범위 회복", "2026-W29", "resolved", "medium", "yield_defect", ("DRAM수율전략",), (("DRAM", "Canopus", "6SS"),), "해결 결과"),
    TopicSpec("Heraion channel hole 편차", "2026-W29", "investigating", "critical", "yield_defect", ("Heraion양산수율", "공정기술PTE"), (("NAND", "Heraion", "4H1"),), "원인과 영향"),
    TopicSpec("6H1 조건 최적화 split", "2026-W29", "action_in_progress", "high", "experiment_validation", ("Heraion양산수율",), (("NAND", "Heraion", "6H1"),), "실험 계획"),
    TopicSpec("Colosseum word line 저항 상관성", "2026-W30", "investigating", "high", "quality_analysis", ("NAND FA PTE", "NAND품질PTE"), (("NAND", "Colosseum", "4C1"), ("NAND", "Colosseum", "6C1")), "분석 결과"),
    TopicSpec("DRAM/NAND 공통 검사 중지 계획", "2026-W30", "monitoring", "medium", "schedule_delivery", ("공정기술PTE", "DRAM수율전략", "Heraion양산수율"), (("DRAM", "Spica", "4SA"), ("NAND", "Heraion", "4H1")), "일정"),
    TopicSpec("Petra 4P1 read 성능 검증", "2026-W30", "monitoring", "medium", "experiment_validation", ("NAND품질PTE",), (("NAND", "Petra", "4P1"),), "검증 결과"),
    TopicSpec("Lucy 6E2 저온 retention fail", "2026-W30", "review_required", "high", "quality_analysis", ("DRAM SRT 개발공정",), (("DRAM", "Lucy", "6E2"),), "열린 질문"),
)

RELATION_SPECS = (
    (1, 2, "possible_cause", "accepted", 0.94),
    (1, 3, "shares_condition", "accepted", 0.82),
    (2, 4, "measurement_effect", "accepted", 0.78),
    (4, 7, "affects", "accepted", 0.75),
    (7, 8, "follow_up", "accepted", 0.91),
    (8, 9, "supports", "accepted", 0.72),
    (5, 6, "comparison", "accepted", 0.86),
    (10, 11, "supports", "accepted", 0.69),
    (3, 5, "shares_condition", "pending", 0.58),
)


def _topic_id(index: int) -> str:
    return f"DEMO-TOPIC-{index:02d}"


def _agenda_id(index: int) -> str:
    return f"DEMO-AGENDA-{index:02d}"


def _run_id(week: str) -> str:
    return f"DEMO-CLASS-{week[-2:]}"


def load_fixture_mails() -> list[dict[str, object]]:
    document = MailDocument.model_validate_json(FIXTURE_PATH.read_text(encoding="utf-8"))
    return [mail.model_dump(mode="json") for mail in document.mails]


def _manifest_path(wiki_data_dir: Path) -> Path:
    return wiki_data_dir / MANIFEST_NAME


def _existing_files(root: Path) -> set[Path]:
    if not root.exists():
        return set()
    return {path for path in root.rglob("*") if path.is_file()}


def validate_demo_destination(
    wiki_data_dir: Path,
    classification_data_dir: Path,
    replace_demo: bool,
) -> set[Path]:
    manifest_path = _manifest_path(wiki_data_dir)
    existing = _existing_files(wiki_data_dir) | _existing_files(classification_data_dir)
    if not existing:
        return set()
    if not manifest_path.exists():
        raise RuntimeError("destination contains non-demo files")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    owned = {
        (wiki_data_dir if entry["root"] == "wiki" else classification_data_dir)
        / entry["path"]
        for entry in manifest["files"]
    }
    unowned = existing - owned - {manifest_path}
    if unowned:
        raise RuntimeError("destination contains non-demo files")
    if not replace_demo:
        raise RuntimeError("demo data already exists; pass --replace-demo")
    return owned


def _remove_owned(paths: set[Path]) -> None:
    for path in sorted(paths, key=lambda item: len(item.parts), reverse=True):
        path.unlink(missing_ok=True)


def _classification_item(index: int, spec: TopicSpec, mail: dict[str, object]) -> ClassificationItem:
    agenda_id = _agenda_id(index)
    quote = str(mail["body"]).split("\n", 1)[0]
    path = spec.paths[0]
    return ClassificationItem(
        agenda_id=agenda_id,
        mail_id=str(mail["id"]),
        summary=spec.title,
        source_quote=quote,
        classification_context=quote,
        item_kind="lotcd_specific",
        decision=ClassificationDecision(
            status="confirmed",
            target_path=CategoryPath(domain=path[0], tech=path[1], lotcd=path[2]),
            confidence=0.96,
        ),
        revision_count=0,
        team=spec.teams[0],
        subject=str(mail["subject"]),
        received_at=mail["received_at"],
        source_path=f"{spec.week}/{spec.teams[0]}/{mail['id']}/combined.txt",
        topic_hint=spec.title,
        state_hint=spec.state,
    )


def _write_demo_mail_html(
    mail_data_dir: Path,
    items: dict[str, ClassificationItem],
    mails: list[dict[str, object]],
) -> None:
    for index, mail in enumerate(mails, start=1):
        item = items[_agenda_id(index)]
        directory = mail_data_dir / Path(item.source_path or "").parent
        directory.mkdir(parents=True, exist_ok=True)
        paragraphs = "".join(
            f"<p>{html.escape(line)}</p>"
            for line in str(mail["body"]).splitlines()
            if line.strip()
        )
        (directory / "body.html").write_text(
            "<!doctype html><html><body>"
            f"<h1>{html.escape(str(mail['subject']))}</h1>{paragraphs}"
            "</body></html>",
            encoding="utf-8",
        )


def _write_classification_weeks(
    classification_data_dir: Path,
    items: dict[str, ClassificationItem],
) -> None:
    classification_data_dir.mkdir(parents=True, exist_ok=True)
    for week in ("2026-W28", "2026-W29", "2026-W30"):
        run_id = _run_id(week)
        week_items = {
            item.agenda_id: item
            for index, item in enumerate(items.values(), start=1)
            if TOPIC_SPECS[index - 1].week == week
        }
        run = ClassificationRun(
            id=run_id,
            week=week,
            status="completed",
            prompt_version="demo-v1",
            classifier_version="demo-v1",
            taxonomy_version=1,
            alias_version=1,
            started_at=STAMP,
            completed_at=STAMP,
        )
        document = WeekDocument(
            week=week,
            workflow_state="approved",
            active_run_id=run_id,
            approved_at=STAMP,
            approved_by="demo-seed",
            runs={run_id: run},
            items=week_items,
        )
        (classification_data_dir / f"{week}.json").write_text(
            document.model_dump_json(indent=2), encoding="utf-8"
        )


def _relation_ids_for(index: int, accepted_only: bool = False) -> list[str]:
    result = []
    for relation_index, (source, target, _kind, state, _confidence) in enumerate(RELATION_SPECS, start=1):
        if index not in {source, target} or (accepted_only and state != "accepted"):
            continue
        result.append(f"DEMO-REL-{relation_index:02d}")
    return result


def _write_wiki(
    wiki_data_dir: Path,
    items: dict[str, ClassificationItem],
) -> tuple[int, int, int]:
    store = JsonWikiStore(wiki_data_dir)
    topics: list[WikiTopic] = []
    for index, spec in enumerate(TOPIC_SPECS, start=1):
        topic_id = _topic_id(index)
        agenda_id = _agenda_id(index)
        revision_id = f"DEMO-REV-{index:02d}"
        section_body = (
            f"{spec.title}은 {spec.week} 주간보고에서 확인됐다. "
            f"현재 상태는 {spec.state}이며 관련 팀이 후속 확인 중이다. "
            f"[agenda:{agenda_id}]"
        )
        topic = WikiTopic(
            topic_id=topic_id,
            title=spec.title,
            topic_kind="issue" if spec.state not in {"resolved", "monitoring"} else "observation",
            primary_area=spec.area,
            secondary_areas=[],
            state=spec.state,
            importance=spec.importance,
            first_seen_week=spec.week,
            last_updated_week=spec.week,
            target_paths=[
                CategoryPath(domain=domain, tech=tech, lotcd=lotcd)
                for domain, tech, lotcd in spec.paths
            ],
            teams=list(spec.teams),
            source_agenda_ids=[agenda_id],
            related_topic_ids=[
                _topic_id(target if source == index else source)
                for source, target, _kind, state, _confidence in RELATION_SPECS
                if index in {source, target} and state == "accepted"
            ],
            current_revision_id=revision_id,
        )
        revision = TopicRevision(
            revision_id=revision_id,
            topic_id=topic_id,
            week=spec.week,
            body_markdown=f"# {spec.title}\n\n{section_body}",
            sections=[TopicSection(key="current_state", title=spec.section_title, body=section_body)],
            claims=[SupportedClaim(text=f"{spec.title} 상태가 보고됐다.", agenda_ids=[agenda_id])],
            source_agenda_ids=[agenda_id],
            evidence_refs=[f"{spec.week}/{_run_id(spec.week)}/{agenda_id}"],
            new_state=spec.state,
            added_agenda_ids=[agenda_id],
            build_run_id=f"DEMO-BUILD-{spec.week[-2:]}",
            prompt_version="demo-v1",
            builder_version="demo-v1",
            summary=spec.title,
            validation_results=["deterministic-demo"],
            created_at=STAMP,
            model="z-ai/glm-4.7-flash",
        )
        store.archive_evidence(ArchivedApprovedEvidence(
            evidence_ref=f"{spec.week}/{_run_id(spec.week)}/{agenda_id}",
            week=spec.week,
            classification_run_id=_run_id(spec.week),
            item=items[agenda_id],
            archived_at=STAMP,
        ))
        store.publish_topic(topic, revision)
        store.save_assignment(TopicAssignment(
            agenda_id=agenda_id,
            topic_id=topic_id,
            decision="create",
            confidence=0.96,
            rationale="deterministic demo assignment",
            decision_source="auto",
            decided_by="demo-seed",
            decided_at=STAMP,
        ))
        topics.append(topic)

    for relation_index, (source, target, kind, state, confidence) in enumerate(RELATION_SPECS, start=1):
        relation_id = f"DEMO-REL-{relation_index:02d}"
        store.save_relation(TopicRelation(
            relation_id=relation_id,
            source_topic_id=_topic_id(source),
            target_topic_id=_topic_id(target),
            kind=kind,
            agenda_ids=[_agenda_id(source), _agenda_id(target)],
            confidence=confidence,
            review_state=state,
            creation_source="migration",
            created_by="demo-seed",
            created_at=STAMP,
            created_build_run_id="DEMO-BUILD-30",
            creation_week="2026-W30",
        ))

    store.save_review(WikiReview(
        review_id="DEMO-REVIEW-ASSIGNMENT",
        kind="assignment",
        agenda_id=_agenda_id(12),
        candidates=[TopicCandidate(topic_id=_topic_id(12), score=0.61, rank_reasons=["저온 fail 키워드"])],
        rationale="Lucy 저온 fail의 기존 Topic 배정을 확인해야 한다.",
        status="pending",
    ))
    store.save_review(WikiReview(
        review_id="DEMO-REVIEW-RELATION",
        kind="relation",
        relation_id="DEMO-REL-09",
        relation_kind="shares_condition",
        relation_agenda_ids=[_agenda_id(3), _agenda_id(5)],
        rationale="세정 조건과 Test fail의 직접 관계를 검토해야 한다.",
        status="pending",
    ))

    for week in ("2026-W28", "2026-W29", "2026-W30"):
        run_id = _run_id(week)
        week_items = {
            agenda_id: item for agenda_id, item in items.items()
            if TOPIC_SPECS[int(agenda_id[-2:]) - 1].week == week
        }
        run = ClassificationRun(
            id=run_id, week=week, status="completed", prompt_version="demo-v1",
            classifier_version="demo-v1", taxonomy_version=1, alias_version=1,
            started_at=STAMP, completed_at=STAMP,
        )
        refs = [f"{week}/{run_id}/{agenda_id}" for agenda_id in sorted(week_items)]
        store.archive_approved_week(ArchivedApprovedWeek(
            week=week,
            classification_run_id=run_id,
            taxonomy_version=1,
            approved_at=STAMP,
            approved_by="demo-seed",
            runs={run_id: run},
            items=week_items,
            evidence_refs=refs,
            archived_at=STAMP,
        ))

    build = WikiBuildRun(
        run_id="DEMO-BUILD-30",
        week="2026-W30",
        classification_run_id="DEMO-CLASS-30",
        taxonomy_version=1,
        status="partially_failed",
        input_hash="demo-input-2026-w30",
        model="z-ai/glm-4.7-flash",
        affected_topic_ids=[topic.topic_id for topic in topics],
        failed_topic_ids=[_topic_id(12)],
        started_at=STAMP,
        completed_at=STAMP,
        error="Demo partial build: prior valid revision retained.",
    )
    store.save_build(build)

    week_topics = {
        week: [topic.topic_id for topic in topics if topic.last_updated_week == week]
        for week in ("2026-W28", "2026-W29", "2026-W30")
    }
    for week in ("2026-W28", "2026-W29"):
        store.save_week(WeekWikiView(
            week=week,
            revision_id=f"DEMO-WEEK-REV-{week[-2:]}",
            published_at=STAMP,
            build_run_id=f"DEMO-BUILD-{week[-2:]}",
            new_topic_ids=week_topics[week],
            changed_topic_ids=[],
            resolved_topic_ids=[_topic_id(6)] if week == "2026-W29" else [],
            reopened_topic_ids=[_topic_id(5)] if week == "2026-W29" else [],
            actions_and_decisions=[],
            new_relation_ids=[],
            pending_assignment_count=0,
            contradictions=[],
            teams=sorted({team for topic in topics if topic.last_updated_week == week for team in topic.teams}),
        ))
    store.save_week(WeekWikiView(
        week="2026-W30", revision_id="DEMO-WEEK-REV-30A", published_at=STAMP,
        build_run_id="DEMO-BUILD-30", new_topic_ids=week_topics["2026-W30"],
        changed_topic_ids=[_topic_id(1), _topic_id(4), _topic_id(7)],
        resolved_topic_ids=[], reopened_topic_ids=[], actions_and_decisions=[],
        new_relation_ids=["DEMO-REL-09"], pending_assignment_count=1,
        contradictions=["DEMO-REL-09 관계 검토 대기"],
        teams=sorted({team for topic in topics for team in topic.teams}),
    ))
    store.save_week(WeekWikiView(
        week="2026-W30", revision_id="DEMO-WEEK-REV-30B", published_at=STAMP,
        build_run_id="DEMO-BUILD-30", new_topic_ids=week_topics["2026-W30"],
        changed_topic_ids=[_topic_id(1), _topic_id(4), _topic_id(7)],
        resolved_topic_ids=[], reopened_topic_ids=[_topic_id(5)], actions_and_decisions=[],
        new_relation_ids=["DEMO-REL-09"], pending_assignment_count=1,
        contradictions=["DEMO-REL-09 관계 검토 대기"],
        teams=sorted({team for topic in topics for team in topic.teams}),
    ))
    store.rebuild_catalog()
    return len(topics), len(RELATION_SPECS), 3


def _generated_paths(wiki_data_dir: Path, classification_data_dir: Path) -> list[dict[str, str]]:
    paths = []
    for root_name, root in (("wiki", wiki_data_dir), ("classification", classification_data_dir)):
        for path in sorted(_existing_files(root)):
            if path.name == MANIFEST_NAME:
                continue
            paths.append({"root": root_name, "path": path.relative_to(root).as_posix()})
    return paths


def write_manifest(
    wiki_data_dir: Path,
    classification_data_dir: Path,
    generated_paths: list[dict[str, str]],
) -> None:
    _manifest_path(wiki_data_dir).write_text(
        json.dumps({"version": 1, "files": generated_paths}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def seed_demo(
    wiki_data_dir: Path,
    classification_data_dir: Path,
    replace_demo: bool = False,
) -> DemoSeedSummary:
    wiki_data_dir = Path(wiki_data_dir)
    classification_data_dir = Path(classification_data_dir)
    owned = validate_demo_destination(wiki_data_dir, classification_data_dir, replace_demo)
    _remove_owned(owned)
    _manifest_path(wiki_data_dir).unlink(missing_ok=True)

    mails = load_fixture_mails()
    items = {
        _agenda_id(index): _classification_item(index, spec, mails[index - 1])
        for index, spec in enumerate(TOPIC_SPECS, start=1)
    }
    _write_demo_mail_html(classification_data_dir / "mail", items, mails)
    _write_classification_weeks(classification_data_dir, items)
    topic_count, relation_count, week_count = _write_wiki(wiki_data_dir, items)
    generated = _generated_paths(wiki_data_dir, classification_data_dir)
    write_manifest(wiki_data_dir, classification_data_dir, generated)
    labels = tuple(f"{entry['root']}:{entry['path']}" for entry in generated)
    return DemoSeedSummary(topic_count, relation_count, week_count, labels)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed deterministic weekly Wiki demo JSON")
    parser.add_argument("--wiki-data-dir", type=Path, default=ROOT / "wiki_data")
    parser.add_argument("--classification-data-dir", type=Path, default=ROOT / "classification_data" / "demo")
    parser.add_argument("--replace-demo", action="store_true")
    args = parser.parse_args()
    summary = seed_demo(args.wiki_data_dir, args.classification_data_dir, args.replace_demo)
    print(
        f"Seeded {summary.topic_count} topics, {summary.relation_count} relations, "
        f"{summary.week_count} weeks."
    )


if __name__ == "__main__":
    main()
