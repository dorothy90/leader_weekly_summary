import re


IDENTITY_ANSWER = (
    "저는 Weekly Mail Assistant입니다. 사용자별 메일 근거를 검색하고 "
    "Fast 답변과 Deep Research 보고서를 제공하는 도우미입니다."
)

_NAME_DECLARATION = re.compile(
    r"내\s*이름\s*은\s*([가-힣A-Za-z][가-힣A-Za-z0-9_-]{0,39})"
)

_IDENTITY_QUESTIONS = frozenset(
    {
        "넌누구야",
        "너누구야",
        "너는누구야",
        "넌누구니",
        "너는누구니",
        "누구세요",
        "당신은누구세요",
        "whoareyou",
    }
)


def is_identity_question(text: str) -> bool:
    compact = "".join(text.casefold().strip().split()).rstrip("?!？！.")
    return compact in _IDENTITY_QUESTIONS


def declared_name(text: str) -> str | None:
    match = _NAME_DECLARATION.search(text.strip())
    return match.group(1) if match else None


def is_name_recall_question(text: str) -> bool:
    compact = "".join(text.casefold().strip().split()).rstrip("?!？！.")
    return compact.startswith("내이름") and any(
        token in compact for token in ("뭐", "무엇", "기억", "알려")
    )


def remembered_name(conversation: object | None) -> str | None:
    turns = list(getattr(conversation, "turns", None) or [])
    for turn in reversed(turns):
        execution = getattr(turn, "execution", None)
        if execution is not None and execution.status != "succeeded":
            continue
        if name := declared_name(str(getattr(turn, "user_content", ""))):
            return name
    messages = list(getattr(conversation, "messages", None) or [])
    for message in reversed(messages):
        if message.get("role") == "user" and (
            name := declared_name(str(message.get("content", "")))
        ):
            return name
    return None
