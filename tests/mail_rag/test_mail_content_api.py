import json

from app.api.dependencies import ServiceContainer
from app.api.main import create_app
from app.content.mail import MailContentStore, mail_content_id, owner_storage_key
from tests.mail_rag.test_chat_api import ASGIClient


def _client(root):
    return ASGIClient(
        create_app(
            ServiceContainer(
                router=None,
                fast=None,
                deep=None,
                conversations=None,
                jobs=None,
                mail_content=MailContentStore(root),
            )
        )
    )


def _write_mail(root, owner, mail_id, html="<p>owned</p>"):
    content_id = mail_content_id(owner, mail_id)
    folder = root / owner_storage_key(owner) / "2026-08" / "YIELD" / mail_id
    folder.mkdir(parents=True)
    (folder / "meta.json").write_text(
        json.dumps({"user_id": owner, "mail_id": mail_id, "content_id": content_id}),
        encoding="utf-8",
    )
    (folder / "body.html").write_text(html, encoding="utf-8")
    return content_id


def test_mail_content_serves_only_the_exact_owner(tmp_path):
    content_id = _write_mail(tmp_path, "kim", "mail_opaque")

    response = _client(tmp_path).post(
        f"/v1/mail-content/{content_id}", json={"user_id": "kim"}
    )

    assert response.status_code == 200
    assert response.text == "<p>owned</p>"
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["content-security-policy"].startswith("sandbox;")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_foreign_and_missing_mail_content_are_indistinguishable(tmp_path):
    content_id = _write_mail(tmp_path, "kim", "mail_opaque")
    client = _client(tmp_path)

    foreign = client.post(f"/v1/mail-content/{content_id}", json={"user_id": "lee"})
    missing = client.post(f"/v1/mail-content/{'0' * 64}", json={"user_id": "lee"})

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"] == missing.json()["error"]
    assert str(tmp_path) not in foreign.text + missing.text


def test_mail_content_rejects_path_traversal_without_leaking_paths(tmp_path):
    response = _client(tmp_path).post(
        "/v1/mail-content/../../etc/passwd", json={"user_id": "kim"}
    )

    assert response.status_code == 404
    assert str(tmp_path) not in response.text
    assert "/etc/passwd" not in response.text


def test_mail_content_rejects_a_body_symlink_outside_owner_namespace(tmp_path):
    owner = "kim"
    mail_id = "mail_symlink"
    content_id = mail_content_id(owner, mail_id)
    folder = tmp_path / owner_storage_key(owner) / "2026-08" / "YIELD" / mail_id
    folder.mkdir(parents=True)
    (folder / "meta.json").write_text(
        json.dumps({"user_id": owner, "mail_id": mail_id, "content_id": content_id}),
        encoding="utf-8",
    )
    outside = tmp_path / "outside-secret.html"
    outside.write_text("secret", encoding="utf-8")
    (folder / "body.html").symlink_to(outside)

    response = _client(tmp_path).post(
        f"/v1/mail-content/{content_id}", json={"user_id": owner}
    )

    assert response.status_code == 404
    assert str(outside) not in response.text


def test_mail_content_rejects_an_owner_namespace_symlink_outside_root(tmp_path):
    root = tmp_path / "configured-root"
    root.mkdir()
    outside = tmp_path / "outside-owner-root"
    content_id = _write_mail(outside.parent, "kim", "mail_escape")
    outside_source = outside.parent / owner_storage_key("kim")
    outside_source.rename(outside)
    (root / owner_storage_key("kim")).symlink_to(outside, target_is_directory=True)

    response = _client(root).post(
        f"/v1/mail-content/{content_id}", json={"user_id": "kim"}
    )

    assert response.status_code == 404
    assert "mail_escape" not in response.text
    assert str(outside) not in response.text


def test_all_traversal_routing_variants_use_the_safe_not_found_envelope(tmp_path):
    client = _client(tmp_path)
    expected = client.post(f"/v1/mail-content/{'0' * 64}", json={"user_id": "kim"})

    responses = [
        client.post(path, json={"user_id": "kim"})
        for path in (
            "/v1/mail-content/../../etc/passwd",
            "/v1/mail-content/%2e%2e/%2e%2e/etc/passwd",
            "/v1/mail-content/not-an-id/../escape",
        )
    ]

    assert expected.status_code == 404
    for response in responses:
        assert response.status_code == 404
        assert response.json()["error"] == expected.json()["error"]
        assert response.json()["error"]["code"] == "UNAUTHORIZED_RESOURCE"
        assert "detail" not in response.json()
        assert "/etc/passwd" not in response.text


def test_global_not_found_envelope_does_not_change_request_validation(tmp_path):
    response = _client(tmp_path).post(f"/v1/mail-content/{'0' * 64}", json={})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_ownerless_mail_content_is_invisible(tmp_path):
    folder = tmp_path / "legacy" / "2026-08" / "YIELD" / "mail_legacy"
    folder.mkdir(parents=True)
    (folder / "meta.json").write_text(
        json.dumps({"mail_id": "mail_legacy"}), encoding="utf-8"
    )
    (folder / "body.html").write_text("secret", encoding="utf-8")

    response = _client(tmp_path).post(
        f"/v1/mail-content/{mail_content_id('kim', 'mail_legacy')}",
        json={"user_id": "kim"},
    )

    assert response.status_code == 404
