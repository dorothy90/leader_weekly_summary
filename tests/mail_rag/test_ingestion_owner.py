import json
import asyncio
import subprocess
import sys
import types
from pathlib import Path

import pytest

import embed_vectordb

if "imap_tools" not in sys.modules:
    imap_tools = types.ModuleType("imap_tools")
    imap_tools.MailBox = object
    imap_tools.AND = lambda **kwargs: kwargs
    sys.modules["imap_tools"] = imap_tools
import fetch_mail
import generate_dummy_team_weeks as dummy_weeks
import process_attachment
import process_vision
import wiki_builder
from scripts.backfill_parent_child import (
    build_bulk_actions,
    build_parent_child,
    build_migration_identity,
    canonical_document_hash,
    collect_source_snapshot,
    parse_args as parse_parent_args,
    plan_migration,
    run_migration,
    validate_checkpoint,
)
from scripts.backfill_user_id import build_update_query, parse_args, run_backfill
from scripts.shadow_retrieval import compare_rankings, run_shadow


def test_fetch_requires_explicit_owner(monkeypatch):
    monkeypatch.delenv("MAIL_USER_ID", raising=False)
    with pytest.raises(RuntimeError, match="MAIL_USER_ID"):
        fetch_mail.require_mail_user_id()

    monkeypatch.setenv("MAIL_USER_ID", "kim")
    assert fetch_mail.require_mail_user_id() == "kim"


