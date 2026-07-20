from pathlib import Path

import pytest

from source_mail import render_source_mail, resolve_mail_html


def test_resolve_mail_html_accepts_only_files_under_mail_root(tmp_path):
    root = tmp_path / "data"
    mail_dir = root / "2026-W30" / "Yield" / "M-001"
    mail_dir.mkdir(parents=True)
    body = mail_dir / "body.html"
    body.write_text("<p>mail</p>", encoding="utf-8")

    assert resolve_mail_html(
        "2026-W30/Yield/M-001/combined.txt", root
    ) == body.resolve()
    assert resolve_mail_html(
        "data/2026-W30/Yield/M-001/combined.txt", root
    ) == body.resolve()
    assert resolve_mail_html(body, root) == body.resolve()

    outside = tmp_path / "private" / "combined.txt"
    outside.parent.mkdir()
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(ValueError, match="mail data root"):
        resolve_mail_html(outside, root)
    with pytest.raises(ValueError, match="mail data root"):
        resolve_mail_html("../private/combined.txt", root)


def test_render_source_mail_sanitizes_active_content_and_highlights_quote():
    rendered = render_source_mail(
        """
        <html><body onload="steal()">
          <script>steal()</script><iframe src="https://evil.test"></iframe>
          <form action="https://evil.test"><input value="secret"></form>
          <p style="background:url(https://evil.test/x)">4SA yield declined.</p>
          <img src="https://evil.test/pixel.png" onerror="steal()">
          <img src="data:image/png;base64,AAAA" alt="chart">
          <a href="https://evil.test">external</a>
        </body></html>
        """,
        "4SA yield declined.",
    )

    assert '<mark id="agenda-source">4SA yield declined.</mark>' in rendered
    for blocked in ("<script", "steal()", "<iframe", "<form", "<input", "evil.test", "onerror", "onload", "style="):
        assert blocked not in rendered
    assert 'src="data:image/png;base64,AAAA"' in rendered


def test_render_source_mail_shows_quote_fallback_when_html_does_not_match():
    rendered = render_source_mail("<p>Original message.</p>", "Expected agenda quote")

    assert 'class="source-mail-fallback"' in rendered
    assert "Expected agenda quote" in rendered
    assert 'id="agenda-source"' in rendered
