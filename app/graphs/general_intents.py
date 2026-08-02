IDENTITY_ANSWER = (
    "저는 Weekly Mail Assistant입니다. 사용자별 메일 근거를 검색하고 "
    "Fast 답변과 Deep Research 보고서를 제공하는 도우미입니다."
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
