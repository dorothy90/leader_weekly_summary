"""Knowledge Explorer API backed by a fixture-seeded SQLite database.

This first vertical slice stays independent from OpenSearch so the hierarchy and
frontend interactions can be verified before production indexing is changed.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from knowledge_auth import EDITOR_ROLE, UserContext, require_authenticated, require_editor
from knowledge_models import (
    Agenda,
    AgendaDetailResponse,
    AgendaListResponse,
    AgendaView,
    AliasCreate,
    AliasListResponse,
    AliasRecord,
    AliasUpdate,
    CategoryCount,
    CategoryCountResponse,
    CategoryWikiPage,
    ClassificationUpdate,
    Mail,
    KnowledgeFacets,
    KnowledgeSession,
    LotcdCreate,
    LotcdUpdate,
    MappingRevisionListResponse,
    RevisionListResponse,
    SearchSyncResponse,
    TechCreate,
    TechUpdate,
    TaxonomyDocument,
)
from knowledge_store import DEFAULT_DB_PATH, SQLiteKnowledgeStore


router = APIRouter(
    prefix="/api/knowledge",
    tags=["knowledge"],
    dependencies=[Depends(require_authenticated)],
)


def _search_opensearch_agenda_ids(query: str) -> list[str]:
    from agenda_opensearch import search_agenda_ids
    from embed_vectordb import get_opensearch_client

    return search_agenda_ids(get_opensearch_client(), query)


def _category_page_id(domain: str, tech: str | None, lotcd: str | None) -> str:
    if lotcd:
        return f"lotcd:{lotcd.lower()}"
    if tech:
        return f"tech:{domain.lower()}:{tech.lower()}"
    return f"domain:{domain.lower()}"


def _get_category_wiki_page(category_id: str) -> CategoryWikiPage:
    from category_wiki_builder import PAGE_INDEX
    from embed_vectordb import get_opensearch_client

    client = get_opensearch_client()
    try:
        response = client.get(index=PAGE_INDEX, id=category_id)
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if status == 404 or exc.__class__.__name__ == "NotFoundError":
            raise HTTPException(status_code=404, detail="Wiki page has not been built") from exc
        raise HTTPException(status_code=503, detail="Wiki page store unavailable") from exc
    return CategoryWikiPage.model_validate(response["_source"])


def _week_received_at(week: str) -> datetime:
    match = re.fullmatch(r"(\d{4})-W?(\d{1,2})", week)
    if not match:
        return datetime.now(UTC)
    return datetime.fromisocalendar(int(match.group(1)), int(match.group(2)), 1).replace(
        tzinfo=UTC
    )


def _get_opensearch_agenda(agenda_id: str) -> AgendaDetailResponse:
    from category_wiki_builder import AGENDA_INDEX, SOURCE_INDEX, merge_overlapping_chunks
    from embed_vectordb import get_opensearch_client

    client = get_opensearch_client()
    try:
        agenda_source = client.get(index=AGENDA_INDEX, id=agenda_id)["_source"]
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if status == 404 or exc.__class__.__name__ == "NotFoundError":
            raise HTTPException(status_code=404, detail=f"Unknown agenda: {agenda_id}") from exc
        raise HTTPException(status_code=503, detail="Agenda store unavailable") from exc

    source_ids = list(agenda_source.get("source_doc_ids", []))
    source_response = client.mget(index=SOURCE_INDEX, body={"ids": source_ids})
    source_parts = sorted(
        (doc for doc in source_response.get("docs", []) if doc.get("found")),
        key=lambda doc: doc["_source"].get("part_index", 0),
    )
    body = merge_overlapping_chunks(
        str(doc["_source"].get("text", "")) for doc in source_parts
    )
    week = str(agenda_source.get("week", "unknown"))
    received_at = _week_received_at(week)
    target_paths = [CategoryPath.model_validate(path) for path in agenda_source.get("target_paths", [])]
    candidate_paths = [
        CategoryPath.model_validate(path)
        for path in agenda_source.get("candidate_paths", [])
    ]
    review_status = str(agenda_source.get("review_status", "pending"))
    view = AgendaView(
        id=agenda_id,
        mail_id=str(agenda_source["mail_id"]),
        source_quote=str(agenda_source.get("source_quote", "")),
        summary=str(agenda_source.get("summary", "")),
        scope=str(agenda_source.get("scope", "unknown")),
        target_paths=target_paths,
        candidate_paths=candidate_paths,
        topic=str(agenda_source.get("topic", "unknown")),
        state=str(agenda_source.get("state", "unknown")),
        confidence=float(agenda_source.get("confidence", 0)),
        review_required=review_status != "confirmed",
        subject=str(agenda_source.get("subject", "")),
        sender_team=str(agenda_source.get("sender_team", "unknown")),
        received_at=received_at,
        review_status=review_status,
    )
    mail = Mail(
        id=view.mail_id,
        subject=view.subject,
        sender_team=view.sender_team,
        sender="unknown",
        received_at=received_at,
        body=body,
        reply_to=None,
    )
    return AgendaDetailResponse(agenda=view, mail=mail)


@lru_cache(maxsize=1)
def get_store() -> SQLiteKnowledgeStore:
    db_path = Path(os.getenv("KNOWLEDGE_DB_PATH", str(DEFAULT_DB_PATH)))
    return SQLiteKnowledgeStore(db_path)


def _path_matches(
    agenda: Agenda,
    domain: str | None,
    tech: str | None,
    lotcd: str | None,
    scope_mode: Literal["direct", "descendants"],
) -> bool:
    if domain is None:
        return True

    if scope_mode == "direct":
        if lotcd is not None:
            return any(
                path.domain == domain
                and path.tech == tech
                and path.lotcd == lotcd
                for path in agenda.target_paths
            )
        if tech is not None:
            return agenda.scope == "tech" and any(
                path.domain == domain
                and path.tech == tech
                and path.lotcd is None
                for path in agenda.target_paths
            )
        return agenda.scope == "domain" and any(
            path.domain == domain and path.tech is None and path.lotcd is None
            for path in agenda.target_paths
        )

    return any(
        path.domain == domain
        and (tech is None or path.tech == tech)
        and (lotcd is None or path.lotcd == lotcd)
        for path in agenda.target_paths
    )


def _validate_selection(
    taxonomy: TaxonomyDocument,
    domain: str | None,
    tech: str | None,
    lotcd: str | None,
) -> None:
    if tech is not None and domain is None:
        raise HTTPException(status_code=422, detail="tech requires domain")
    if lotcd is not None and tech is None:
        raise HTTPException(status_code=422, detail="lotcd requires tech")
    if domain is None:
        return

    selected_domain = next(
        (item for item in taxonomy.domains if item.name == domain), None
    )
    if selected_domain is None:
        raise HTTPException(status_code=404, detail=f"Unknown domain: {domain}")
    if tech is None:
        return

    selected_tech = next(
        (item for item in selected_domain.techs if item.name == tech), None
    )
    if selected_tech is None:
        raise HTTPException(status_code=404, detail=f"Unknown tech: {tech}")
    if lotcd is not None and all(item.code != lotcd for item in selected_tech.lotcds):
        raise HTTPException(status_code=404, detail=f"Unknown LOTCD: {lotcd}")


@router.get("/taxonomy", response_model=TaxonomyDocument)
def get_taxonomy() -> TaxonomyDocument:
    return get_store().taxonomy


@router.get("/wiki/pages/{domain}/{tech}/{lotcd}", response_model=CategoryWikiPage)
def get_lotcd_wiki_page(
    domain: Literal["DRAM", "NAND"], tech: str, lotcd: str
) -> CategoryWikiPage:
    taxonomy = get_store().taxonomy
    _validate_selection(taxonomy, domain, tech, lotcd.upper())
    return _get_category_wiki_page(_category_page_id(domain, tech, lotcd))


@router.get("/wiki/pages/{domain}/{tech}", response_model=CategoryWikiPage)
def get_tech_wiki_page(
    domain: Literal["DRAM", "NAND"], tech: str
) -> CategoryWikiPage:
    taxonomy = get_store().taxonomy
    _validate_selection(taxonomy, domain, tech, None)
    return _get_category_wiki_page(_category_page_id(domain, tech, None))


@router.get("/wiki/pages/{domain}", response_model=CategoryWikiPage)
def get_domain_wiki_page(domain: Literal["DRAM", "NAND"]) -> CategoryWikiPage:
    taxonomy = get_store().taxonomy
    _validate_selection(taxonomy, domain, None, None)
    return _get_category_wiki_page(_category_page_id(domain, None, None))


@router.get("/session", response_model=KnowledgeSession)
def get_session(
    user: UserContext = Depends(require_authenticated),
) -> KnowledgeSession:
    return KnowledgeSession(
        user_id=user.user_id,
        roles=sorted(user.roles),
        can_edit=EDITOR_ROLE in user.roles,
    )


@router.get("/counts", response_model=CategoryCountResponse)
def get_category_counts() -> CategoryCountResponse:
    store = get_store()
    items: list[CategoryCount] = []
    for domain in store.taxonomy.domains:
        domain_path = {"domain": domain.name, "tech": None, "lotcd": None}
        items.append(
            CategoryCount(
                path=domain_path,
                direct=sum(
                    _path_matches(agenda, domain.name, None, None, "direct")
                    for agenda in store.agendas
                ),
                descendants=sum(
                    _path_matches(agenda, domain.name, None, None, "descendants")
                    for agenda in store.agendas
                ),
            )
        )
        for tech in domain.techs:
            tech_path = {"domain": domain.name, "tech": tech.name, "lotcd": None}
            items.append(
                CategoryCount(
                    path=tech_path,
                    direct=sum(
                        _path_matches(
                            agenda, domain.name, tech.name, None, "direct"
                        )
                        for agenda in store.agendas
                    ),
                    descendants=sum(
                        _path_matches(
                            agenda, domain.name, tech.name, None, "descendants"
                        )
                        for agenda in store.agendas
                    ),
                )
            )
            for lotcd in tech.lotcds:
                path = {
                    "domain": domain.name,
                    "tech": tech.name,
                    "lotcd": lotcd.code,
                }
                count = sum(
                    _path_matches(
                        agenda,
                        domain.name,
                        tech.name,
                        lotcd.code,
                        "descendants",
                    )
                    for agenda in store.agendas
                )
                items.append(CategoryCount(path=path, direct=count, descendants=count))
    return CategoryCountResponse(items=items)


@router.get("/facets", response_model=KnowledgeFacets)
def get_facets() -> KnowledgeFacets:
    store = get_store()
    views = [store.agenda_view(agenda) for agenda in store.agendas]
    dates = [view.received_at.date() for view in views]
    return KnowledgeFacets(
        topics=sorted({view.topic for view in views}),
        states=sorted({view.state for view in views}),
        sender_teams=sorted({view.sender_team for view in views}),
        date_min=min(dates) if dates else None,
        date_max=max(dates) if dates else None,
    )


@router.get("/agendas", response_model=AgendaListResponse)
def get_agendas(
    domain: Literal["DRAM", "NAND"] | None = None,
    tech: str | None = None,
    lotcd: str | None = None,
    scope_mode: Literal["direct", "descendants"] = "descendants",
    topic: str | None = None,
    state: str | None = None,
    sender_team: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    review_required: bool | None = None,
    review_status: Literal["pending", "confirmed", "on_hold"] | None = None,
    q: str | None = Query(default=None, max_length=200),
) -> AgendaListResponse:
    store = get_store()
    _validate_selection(store.taxonomy, domain, tech, lotcd)
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must not exceed date_to")
    normalized_query = q.strip().casefold() if q else None
    all_agendas = store.agendas
    ranked_ids: list[str] | None = None
    ranked_id_set: set[str] = set()
    if normalized_query and os.getenv(
        "KNOWLEDGE_SEARCH_BACKEND", "sqlite"
    ).lower() == "opensearch":
        try:
            ranked_ids = _search_opensearch_agenda_ids(q.strip())
            ranked_id_set = set(ranked_ids)
            rank = {agenda_id: index for index, agenda_id in enumerate(ranked_ids)}
            all_agendas.sort(key=lambda agenda: rank.get(agenda.id, len(rank)))
        except Exception:
            ranked_ids = None
    pending_sync_mails = set(store.pending_search_sync())

    items: list[AgendaView] = []
    for agenda in all_agendas:
        if not _path_matches(agenda, domain, tech, lotcd, scope_mode):
            continue
        if topic is not None and agenda.topic != topic:
            continue
        if state is not None and agenda.state != state:
            continue
        if review_required is not None and agenda.review_required != review_required:
            continue
        view = store.agenda_view(agenda)
        if sender_team is not None and view.sender_team != sender_team:
            continue
        received_date = view.received_at.date()
        if date_from is not None and received_date < date_from:
            continue
        if date_to is not None and received_date > date_to:
            continue
        if review_status is not None and view.review_status != review_status:
            continue
        if normalized_query is not None:
            literal_match = normalized_query in (
                f"{agenda.summary} {agenda.source_quote}"
            ).casefold()
            use_sqlite_match = (
                ranked_ids is None
                or view.review_status != "confirmed"
                or view.mail_id in pending_sync_mails
            )
            if use_sqlite_match and not literal_match:
                continue
            if not use_sqlite_match and agenda.id not in ranked_id_set:
                continue
        items.append(view)

    if ranked_ids is None:
        items.sort(key=lambda item: (item.received_at, item.id), reverse=True)
    return AgendaListResponse(items=items, total=len(items))


@router.get("/agendas/{agenda_id}", response_model=AgendaDetailResponse)
def get_agenda(agenda_id: str) -> AgendaDetailResponse:
    store = get_store()
    agenda = next((item for item in store.agendas if item.id == agenda_id), None)
    if agenda is None:
        return _get_opensearch_agenda(agenda_id)
    mail = store.mails[agenda.mail_id]
    return AgendaDetailResponse(agenda=store.agenda_view(agenda), mail=mail)


@router.get("/mails/{mail_id}", response_model=Mail)
def get_mail(mail_id: str) -> Mail:
    mail = get_store().mails.get(mail_id)
    if mail is None:
        raise HTTPException(status_code=404, detail=f"Unknown mail: {mail_id}")
    return mail


@router.get("/review-queue", response_model=AgendaListResponse)
def get_review_queue() -> AgendaListResponse:
    store = get_store()
    items = []
    for agenda in store.agendas:
        view = store.agenda_view(agenda)
        if view.review_status == "pending":
            items.append(view)
    items.sort(key=lambda item: (item.received_at, item.id), reverse=True)
    return AgendaListResponse(items=items, total=len(items))


@router.patch(
    "/agendas/{agenda_id}/classification", response_model=AgendaDetailResponse
)
def update_classification(
    agenda_id: str,
    update: ClassificationUpdate,
    user: UserContext = Depends(require_editor),
) -> AgendaDetailResponse:
    store = get_store()
    if update.review_status == "confirmed" and not update.target_paths:
        raise HTTPException(
            status_code=422, detail="confirmed classification requires target_paths"
        )
    try:
        agenda = store.update_classification(
            agenda_id=agenda_id,
            target_paths=update.target_paths,
            review_status=update.review_status,
            changed_by=user.user_id,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=f"Unknown agenda: {agenda_id}"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return AgendaDetailResponse(
        agenda=store.agenda_view(agenda), mail=store.mails[agenda.mail_id]
    )


@router.get(
    "/agendas/{agenda_id}/revisions", response_model=RevisionListResponse
)
def get_classification_revisions(agenda_id: str) -> RevisionListResponse:
    store = get_store()
    if all(agenda.id != agenda_id for agenda in store.agendas):
        _get_opensearch_agenda(agenda_id)
        return RevisionListResponse(items=[])
    return RevisionListResponse(items=store.revisions(agenda_id))


@router.get("/aliases", response_model=AliasListResponse)
def get_aliases() -> AliasListResponse:
    return AliasListResponse(items=get_store().aliases())


@router.post("/aliases", response_model=AliasRecord, status_code=201)
def create_alias(
    create: AliasCreate, user: UserContext = Depends(require_editor)
) -> AliasRecord:
    try:
        return get_store().create_alias(
            value=create.value,
            target_paths=create.target_paths,
            changed_by=user.user_id,
        )
    except ValueError as exc:
        status = 409 if "already exists" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.patch("/aliases/{alias_id}", response_model=AliasRecord)
def update_alias(
    alias_id: int,
    update: AliasUpdate,
    user: UserContext = Depends(require_editor),
) -> AliasRecord:
    try:
        return get_store().update_alias(
            alias_id=alias_id,
            value=update.value,
            target_paths=update.target_paths,
            changed_by=user.user_id,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=f"Unknown alias: {alias_id}"
        ) from exc
    except ValueError as exc:
        status = 409 if "already exists" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.delete("/aliases/{alias_id}", response_model=AliasRecord)
def delete_alias(
    alias_id: int,
    user: UserContext = Depends(require_editor),
) -> AliasRecord:
    try:
        return get_store().delete_alias(alias_id, user.user_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=f"Unknown alias: {alias_id}"
        ) from exc


@router.get(
    "/aliases/{alias_id}/revisions", response_model=MappingRevisionListResponse
)
def get_mapping_revisions(alias_id: int) -> MappingRevisionListResponse:
    store = get_store()
    if all(alias.id != alias_id for alias in store.aliases()):
        raise HTTPException(status_code=404, detail=f"Unknown alias: {alias_id}")
    return MappingRevisionListResponse(items=store.mapping_revisions(alias_id))


@router.post("/search-index/sync", response_model=SearchSyncResponse)
def sync_search_index(
    _user: UserContext = Depends(require_editor),
) -> SearchSyncResponse:
    from agenda_opensearch import sync_pending
    from embed_vectordb import get_opensearch_client

    result = sync_pending(get_opensearch_client(), get_store())
    return SearchSyncResponse.model_validate(result)


@router.post("/techs", response_model=TaxonomyDocument, status_code=201)
def create_tech(
    create: TechCreate,
    _user: UserContext = Depends(require_editor),
) -> TaxonomyDocument:
    try:
        return get_store().create_tech(
            domain=create.domain,
            tech_id=create.id,
            name=create.name,
            aliases=create.aliases,
        )
    except ValueError as exc:
        status = 409 if "already exists" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.patch("/techs/{tech_id}", response_model=TaxonomyDocument)
def update_tech(
    tech_id: str,
    update: TechUpdate,
    _user: UserContext = Depends(require_editor),
) -> TaxonomyDocument:
    try:
        return get_store().update_tech(tech_id=tech_id, **update.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown Tech: {tech_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/techs/{tech_id}", response_model=TaxonomyDocument)
def delete_tech(
    tech_id: str,
    _user: UserContext = Depends(require_editor),
) -> TaxonomyDocument:
    try:
        return get_store().delete_tech(tech_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown Tech: {tech_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/lotcds", response_model=TaxonomyDocument, status_code=201)
def create_lotcd(
    create: LotcdCreate,
    _user: UserContext = Depends(require_editor),
) -> TaxonomyDocument:
    try:
        return get_store().create_lotcd(**create.model_dump())
    except ValueError as exc:
        status = 409 if "already exists" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.patch("/lotcds/{code}", response_model=TaxonomyDocument)
def update_lotcd(
    code: str,
    update: LotcdUpdate,
    _user: UserContext = Depends(require_editor),
) -> TaxonomyDocument:
    try:
        return get_store().update_lotcd(code=code, **update.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown LOTCD: {code}") from exc


@router.delete("/lotcds/{code}", response_model=TaxonomyDocument)
def delete_lotcd(
    code: str,
    _user: UserContext = Depends(require_editor),
) -> TaxonomyDocument:
    try:
        return get_store().delete_lotcd(code)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown LOTCD: {code}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
