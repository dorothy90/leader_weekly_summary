from app.llm.answer_format import (
    ensure_rag_answer_structure,
    prepend_summary_notice,
)


HEADINGS = ("### 요약", "### 상세설명", "### 핵심결론")


def assert_section_contract(answer: str) -> None:
    assert answer.startswith(HEADINGS[0])
    assert all(answer.count(heading) == 1 for heading in HEADINGS)
    assert [answer.index(heading) for heading in HEADINGS] == sorted(
        answer.index(heading) for heading in HEADINGS
    )


def test_formatter_preserves_valid_three_section_answer():
    answer = (
        "### 요약\n요약\n\n"
        "### 상세설명\n근거 [S1]\n\n"
        "### 핵심결론\n결론 [S1]"
    )

    assert ensure_rag_answer_structure(answer) == answer


def test_formatter_wraps_malformed_answer_without_losing_citation():
    answer = ensure_rag_answer_structure("확인된 사실입니다 [S1]")

    assert_section_contract(answer)
    assert "확인된 사실입니다 [S1]" in answer


def test_summary_notice_stays_inside_summary_section():
    answer = prepend_summary_notice(
        "근거가 부족합니다.",
        "확인 범위가 제한적입니다.",
    )

    assert_section_contract(answer)
    assert answer.index("확인 범위가 제한적입니다.") < answer.index("### 상세설명")
