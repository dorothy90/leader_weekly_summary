from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from classification_store import (
    DEFAULT_DATA_DIR,
    DEFAULT_RULES_PATH,
    JsonClassificationStore,
)
from knowledge_auth import EDITOR_ROLE, UserContext, require_authenticated, require_editor
from knowledge_models import (
    AliasRecord,
    ClassificationItem,
    ClassificationItemListResponse,
    ClassificationRunRequest,
    DecisionStatus,
    ItemDispositionUpdate,
    ItemSplitRequest,
    KnowledgeSession,
    LearnedAliasCreate,
    LotcdWikiView,
    RunComparison,
    TaxonomyDocument,
    TeamWikiView,
    TopicListItem,
    TopicState,
    KnowledgeArea,
    DomainName,
    WeekClassificationSummary,
    WeekWikiView,
    WikiBuildRun,
    WikiGraphView,
    WikiReview,
    WikiReviewResolution,
    WikiTopicDetail,
    WikiIndex,
    WorkbenchCorrection,
)
from topic_linker import build_link_decider, resolve_wiki_review
from topic_wiki_builder import build_analysis_fn, build_draft_fn, build_week
from wiki_projections import (
    build_lotcd_view,
    build_team_view,
    build_topic_detail,
    list_topics,
)
from wiki_store import DEFAULT_WIKI_DATA_DIR, JsonWikiStore


router = APIRouter(
    prefix="/api/knowledge",
    tags=["classification"],
    dependencies=[Depends(require_authenticated)],
)


@lru_cache(maxsize=1)
def get_store() -> JsonClassificationStore:
    return JsonClassificationStore(
        data_dir=Path(os.getenv("CLASSIFICATION_DATA_DIR", str(DEFAULT_DATA_DIR))),
        rules_path=Path(
            os.getenv("CLASSIFICATION_RULES_PATH", str(DEFAULT_RULES_PATH))
        ),
    )


@lru_cache(maxsize=1)
def get_wiki_store() -> JsonWikiStore:
    return JsonWikiStore(
        Path(os.getenv("WIKI_DATA_DIR", str(DEFAULT_WIKI_DATA_DIR)))
    )


def _mail_directories(week: str) -> list[Path]:
    return [path.parent for path in sorted((Path("data") / week).glob("**/combined.txt"))]


def _item_or_404(agenda_id: str) -> ClassificationItem:
    store = get_store()
    for summary in store.week_summaries():
        for item in store.classification_items(summary.week):
            if item.agenda_id == agenda_id:
                return item
    raise HTTPException(status_code=404, detail=f"Unknown item: {agenda_id}")


def _value_error(exc: ValueError, *, invalid: bool = False) -> HTTPException:
    message = str(exc)
    return HTTPException(
        status_code=422 if invalid or "Unknown LOTCD" in message else 409,
        detail=message,
    )


@router.get("/session", response_model=KnowledgeSession)
def session(user: UserContext = Depends(require_authenticated)) -> KnowledgeSession:
    return KnowledgeSession(
        user_id=user.user_id,
        roles=sorted(user.roles),
        can_edit=EDITOR_ROLE in user.roles,
    )


@router.get("/taxonomy", response_model=TaxonomyDocument)
def taxonomy() -> TaxonomyDocument:
    return get_store().taxonomy


@router.get("/classification/weeks", response_model=list[WeekClassificationSummary])
def weeks() -> list[WeekClassificationSummary]:
    return get_store().week_summaries()


@router.get(
    "/classification/weeks/{week}/items",
    response_model=ClassificationItemListResponse,
)
def items(
    week: str,
    lotcd: str | None = None,
    status: DecisionStatus | None = None,
    q: str | None = Query(default=None, max_length=200),
) -> ClassificationItemListResponse:
    store = get_store()
    try:
        source = store.classification_items(week)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown week: {week}") from exc
    if lotcd and not any(
        item.code == lotcd
        for domain in store.taxonomy.domains
        for tech in domain.techs
        for item in tech.lotcds
    ):
        raise HTTPException(status_code=422, detail=f"Unknown LOTCD: {lotcd}")
    query = q.strip().casefold() if q else None
    filtered = []
    for item in source:
        if lotcd and (
            item.decision.target_path is None
            or item.decision.target_path.lotcd != lotcd
        ):
            continue
        if status and item.decision.status != status:
            continue
        if query and query not in (
            f"{item.summary} {item.source_quote} {item.classification_context}"
        ).casefold():
            continue
        filtered.append(item)
    return ClassificationItemListResponse(items=filtered, total=len(filtered))


@router.get("/classification/items/{agenda_id}", response_model=ClassificationItem)
def item(agenda_id: str) -> ClassificationItem:
    return _item_or_404(agenda_id)


