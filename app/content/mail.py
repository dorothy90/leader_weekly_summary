import json
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from app.domain.policy import PolicyContext

_CONTENT_ID = re.compile(r"^[0-9a-f]{64}$")


def owner_storage_key(user_id: str) -> str:
    owner = str(user_id or "").strip()
    if not owner:
        raise ValueError("user_id is required for storage namespacing")
    return sha256(f"owner:{owner}".encode()).hexdigest()[:24]


def mail_content_id(user_id: str, mail_id: str) -> str:
    owner = str(user_id or "").strip()
    opaque_mail_id = str(mail_id or "").strip()
    if not owner or not opaque_mail_id:
        raise ValueError("owner and mail identity are required")
    payload = f"mail-content-v1\x1f{owner}\x1f{opaque_mail_id}"
    return sha256(payload.encode()).hexdigest()


def mail_content_locator(content_id: str) -> str:
    if not _CONTENT_ID.fullmatch(str(content_id or "")):
        raise ValueError("invalid content identity")
    return f"/v1/mail-content/{content_id}"


def safe_mail_log_context(mail_dir: Path) -> str:
    """Return opaque identifiers only; never derive log text from a raw path."""
    try:
        meta = json.loads((mail_dir / "meta.json").read_text(encoding="utf-8"))
        owner = str(meta.get("user_id") or "").strip()
        mail_id = str(meta.get("mail_id") or mail_dir.name).strip()
        if owner and mail_id:
            return (
                f"owner={owner_storage_key(owner)} "
                f"content={mail_content_id(owner, mail_id)}"
            )
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return "owner=unknown content=unknown"


@dataclass(frozen=True)
class MailContentStore:
    root: Path

    def resolve(self, content_id: str, policy: PolicyContext) -> Path | None:
        """Resolve an opaque ID within one owner's namespace, failing closed."""
        if not _CONTENT_ID.fullmatch(str(content_id or "")):
            return None
        configured_root = self.root.resolve()
        owner_root = (configured_root / owner_storage_key(policy.user_id)).resolve()
        if not owner_root.is_relative_to(configured_root) or not owner_root.is_dir():
            return None
        try:
            metadata_files = owner_root.glob("*/*/*/meta.json")
            for meta_path in metadata_files:
                try:
                    resolved_meta = meta_path.resolve(strict=True)
                    if not resolved_meta.is_relative_to(owner_root):
                        continue
                    meta = json.loads(resolved_meta.read_text(encoding="utf-8"))
                    owner = str(meta.get("user_id") or "").strip()
                    mail_id = str(
                        meta.get("mail_id") or resolved_meta.parent.name
                    ).strip()
                    if owner != policy.user_id or not mail_id:
                        continue
                    expected = mail_content_id(owner, mail_id)
                    if meta.get("content_id") != expected or content_id != expected:
                        continue
                    body = (resolved_meta.parent / "body.html").resolve(strict=True)
                    if body.is_relative_to(owner_root) and body.is_file():
                        return body
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
        except OSError:
            return None
        return None
