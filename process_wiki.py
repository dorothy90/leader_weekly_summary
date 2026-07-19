"""Build the Topic Wiki for one approved classification week."""

from __future__ import annotations

import argparse
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

from classification_store import (
    DEFAULT_DATA_DIR,
    DEFAULT_RULES_PATH,
    JsonClassificationStore,
)
from knowledge_models import WikiBuildRun
from topic_linker import build_link_decider
from topic_wiki_builder import (
    build_analysis_fn,
    build_draft_fn,
    build_week,
    build_wiki_llm,
)
from wiki_store import DEFAULT_WIKI_DATA_DIR, JsonWikiStore


@contextmanager
def _external_llm_acknowledgement(enabled: bool) -> Iterator[None]:
    if not enabled:
        yield
        return
    name = "KNOWLEDGE_LLM_DATA_POLICY_ACK"
    previous = os.environ.get(name)
    os.environ[name] = "true"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def process_week(
    *,
    week: str,
    classification_data_dir: Path = DEFAULT_DATA_DIR,
    rules: Path = DEFAULT_RULES_PATH,
    wiki_data_dir: Path = DEFAULT_WIKI_DATA_DIR,
) -> WikiBuildRun:
    classification_store = JsonClassificationStore(classification_data_dir, rules)
    classification_store.approved_week(week)
    wiki_store = JsonWikiStore(wiki_data_dir)
    wiki_llm = build_wiki_llm()
    return build_week(
        week,
        classification_store,
        wiki_store,
        build_link_decider(),
        build_analysis_fn(wiki_llm),
        build_draft_fn(wiki_llm),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument(
        "--classification-data-dir", type=Path, default=DEFAULT_DATA_DIR
    )
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES_PATH)
    parser.add_argument("--wiki-data-dir", type=Path, default=DEFAULT_WIKI_DATA_DIR)
    parser.add_argument("--allow-external-llm", action="store_true")
    args = parser.parse_args(argv)
    with _external_llm_acknowledgement(args.allow_external_llm):
        run = process_week(
            week=args.week,
            classification_data_dir=args.classification_data_dir,
            rules=args.rules,
            wiki_data_dir=args.wiki_data_dir,
        )
    print(run.model_dump_json())
    return 0 if run.status in {"published", "review_required"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
