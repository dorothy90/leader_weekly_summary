RAG_SECTION_HEADINGS = ("### 요약", "### 상세설명", "### 핵심결론")

RAG_ANSWER_STRUCTURE_INSTRUCTION = """Return exactly these Markdown sections in this order:

### 요약
Briefly summarize the retrieved findings.

### 상세설명
Explain the findings using only the supplied evidence. Cite every factual claim with an existing [S#].

### 핵심결론
State the most important supported conclusion and any evidence limitations.

Do not add any other sections."""


def ensure_rag_answer_structure(answer: str) -> str:
    text = answer.strip()
    positions = [text.find(heading) for heading in RAG_SECTION_HEADINGS]
    valid = (
        text.startswith(RAG_SECTION_HEADINGS[0])
        and all(text.count(heading) == 1 for heading in RAG_SECTION_HEADINGS)
        and positions == sorted(positions)
    )
    if valid:
        return text

    detail = text or "확인 가능한 내용이 없습니다."
    return (
        "### 요약\n요청 결과를 아래와 같이 정리합니다.\n\n"
        f"### 상세설명\n{detail}\n\n"
        "### 핵심결론\n상세설명에 제시된 확인 범위를 참고해주세요."
    )


def prepend_summary_notice(answer: str, notice: str) -> str:
    formatted = ensure_rag_answer_structure(answer)
    safe_notice = notice.strip()
    if not safe_notice:
        return formatted
    summary = formatted.split("### 상세설명", 1)[0]
    if safe_notice in summary:
        return formatted
    return formatted.replace(
        "### 요약\n",
        f"### 요약\n{safe_notice}\n\n",
        1,
    )
