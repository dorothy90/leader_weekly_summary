import re
from hashlib import sha256


def sanitize_text(value: str) -> str:
    sanitized = re.sub(
        r"<think>.*?</think>",
        "",
        value,
        flags=re.DOTALL | re.IGNORECASE,
    )
    sanitized = re.sub(
        r"(?im)^.*(?:chain[- ]of[- ]thought|internal reasoning|내부 추론).*$",
        "",
        sanitized,
    )
    sanitized = re.sub(
        r"(?i)https?://[^\s/@:]+:[^\s/@]+@[^\s]+",
        "[REDACTED_URL]",
        sanitized,
    )
    sanitized = re.sub(
        r"(?i)\b(?:authorization\s*:\s*)?bearer\s+\S+",
        "[REDACTED]",
        sanitized,
    )
    sanitized = re.sub(
        (
            r"(?i)\b(?:[a-z0-9]+[_-])*(?:api[ \t_-]*key|"
            r"client[ \t_-]*secret|secret[ \t_-]*access[ \t_-]*key|"
            r"access[ \t_-]*key|access[ \t_-]*token|"
            r"refresh[ \t_-]*token|private[ \t_-]*key|password|secret|token)"
            r"\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|\S+)"
        ),
        "[REDACTED]",
        sanitized,
    )
    sanitized = re.sub(
        r"(?i)\bfile:(?://)?/[^\s,;)]*",
        "[REDACTED_PATH]",
        sanitized,
    )
    sanitized = re.sub(
        r"(?<![\w.])(?:\.\.?[/\\])+[^\s,;)]*",
        "[REDACTED_PATH]",
        sanitized,
    )
    sanitized = re.sub(
        (
            r"(?<![\w:/<])/{2,}[^\s,;)<>]+|"
            r"(?<![\w:/<])/(?!/)(?:(?i:tmp|var|etc|home|users|srv|opt|usr|"
            r"private|root|data)(?:/[^\s,;)<>]+)*|"
            r"(?:[^/\s<>]+/)+[^\s,;)<>]+|"
            r"[^/\s<>]+\.[A-Za-z0-9]{1,12}|"
            r"(?!(?i:help)(?=$|[\s,;.!?)]))[A-Za-z0-9._~-]+)"
        ),
        "[REDACTED_PATH]",
        sanitized,
    )
    sanitized = re.sub(
        r"(?i)\b[A-Z]:\\[^\s,;)]*",
        "[REDACTED_PATH]",
        sanitized,
    )
    sanitized = re.sub(
        r"\\\\[^\\\s]+\\[^\s,.;)]*",
        "[REDACTED_PATH]",
        sanitized,
    )
    return sanitized.strip()


def opaque_identifier(value: str) -> str:
    sanitized = sanitize_text(value)
    if sanitized == value and sanitized:
        return sanitized
    digest = sha256(value.encode()).hexdigest()[:24]
    return f"opaque:{digest}"
