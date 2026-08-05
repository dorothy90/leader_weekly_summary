from app.domain.chat import BM25_FALLBACK_DISCLOSURE, normalize_bm25_fallback


RAG_SECTION_HEADINGS = ("### 요약", "### 상세설명", "### 핵심결론")

RAG_ANSWER_STRUCTURE_INSTRUCTION = """Return exactly these Markdown sections in this order:

### 요약
Briefly summarize the retrieved findings.

### 상세설명
Explain the findings using only the supplied evidence. Cite every factual claim with an existing [S#].

### 핵심결론
State the most important supported conclusion and any evidence limitations.

Do not add any other sections."""


def _truncate_utf8(text: str, byte_limit: int) -> str:
    return text.encode("utf-8")[:byte_limit].decode("utf-8", errors="ignore")


def ensure_rag_answer_structure(
    answer: str,
    *,
    max_bytes: int | None = None,
) -> str:
    text = answer.strip()
    h3_headings = tuple(
        line for line in text.splitlines() if line.startswith("### ")
    )
    valid = h3_headings == RAG_SECTION_HEADINGS
    if valid and (max_bytes is None or len(text.encode("utf-8")) <= max_bytes):
        return text

    detail = text or "확인 가능한 내용이 없습니다."
    detail = "\n".join(
        line.removeprefix("### ") if line.startswith("### ") else line
        for line in detail.splitlines()
    )
    prefix = (
        "### 요약\n요청 결과를 아래와 같이 정리합니다.\n\n### 상세설명\n"
    )
    suffix = "\n\n### 핵심결론\n상세설명에 제시된 확인 범위를 참고해주세요."
    if max_bytes is not None:
        available = max_bytes - len(f"{prefix}{suffix}".encode("utf-8"))
        if available < 0:
            raise ValueError("max_bytes is too small for the RAG answer structure")
        detail = _truncate_utf8(detail, available).strip()
    return f"{prefix}{detail}{suffix}"


def ensure_rag_answer_with_bm25_disclosure(
    answer: str,
    disclosures: list[str],
    *,
    max_bytes: int,
) -> tuple[str, list[str]]:
    fallback = BM25_FALLBACK_DISCLOSURE in answer or any(
        BM25_FALLBACK_DISCLOSURE in item for item in disclosures
    )
    base = answer.replace(BM25_FALLBACK_DISCLOSURE, "").strip()
    suffix_bytes = (
        len(f"\n\n{BM25_FALLBACK_DISCLOSURE}".encode("utf-8")) if fallback else 0
    )
    structured = ensure_rag_answer_structure(
        base,
        max_bytes=max_bytes - suffix_bytes,
    )
    return normalize_bm25_fallback(
        structured,
        disclosures,
        max_bytes=max_bytes,
    )


def prepend_summary_notice(answer: str, notice: str) -> str:
    formatted = ensure_rag_answer_structure(answer)
    safe_notice = notice.strip()
    if not safe_notice:
        return formatted
    summary = formatted.split("### 상세설명", 1)[0]
    notice_key = safe_notice.splitlines()[0].strip()
    if notice_key and notice_key in summary:
        return formatted
    return formatted.replace(
        "### 요약\n",
        f"### 요약\n{safe_notice}\n\n",
        1,
    )