def test_saved_mail_metadata_propagates_exact_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_mail, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(fetch_mail, "MAIL_DIR", tmp_path / "mail")
    mail_data = {
        "subject": "weekly",
        "sender": "sender@example.test",
        "received": None,
        "body_text": "body",
        "body_html": "",
        "inline_images": [],
        "attachments": [],
    }

    mail_dir = fetch_mail.save_mail(mail_data, "2026-08", "YIELD", "mail-1", "kim")

    meta = json.loads((mail_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["user_id"] == "kim"
    assert meta["content_id"]
    assert meta["document_locator"] == f"/v1/mail-content/{meta['content_id']}"
    assert str(mail_dir) not in json.dumps(meta)
    assert not (tmp_path / "mail").exists()


def test_same_mail_id_is_namespaced_by_owner_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_mail, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(fetch_mail, "MAIL_DIR", tmp_path / "mail")
    mail_data = {
        "subject": "weekly",
        "sender": "sender@example.test",
        "received": None,
        "body_text": "body",
        "body_html": "",
        "inline_images": [],
        "attachments": [],
    }

    kim = fetch_mail.save_mail(mail_data, "2026-08", "YIELD", "same-mail", "kim")
    lee = fetch_mail.save_mail(mail_data, "2026-08", "YIELD", "same-mail", "lee")

    assert kim != lee
    assert json.loads((kim / "meta.json").read_text())["user_id"] == "kim"
    assert json.loads((lee / "meta.json").read_text())["user_id"] == "lee"


@pytest.mark.parametrize("module", [process_attachment, process_vision])
def test_preprocessors_discover_only_owned_namespaced_mail(module, tmp_path):
    owned = tmp_path / "owner-key" / "2026-08" / "YIELD" / "mail_1"
    ownerless = tmp_path / "2026-08" / "YIELD" / "mail_2"
    for folder, meta in (
        (owned, {"user_id": "kim", "week": "2026-08"}),
        (ownerless, {"week": "2026-08"}),
    ):
        folder.mkdir(parents=True)
        (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    assert module.discover_owned_mail_folders(tmp_path, "2026-08") == [owned]


def test_indexing_rejects_missing_owner_before_embedding(tmp_path, monkeypatch):
    mail_dir = tmp_path / "mail-1"
    mail_dir.mkdir()
    (mail_dir / "combined.txt").write_text("owned text", encoding="utf-8")
    (mail_dir / "meta.json").write_text('{"mail_id":"mail-1"}', encoding="utf-8")
    monkeypatch.setattr(
        embed_vectordb,
        "get_embeddings_batch",
        lambda *_: pytest.fail("missing-owner document must not be embedded"),
    )

    with pytest.raises(ValueError, match="user_id"):
        embed_vectordb.index_original_mail(object(), object(), mail_dir)


def test_indexing_writes_owner_to_every_chunk(tmp_path, monkeypatch):
    mail_dir = tmp_path / "mail-1"
    mail_dir.mkdir()
    (mail_dir / "combined.txt").write_text("owned text", encoding="utf-8")
    (mail_dir / "meta.json").write_text(
        json.dumps({"user_id": "kim", "team": "YIELD", "week": "2026-08"}),
        encoding="utf-8",
    )
    captured = []
    monkeypatch.setattr(embed_vectordb, "get_embeddings_batch", lambda *_: [[0.1]])
    monkeypatch.setattr(
        embed_vectordb.helpers, "bulk", lambda client, actions: captured.extend(actions)
    )

    embed_vectordb.index_original_mail(object(), object(), mail_dir)

    assert captured and {item["_source"]["user_id"] for item in captured} == {"kim"}
    sources = [item["_source"] for item in captured]
    assert all("html_path" not in source for source in sources)
    assert all(
        source["document_locator"].startswith("/v1/mail-content/") for source in sources
    )


def test_operational_logs_redact_paths_and_exception_messages(
    tmp_path, monkeypatch, capsys
):
    mail_dir = tmp_path / "private-owner" / "2026-08" / "YIELD" / "mail_secret"
    mail_dir.mkdir(parents=True)
    (mail_dir / "meta.json").write_text(
        json.dumps({"user_id": "kim", "mail_id": "mail_secret"}),
        encoding="utf-8",
    )
    secret = "https://token@example.test/private/path"

    process_attachment.process_mail_folder(mail_dir)
    attachment_log = capsys.readouterr().out

    monkeypatch.setattr(process_vision, "DATA_DIR", tmp_path)
    monkeypatch.setattr(
        process_vision,
        "process_mail_folder",
        lambda _folder: (_ for _ in ()).throw(RuntimeError(secret)),
    )
    process_vision.process_all()
    vision_log = capsys.readouterr().out

    class FakeIndices:
        def exists(self, **_kwargs):
            return True

        def refresh(self, **_kwargs):
            return None

    class FakeOpenSearch:
        indices = FakeIndices()

        def info(self):
            return {"version": {"distribution": "test", "number": "1"}}

    monkeypatch.setattr(embed_vectordb, "DATA_DIR", tmp_path)
    monkeypatch.setattr(embed_vectordb, "get_opensearch_client", FakeOpenSearch)
    monkeypatch.setattr(embed_vectordb, "get_embedding_client", lambda: object())
    monkeypatch.setattr(
        embed_vectordb,
        "process_mail_folder",
        lambda *_args: (_ for _ in ()).throw(RuntimeError(secret)),
    )
    embed_vectordb.process_all()
    embed_log = capsys.readouterr().out

    for output in (attachment_log, vision_log, embed_log):
        assert secret not in output
        assert str(tmp_path) not in output
        assert "mail_secret" not in output
    for output in (vision_log, embed_log):
        assert "RuntimeError" in output


def test_same_mail_id_has_distinct_index_ids_for_different_owners(
    tmp_path, monkeypatch
):
    captured = []
    monkeypatch.setattr(embed_vectordb, "get_embeddings_batch", lambda *_: [[0.1]])
    monkeypatch.setattr(
        embed_vectordb.helpers, "bulk", lambda client, actions: captured.extend(actions)
    )
    for owner in ("kim", "lee"):
        mail_dir = tmp_path / owner / "mail-1"
        mail_dir.mkdir(parents=True)
        (mail_dir / "combined.txt").write_text("owned text", encoding="utf-8")
        (mail_dir / "meta.json").write_text(
            json.dumps({"user_id": owner, "team": "YIELD", "week": "2026-08"}),
            encoding="utf-8",
        )
        embed_vectordb.index_original_mail(object(), object(), mail_dir)

    assert len({item["_id"] for item in captured}) == 2


def test_backfill_targets_only_missing_owner_and_uses_explicit_value():
    body = build_update_query("kim", "corpus_id", "legacy-2026")
    assert body["query"] == {
        "bool": {
            "filter": [{"term": {"corpus_id": "legacy-2026"}}],
            "must_not": [{"exists": {"field": "user_id"}}],
        }
    }
    assert body["script"]["params"] == {"user_id": "kim"}


def test_backfill_is_dry_run_by_default_and_rejects_blank_owner():
    args = parse_args(
        [
            "--index",
            "mail",
            "--user-id",
            "kim",
            "--partition-field",
            "corpus_id",
            "--partition-value",
            "legacy",
            "--expected-count",
            "2",
        ]
    )
    assert args.apply is False
    with pytest.raises(ValueError):
        build_update_query(" ", "corpus_id", "legacy")


def test_backfill_mixed_corpus_changes_only_approved_partition(tmp_path):
    class Client:
        def __init__(self):
            self.docs = [
                {"corpus_id": "approved"},
                {"corpus_id": "approved"},
                {"corpus_id": "other"},
                {"corpus_id": "approved", "user_id": "lee"},
            ]

        def count(self, index, body):
            query = body["query"]
            partition = query["bool"]["filter"][0]["term"]
            docs = [
                d for d in self.docs if all(d.get(k) == v for k, v in partition.items())
            ]
            if query["bool"].get("must_not"):
                docs = [d for d in docs if "user_id" not in d]
            if query["bool"].get("filter", [None, None])[-1] != {"term": partition}:
                owner = query["bool"]["filter"][-1].get("term", {}).get("user_id")
                if owner:
                    docs = [d for d in docs if d.get("user_id") == owner]
            return {"count": len(docs)}

        def update_by_query(self, index, body, **kwargs):
            for doc in self.docs:
                if doc.get("corpus_id") == "approved" and "user_id" not in doc:
                    doc["user_id"] = body["script"]["params"]["user_id"]
            return {"updated": 2, "version_conflicts": 0}

    client = Client()
    report = run_backfill(
        client,
        index="mail",
        user_id="kim",
        partition_field="corpus_id",
        partition_value="approved",
        expected_count=2,
        checkpoint_path=tmp_path / "checkpoint.json",
        apply=True,
    )
    assert (
        report["updated"] == 2
        and report["conflicts"] == 0
        and report["eligible_after"] == 0
    )
    assert client.docs[2] == {"corpus_id": "other"}
    assert client.docs[3]["user_id"] == "lee"


def test_parent_child_is_deterministic_and_reuses_existing_embedding():
    source = {
        "user_id": "kim",
        "mail_id": "m1",
        "text": "source text",
        "part_index": 2,
        "embedding": [0.1, 0.2],
    }
    first = build_parent_child(source)
    second = build_parent_child(source)

    assert first == second
    assert first[1][0]["embedding"] == [0.1, 0.2]


def test_parent_child_groups_compatible_legacy_chunks_and_only_reuses_exact_embedding():
    records = [
        {
            "_id": "p0",
            "_source": {
                "user_id": "kim",
                "mail_id": "m1",
                "section": "body",
                "part_index": 0,
                "text": "first",
                "embedding": [0.1],
            },
        },
        {
            "_id": "p1",
            "_source": {
                "user_id": "kim",
                "mail_id": "m1",
                "section": "body",
                "part_index": 1,
                "text": "second",
                "embedding": [0.2],
            },
        },
    ]
    plan = plan_migration(records, allowed_owners={"kim"})
    assert len(plan.parent_actions) == 1
    assert plan.parent_actions[0]["_source"]["text"] == "first\n\nsecond"
    children = sorted(
        plan.child_actions, key=lambda item: item["_source"]["part_index"]
    )
    assert [item["_source"]["text"] for item in children] == ["first", "second"]
    assert [item["_source"].get("embedding") for item in children] == [[0.1], [0.2]]


def test_parent_child_migration_is_dry_run_by_default():
    args = parse_parent_args(
        [
            "--source-index",
            "legacy",
            "--parent-index",
            "parent-v2",
            "--child-index",
            "child-v2",
            "--owner",
            "kim",
        ]
    )
    assert args.apply is False


@pytest.mark.parametrize(
    "script",
    ["backfill_user_id.py", "backfill_parent_child.py", "shadow_retrieval.py"],
)
def test_migration_scripts_support_direct_cli_execution(script):
    result = subprocess.run(
        [sys.executable, str(Path("scripts") / script), "--help"],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_migration_skips_unknown_and_cross_owner_documents_and_reports_collisions():
    records = [
        {"_id": "owned", "_source": {"user_id": "kim", "mail_id": "m1", "text": "one"}},
        {
            "_id": "foreign",
            "_source": {"user_id": "lee", "mail_id": "m2", "text": "two"},
        },
        {"_id": "missing", "_source": {"mail_id": "m3", "text": "three"}},
        {
            "_id": "collision",
            "_source": {"user_id": "kim", "mail_id": "m1", "text": "different"},
        },
    ]

    plan = plan_migration(records, allowed_owners={"kim"})

    assert {action["_source"]["user_id"] for action in plan.parent_actions} == {"kim"}
    assert {action["_source"]["user_id"] for action in plan.child_actions} == {"kim"}
    assert plan.report.missing_owner_ids == ["missing"]
    assert plan.report.unknown_owner_ids == ["foreign"]
    assert plan.report.collision_ids
    with pytest.raises(ValueError, match="collision"):
        build_bulk_actions(plan, "parent-v2", "child-v2")


def test_migration_reports_deterministic_id_collisions_across_batches():
    seen = {}
    first = plan_migration(
        [{"_id": "one", "_source": {"user_id": "kim", "mail_id": "m1", "text": "one"}}],
        allowed_owners={"kim"},
        seen_parent_hashes=seen,
    )
    second = plan_migration(
        [{"_id": "two", "_source": {"user_id": "kim", "mail_id": "m1", "text": "two"}}],
        allowed_owners={"kim"},
        seen_parent_hashes=seen,
    )

    assert not first.report.collision_ids
    assert second.report.collision_ids
    assert second.parent_actions == []


def test_checkpoint_rejects_mismatched_complete_migration_identity():
    identity = build_migration_identity(
        "legacy", "parent-v2", "child-v2", {"kim"}, "mail-v2", "child-v2"
    )
    assert set(identity) == {
        "migration_version",
        "source_index",
        "parent_index",
        "child_index",
        "owner_mapping_digest",
        "parser_version",
        "chunker_version",
        "batch_size",
        "snapshot_strategy",
        "scroll_keepalive",
        "embedding_strategy",
        "canonical_hash_version",
        "mutable_document_fields",
        "parent_template_version",
        "child_template_version",
    }
    validate_checkpoint({"identity": identity}, identity)
    changed = {**identity, "source_index": "other"}
    with pytest.raises(ValueError, match="checkpoint"):
        validate_checkpoint({"identity": identity}, changed)


def test_canonical_collision_hash_covers_complete_mapped_document():
    base = {
        "user_id": "kim",
        "content_hash": "same",
        "team": "YIELD",
        "indexed_at": "old",
    }
    mutable_only = {**base, "indexed_at": "new"}
    changed_facet = {**base, "team": "FA"}
    assert canonical_document_hash(base) == canonical_document_hash(mutable_only)
    assert canonical_document_hash(base) != canonical_document_hash(changed_facet)


def test_scroll_snapshot_is_cleaned_up_even_when_iteration_fails():
    class Client:
        def __init__(self):
            self.cleared = []

        def search(self, **kwargs):
            return {
                "_scroll_id": "scroll-1",
                "hits": {"hits": [{"_id": "one", "_source": {}}]},
            }

        def scroll(self, **kwargs):
            raise RuntimeError("late failure")

        def clear_scroll(self, **kwargs):
            self.cleared.append(kwargs["scroll_id"])

    client = Client()
    with pytest.raises(RuntimeError, match="late failure"):
        collect_source_snapshot(client, "legacy", batch_size=1)
    assert client.cleared == ["scroll-1"]


def test_late_cross_batch_collision_causes_zero_writes(tmp_path):
    records = [
        {"_id": "one", "_source": {"user_id": "kim", "mail_id": "m1", "text": "one"}},
        {"_id": "two", "_source": {"user_id": "kim", "mail_id": "m1", "text": "two"}},
    ]

    class Client:
        def __init__(self):
            self.scroll_calls = 0
            self.bulk_calls = []

        def search(self, **kwargs):
            return {"_scroll_id": "s", "hits": {"hits": records[:1]}}

        def scroll(self, **kwargs):
            self.scroll_calls += 1
            hits = records[1:] if self.scroll_calls == 1 else []
            return {"_scroll_id": "s", "hits": {"hits": hits}}

        def clear_scroll(self, **kwargs):
            pass

        def mget(self, **kwargs):
            return {"docs": []}

    client = Client()
    report = run_migration(
        client,
        source_index="legacy",
        parent_index="parent-v2",
        child_index="child-v2",
        allowed_owners={"kim"},
        checkpoint_path=tmp_path / "checkpoint.json",
        apply=True,
        bulk_writer=lambda client, actions: client.bulk_calls.append(actions),
        batch_size=1,
    )
    assert report.collision_ids
    assert client.bulk_calls == []
    assert not (tmp_path / "checkpoint.json").exists()


def test_target_collision_causes_zero_writes(tmp_path):
    record = {
        "_id": "one",
        "_source": {"user_id": "kim", "mail_id": "m1", "text": "one"},
    }

    class Client:
        def __init__(self):
            self.bulk_calls = []

        def search(self, **kwargs):
            return {"_scroll_id": "s", "hits": {"hits": [record]}}

        def scroll(self, **kwargs):
            return {"_scroll_id": "s", "hits": {"hits": []}}

        def clear_scroll(self, **kwargs):
            pass

        def mget(self, *, index, body):
            return {
                "docs": [
                    {
                        "_id": body["ids"][0],
                        "found": True,
                        "_source": {"user_id": "lee", "content_hash": "same"},
                    }
                ]
            }

    client = Client()
    report = run_migration(
        client,
        source_index="legacy",
        parent_index="parent-v2",
        child_index="child-v2",
        allowed_owners={"kim"},
        checkpoint_path=tmp_path / "checkpoint.json",
        apply=True,
        bulk_writer=lambda client, actions: client.bulk_calls.append(actions),
    )
    assert report.collision_ids
    assert client.bulk_calls == []
    assert not (tmp_path / "checkpoint.json").exists()


def test_dummy_ids_and_files_are_owner_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(dummy_weeks, "TEAM_WEEK_DIR", tmp_path)
    assert dummy_weeks._doc_id("kim", "YIELD", "2026-08") != dummy_weeks._doc_id(
        "lee", "YIELD", "2026-08"
    )
    assert dummy_weeks._file_path("kim", "YIELD", "2026-08") != dummy_weeks._file_path(
        "lee", "YIELD", "2026-08"
    )


def test_dummy_generation_does_not_swallow_owner_contract_type_errors(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(dummy_weeks, "TEAM_WEEK_DIR", tmp_path)
    monkeypatch.setattr(dummy_weeks, "month_to_weeks", lambda month: ["2026-08"])
    monkeypatch.setattr(dummy_weeks, "teams_by_group", {"group": ["YIELD"]})
    monkeypatch.setattr(
        dummy_weeks,
        "_index_to_os",
        lambda *args: (_ for _ in ()).throw(TypeError("owner contract")),
    )
    with pytest.raises(TypeError, match="owner contract"):
        dummy_weeks.cmd_generate("2026-08", no_index=False, user_id="kim")


def test_dummy_purge_is_exact_owner_scoped(monkeypatch, tmp_path):
    class Indices:
        def exists(self, **kwargs):
            return True

        def refresh(self, **kwargs):
            pass

    class Client:
        indices = Indices()

        def __init__(self):
            self.body = None
            self.deleted = []

        def search(self, *, index, body):
            self.body = body
            return {
                "hits": {
                    "hits": [
                        {"_id": "owned", "_source": {"user_id": "kim"}},
                        {"_id": "foreign", "_source": {"user_id": "lee"}},
                    ]
                }
            }

        def delete(self, *, index, id, refresh):
            self.deleted.append(id)

    client = Client()
    monkeypatch.setattr(dummy_weeks, "get_client", lambda: client)
    monkeypatch.setattr(dummy_weeks, "TEAM_WEEK_DIR", tmp_path)
    dummy_weeks.cmd_purge(None, "kim")

    assert client.body["query"]["bool"]["filter"][0] == {"term": {"user_id": "kim"}}
    assert client.deleted == ["owned"]


def test_shadow_comparison_drops_cross_owner_and_does_not_emit_query_or_source():
    output = compare_rankings(
        "sensitive question",
        "kim",
        [
            {"document_id": "a", "user_id": "kim", "source_locator": "/secret/a"},
            {"document_id": "b", "user_id": "lee"},
            {"document_id": "c"},
        ],
        [{"document_id": "a", "user_id": "kim"}],
        v1_latency_ms=1.25,
        v2_latency_ms=2.5,
    )

    assert output["v1_document_ids"] == ["a"]
    assert output["v2_document_ids"] == ["a"]
    assert "query" not in output
    assert "/secret" not in json.dumps(output)


def test_shadow_runner_propagates_exact_owner_to_both_services():
    class Result:
        evidence = []

    class Service:
        def __init__(self):
            self.calls = []

        async def search(self, task, policy):
            self.calls.append((task, policy))
            return Result()

    v1, v2 = Service(), Service()
    rows = asyncio.run(run_shadow("kim", ["question"], v1, v2))

    assert len(rows) == 1
    assert v1.calls[0][1].user_id == "kim"
    assert v2.calls[0][1].user_id == "kim"
    assert v1.calls[0][0].query == v2.calls[0][0].query == "question"


def test_wiki_owner_filter_is_exact_and_team_is_only_a_facet():
    assert wiki_builder.owner_filter("kim") == {"term": {"user_id": "kim"}}
    with pytest.raises(ValueError, match="user_id"):
        wiki_builder.owner_filter(" ")


def test_wiki_source_query_is_owner_scoped_and_cross_owner_hits_are_discarded():
    class SearchClient:
        def __init__(self):
            self.body = None

        def search(self, *, index, body):
            self.body = body
            return {
                "hits": {
                    "hits": [
                        {"_id": "owned", "_source": {"user_id": "kim", "text": "a"}},
                        {"_id": "foreign", "_source": {"user_id": "lee", "text": "b"}},
                        {"_id": "missing", "_source": {"text": "c"}},
                    ]
                }
            }

    client = SearchClient()
    chunks = wiki_builder.fetch_chunks_for_team_week(
        client, "YIELD", "2026-08", user_id="kim"
    )

    assert client.body["query"]["bool"]["filter"][0] == {"term": {"user_id": "kim"}}
    assert [chunk["id"] for chunk in chunks] == ["owned"]


def test_wiki_save_requires_owner_and_uses_owner_scoped_stable_id(monkeypatch):
    class IndexClient:
        def __init__(self):
            self.calls = []

        def get(self, **kwargs):
            raise KeyError

        def index(self, **kwargs):
            self.calls.append(kwargs)

    client = IndexClient()
    monkeypatch.setattr(wiki_builder, "get_embedding", lambda *_: [0.1])

    wiki_builder.save_wiki_doc(
        client,
        object(),
        text="summary",
        title="title",
        summary_type="overview",
        user_id="kim",
        doc_id="overview_2026-08",
    )

    call = client.calls[0]
    assert call["body"]["user_id"] == "kim"
    assert call["id"] == wiki_builder.owner_scoped_doc_id("kim", "overview_2026-08")
    with pytest.raises(ValueError, match="user_id"):
        wiki_builder.save_wiki_doc(
            client, object(), "summary", "title", "overview", user_id=" "
        )
