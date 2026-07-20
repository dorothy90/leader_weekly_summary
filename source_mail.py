from __future__ import annotations

import html
from html.parser import HTMLParser
from pathlib import Path


_ALLOWED_TAGS = {
    "html", "head", "title", "body", "article", "section", "div", "p", "br",
    "span", "table", "thead", "tbody", "tfoot", "tr", "td", "th", "ul", "ol",
    "li", "strong", "b", "em", "i", "u", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "code", "img", "hr", "a",
}
_VOID_TAGS = {"br", "hr", "img"}
_DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "svg", "canvas"}
_ALLOWED_ATTRIBUTES = {"class", "title", "alt", "width", "height", "colspan", "rowspan"}


def resolve_mail_html(source_path: str | Path, mail_data_root: Path) -> Path:
    root = mail_data_root.resolve()
    source = Path(source_path)
    candidate = source.resolve() if source.is_absolute() else (root / source).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("Mail source is outside the mail data root") from exc

    if candidate.name == "body.html":
        body = candidate
    elif candidate.suffix:
        body = candidate.parent / "body.html"
    else:
        body = candidate / "body.html"
    try:
        body.resolve().relative_to(root)
    except ValueError as exc:
        raise ValueError("Mail source is outside the mail data root") from exc
    if not body.is_file():
        raise FileNotFoundError("Original mail HTML is unavailable")
    return body.resolve()


class _MailSanitizer(HTMLParser):
    def __init__(self, quote: str) -> None:
        super().__init__(convert_charrefs=False)
        self.quote = quote
        self.output: list[str] = []
        self.drop_depth = 0
        self.highlighted = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _DROP_WITH_CONTENT:
            self.drop_depth += 1
            return
        if self.drop_depth or tag not in _ALLOWED_TAGS:
            return
        safe_attrs: list[str] = []
        for name, value in attrs:
            name = name.lower()
            if value is None or name.startswith("on") or name == "style":
                continue
            if tag == "img" and name == "src":
                if value.lower().startswith("data:image/"):
                    safe_attrs.append(f' src="{html.escape(value, quote=True)}"')
                continue
            if name in _ALLOWED_ATTRIBUTES:
                safe_attrs.append(f' {name}="{html.escape(value, quote=True)}"')
        self.output.append(f"<{tag}{''.join(safe_attrs)}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _DROP_WITH_CONTENT:
            if self.drop_depth:
                self.drop_depth -= 1
            return
        if not self.drop_depth and tag in _ALLOWED_TAGS and tag not in _VOID_TAGS:
            self.output.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self.drop_depth:
            return
        if self.quote and not self.highlighted and self.quote in data:
            before, match, after = data.partition(self.quote)
            self.output.extend((
                html.escape(before),
                f'<mark id="agenda-source">{html.escape(match)}</mark>',
                html.escape(after),
            ))
            self.highlighted = True
            return
        self.output.append(html.escape(data))

    def handle_entityref(self, name: str) -> None:
        if not self.drop_depth:
            self.output.append(f"&amp;{html.escape(name)};")

    def handle_charref(self, name: str) -> None:
        if not self.drop_depth:
            self.output.append(f"&amp;#{html.escape(name)};")


def render_source_mail(raw_html: str, source_quote: str) -> str:
    parser = _MailSanitizer(source_quote.strip())
    parser.feed(raw_html)
    parser.close()
    fallback = ""
    if source_quote.strip() and not parser.highlighted:
        fallback = (
            '<aside id="agenda-source" class="source-mail-fallback">'
            '<strong>이 문서에서 기대한 인용문</strong>'
            f"<blockquote>{html.escape(source_quote.strip())}</blockquote></aside>"
        )
    body = "".join(parser.output)
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>원본 메일</title><style>
body{{max-width:900px;margin:40px auto;padding:0 24px;color:#292722;background:#fffdf8;font:16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
img{{max-width:100%;height:auto}} table{{max-width:100%;border-collapse:collapse}} td,th{{padding:6px;border:1px solid #ddd}}
mark{{background:#fff0a8;padding:.08em .15em}} .source-mail-fallback{{padding:16px 20px;margin-bottom:28px;background:#fff8dc;border-left:3px solid #b89436}}
.source-mail-fallback blockquote{{margin:8px 0 0}}
</style></head><body>{fallback}<main>{body}</main></body></html>"""
