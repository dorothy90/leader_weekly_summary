"""Evaluate canonical category resolution against the dummy gold fixture."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agenda_extract import CanonicalResolver, prior_sentence_context
from knowledge_models import AgendaDocument, MailDocument, TaxonomyDocument


FIXTURES = ROOT / "fixtures" / "knowledge"


def path_keys(paths):
    return {(path.domain, path.tech, path.lotcd) for path in paths}


def main() -> int:
    taxonomy = TaxonomyDocument.model_validate_json(
        (FIXTURES / "taxonomy.json").read_text(encoding="utf-8")
    )
    mail_document = MailDocument.model_validate_json(
        (FIXTURES / "mails.json").read_text(encoding="utf-8")
    )
    gold = AgendaDocument.model_validate_json(
        (FIXTURES / "expected_agendas.json").read_text(encoding="utf-8")
    )
    mails = {mail.id: mail for mail in mail_document.mails}
    resolver = CanonicalResolver(taxonomy)
    target_matches = 0
    scope_matches = 0
    failures = []

    for agenda in gold.agendas:
        mail = mails[agenda.mail_id]
        context = agenda.source_quote
        resolved = resolver.resolve(context, mail.sender_team)
        if not resolved.target_paths and not resolved.candidate_paths:
            context = prior_sentence_context(mail.body, agenda.source_quote)
            resolved = resolver.resolve(context, mail.sender_team)

        expected_paths = path_keys(agenda.target_paths)
        actual_paths = path_keys(resolved.target_paths)
        if expected_paths == actual_paths:
            target_matches += 1
        else:
            failures.append(
                {
                    "agenda_id": agenda.id,
                    "expected": sorted(expected_paths),
                    "actual": sorted(actual_paths),
                    "context": context,
                }
            )
        if agenda.scope == resolved.scope:
            scope_matches += 1

    total = len(gold.agendas)
    result = {
        "total": total,
        "target_path_accuracy": round(target_matches / total, 4),
        "scope_accuracy": round(scope_matches / total, 4),
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if target_matches / total >= 0.95 else 1


if __name__ == "__main__":
    raise SystemExit(main())