@router.get(
    "/classification/runs/{old_run_id}/comparison/{new_run_id}",
    response_model=RunComparison,
)
def comparison(old_run_id: str, new_run_id: str) -> RunComparison:
    try:
        return get_store().compare_runs(old_run_id, new_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown run: {exc.args[0]}") from exc


@router.post(
    "/classification/weeks/{week}/run",
    response_model=WeekClassificationSummary,
)
def run_week(
    week: str,
    request: ClassificationRunRequest | None = None,
    _user: UserContext = Depends(require_editor),
) -> WeekClassificationSummary:
    directories = _mail_directories(week)
    if not directories:
        raise HTTPException(status_code=404, detail=f"Unknown week: {week}")
    store = get_store()
    try:
        from agenda_extract import build_splitter
        from classification_workbench import run_week_classification

        return run_week_classification(
            week=week,
            store=store,
            mail_directories=directories,
            splitter=build_splitter(store.taxonomy),
            rerun=request.rerun if request else False,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise _value_error(exc) from exc


@router.post(
    "/classification/weeks/{week}/approve",
    response_model=WeekClassificationSummary,
)
def approve(
    week: str, user: UserContext = Depends(require_editor)
) -> WeekClassificationSummary:
    try:
        return get_store().approve_week(week, user.user_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown week: {week}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/classification/items/{agenda_id}", response_model=ClassificationItem)
def correct(
    agenda_id: str,
    update: WorkbenchCorrection,
    user: UserContext = Depends(require_editor),
) -> ClassificationItem:
    try:
        return get_store().correct_classification(
            agenda_id, update.lotcd, user.user_id, update.reason
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown item: {agenda_id}") from exc
    except ValueError as exc:
        raise _value_error(exc) from exc


@router.patch(
    "/classification/items/{agenda_id}/disposition",
    response_model=ClassificationItem,
)
def disposition(
    agenda_id: str,
    update: ItemDispositionUpdate,
    user: UserContext = Depends(require_editor),
) -> ClassificationItem:
    try:
        return get_store().set_item_disposition(
            agenda_id, update.status, user.user_id, update.reason
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown item: {agenda_id}") from exc
    except ValueError as exc:
        raise _value_error(exc) from exc


@router.post(
    "/classification/items/{agenda_id}/split",
    response_model=list[ClassificationItem],
)
def split(
    agenda_id: str,
    request: ItemSplitRequest,
    user: UserContext = Depends(require_editor),
) -> list[ClassificationItem]:
    try:
        return get_store().split_classification_item(
            agenda_id, request.parts, user.user_id, request.reason
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown item: {agenda_id}") from exc
    except ValueError as exc:
        raise _value_error(exc, invalid="Cannot modify item" not in str(exc)) from exc


@router.post("/classification/aliases", response_model=AliasRecord, status_code=201)
def alias(
    create: LearnedAliasCreate,
    user: UserContext = Depends(require_editor),
) -> AliasRecord:
    _item_or_404(create.origin_agenda_id)
    try:
        return get_store().create_learned_alias(
            value=create.value,
            lotcd=create.lotcd,
            origin_agenda_id=create.origin_agenda_id,
            changed_by=user.user_id,
            context_domain=create.context_domain,
            context_tech=create.context_tech,
        )
    except ValueError as exc:
        raise _value_error(exc) from exc


@router.get("/wiki/topics", response_model=list[TopicListItem])
def wiki_topics(
    q: str | None = None,
    state: TopicState | None = None,
    area: KnowledgeArea | None = None,
    team: str | None = None,
    lotcd: str | None = None,
) -> list[TopicListItem]:
    return list_topics(
        get_wiki_store(), q=q, state=state, area=area, team=team, lotcd=lotcd
    )


@router.get("/wiki/graph", response_model=WikiGraphView)
def wiki_graph() -> WikiGraphView:
    store = get_wiki_store()
    return WikiGraphView(
        topics=list_topics(store),
        relations=[
            relation
            for relation in store.relations()
            if relation.review_state != "rejected"
        ],
    )


@router.get("/wiki/topics/{topic_id}", response_model=WikiTopicDetail)
def wiki_topic(topic_id: str) -> WikiTopicDetail:
    try:
        return build_topic_detail(get_wiki_store(), get_store(), topic_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown Topic: {topic_id}") from exc


@router.get("/wiki/lotcd/{domain}/{tech}/{lotcd}", response_model=LotcdWikiView)
def wiki_lotcd(domain: DomainName, tech: str, lotcd: str) -> LotcdWikiView:
    try:
        return build_lotcd_view(
            get_wiki_store(), get_store(), domain, tech, lotcd
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown LOTCD: {domain}/{tech}/{lotcd}",
        ) from exc


@router.get("/wiki/teams/{team}", response_model=TeamWikiView)
def wiki_team(team: str) -> TeamWikiView:
    return build_team_view(get_wiki_store(), get_store(), team)


@router.get("/wiki/teams", response_model=WikiIndex)
def wiki_teams() -> WikiIndex:
    return WikiIndex(values=sorted({
        team for topic in get_wiki_store().topics() for team in topic.teams
    }))


@router.get("/wiki/weeks/{week}", response_model=WeekWikiView)
def wiki_week(week: str) -> WeekWikiView:
    try:
        return get_wiki_store().week(week)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=f"Unknown Wiki week: {week}"
        ) from exc


@router.get("/wiki/weeks", response_model=WikiIndex)
def wiki_weeks() -> WikiIndex:
    return WikiIndex(values=get_wiki_store().weeks())


@router.get("/wiki/reviews", response_model=list[WikiReview])
def wiki_reviews(status: str | None = "pending") -> list[WikiReview]:
    return get_wiki_store().reviews(status)


@router.post("/wiki/reviews/{review_id}/resolve", response_model=WikiReview)
def resolve_review(
    review_id: str,
    resolution: WikiReviewResolution,
    user: UserContext = Depends(require_editor),
) -> WikiReview:
    try:
        return resolve_wiki_review(
            get_wiki_store(), review_id, resolution, user.user_id
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=f"Unknown Wiki review: {review_id}"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/wiki/builds/{week}", response_model=WikiBuildRun)
def start_wiki_build(
    week: str, _user: UserContext = Depends(require_editor)
) -> WikiBuildRun:
    classification_store = get_store()
    try:
        classification_store.approved_week(week)
        return build_week(
            week,
            classification_store,
            get_wiki_store(),
            build_link_decider(),
            build_analysis_fn(),
            build_draft_fn(),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown week: {week}") from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/wiki/builds/{run_id}", response_model=WikiBuildRun)
def wiki_build(run_id: str) -> WikiBuildRun:
    try:
        return get_wiki_store().build(run_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=f"Unknown Wiki build: {run_id}"
        ) from exc
