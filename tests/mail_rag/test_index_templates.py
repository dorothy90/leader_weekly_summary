import json
from pathlib import Path

import pytest


TEMPLATE_DIR = Path(__file__).parents[2] / "index_templates"


@pytest.mark.parametrize(
    "name", ["weekly_mail_child_v2", "weekly_mail_parent_v2", "wiki_summaries_v2"]
)
def test_v2_template_is_strict_and_owner_filterable(name):
    body = json.loads((TEMPLATE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    assert body["mappings"]["dynamic"] == "strict"
    assert body["mappings"]["properties"]["user_id"] == {"type": "keyword"}


def test_child_template_supports_exact_parent_child_and_existing_vectors():
    body = json.loads((TEMPLATE_DIR / "weekly_mail_child_v2.json").read_text())
    properties = body["mappings"]["properties"]
    for field in ("child_id", "parent_id", "mail_id", "user_id"):
        assert properties[field] == {"type": "keyword"}
    assert properties["embedding"]["dimension"] == 4096
    assert properties["embedding"]["method"]["engine"] == "faiss"


def test_parent_template_has_no_embedding_requirement():
    body = json.loads((TEMPLATE_DIR / "weekly_mail_parent_v2.json").read_text())
    assert "embedding" not in body["mappings"]["properties"]


@pytest.mark.parametrize("name", ["weekly_mail_child_v2", "weekly_mail_parent_v2"])
def test_mail_templates_support_only_opaque_content_references(name):
    body = json.loads((TEMPLATE_DIR / f"{name}.json").read_text())
    properties = body["mappings"]["properties"]
    assert properties["content_id"] == {"type": "keyword"}
    assert properties["document_locator"] == {"type": "keyword"}
    assert "html_path" not in properties
