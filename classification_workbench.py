from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from knowledge_models import (
    AliasRecord,
    CandidateMatch,
    CategoryPath,
    ClassificationDecision,
    TaxonomyDocument,
    WeekClassificationSummary,
)

CLASSIFIER_VERSION = "lotcd-v1"
PROMPT_VERSION = "agenda-v2"


def run_week_classification(
    week: str,
    store,
    mail_directories: Iterable[Path],
    splitter,
    rerun: bool = False,
) -> WeekClassificationSummary:
    from agenda_extract import CanonicalResolver, extract_mail
    import process_agendas

    run = store.start_classification_run(
        week=week,
        prompt_version=PROMPT_VERSION,
        classifier_version=CLASSIFIER_VERSION,
        rerun=rerun,
    )
    mail_directory: Path | None = None
    stage = "load_active_aliases"
    try:
        resolver = CanonicalResolver(store.taxonomy, store.aliases())
        for mail_directory in mail_directories:
            mail_directory = Path(mail_directory)
            stage = "mail_from_directory"
            mail = process_agendas.mail_from_directory(mail_directory)
            stage = "extract_mail"
            result = extract_mail(mail, splitter, resolver)
            stage = "save_classified_extraction"
            store.save_classified_extraction(run.id, mail, result)
        stage = "finish_classification_run"
        return store.finish_classification_run(run.id)
    except Exception as exc:
        return store.fail_classification_run(
            run.id,
            stage=stage,
            mail_directory=(str(mail_directory) if mail_directory else None),
            exception_type=type(exc).__name__,
            message=str(exc),
        )


def _contains(text: str, phrase: str) -> bool:
    return bool(re.search(
        rf"(?<![A-Za-z0-9]){re.escape(phrase)}(?![A-Za-z0-9])",
        text, re.IGNORECASE,
    ))


def lotcd_path(taxonomy: TaxonomyDocument, code: str) -> CategoryPath:
    for domain in taxonomy.domains:
        for tech in domain.techs:
            for lotcd in tech.lotcds:
                if lotcd.code.casefold() == code.casefold():
                    return CategoryPath(
                        domain=domain.name, tech=tech.name, lotcd=lotcd.code
                    )
    raise ValueError(f"Unknown LOTCD: {code}")


def classify_context(
    text: str,
    item_kind: str,
    taxonomy: TaxonomyDocument,
    aliases: list[AliasRecord] | None = None,
    sender_team: str = "",
) -> ClassificationDecision:
    if item_kind == "aggregate":
        return ClassificationDecision(
            status="aggregate",
            diagnostics=["AGGREGATE_METRIC"],
            confidence=1.0,
        )
    matches = []
    for domain in taxonomy.domains:
        for tech in domain.techs:
            for lotcd in tech.lotcds:
                if _contains(text, lotcd.code):
                    matches.append(CandidateMatch(
                        phrase=lotcd.code,
                        lotcd=lotcd.code,
                        match_type="canonical",
                        rule_id=f"canonical:{lotcd.code}",
                        score=1.0,
                    ))
    alias_collision = False
    unknown_alias_target = False
    for alias in aliases or []:
        if not _contains(text, alias.value):
            continue
        if alias.context_domain or alias.context_tech:
            contextual_terms: list[str] = []
            for domain in taxonomy.domains:
                if alias.context_domain == domain.name:
                    contextual_terms.append(domain.name)
                for tech in domain.techs:
                    if alias.context_tech == tech.name:
                        contextual_terms.extend([tech.name, *tech.aliases])
            context = f"{text}\n{sender_team}"
            if not any(_contains(context, term) for term in contextual_terms):
                continue
        target_lotcds = list(dict.fromkeys(
            path.lotcd for path in alias.target_paths if path.lotcd is not None
        ))
        for lotcd in target_lotcds:
            matches.append(CandidateMatch(
                phrase=alias.value,
                lotcd=lotcd,
                match_type="alias",
                rule_id=f"alias:{alias.id}",
                score=1.0,
            ))
            try:
                lotcd_path(taxonomy, lotcd)
            except ValueError:
                unknown_alias_target = True
        if len(alias.target_paths) != 1 or len(target_lotcds) != 1:
            alias_collision = True
    diagnostics = []
    if alias_collision:
        diagnostics.append("ALIAS_COLLISION")
    if unknown_alias_target:
        diagnostics.append("UNKNOWN_LOTCD_CODE")
    if alias_collision:
        return ClassificationDecision(
            status="conflict",
            matches=matches,
            diagnostics=diagnostics,
            confidence=0.0,
        )
    if unknown_alias_target:
        return ClassificationDecision(
            status="review_required",
            matches=matches,
            diagnostics=diagnostics,
            confidence=0.0,
        )
    codes = sorted({match.lotcd for match in matches})
    if len(codes) == 1:
        return ClassificationDecision(
            status="confirmed",
            target_path=lotcd_path(taxonomy, codes[0]),
            matches=matches,
            confidence=1.0,
        )
    if len(codes) > 1:
        return ClassificationDecision(
            status="conflict",
            matches=matches,
            diagnostics=["MULTIPLE_LOTCD_CONFLICT"],
            confidence=0.0,
        )
    return ClassificationDecision(
        status="unclassified",
        diagnostics=["NO_LOTCD_MATCH"],
        confidence=0.0,
    )
