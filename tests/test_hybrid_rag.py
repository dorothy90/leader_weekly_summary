from hybrid_rag import (
    FINAL_DOCUMENT_LIMIT,
    AUTO_WIKI_SAVE_ENABLED,
    MAX_ANSWER_REVISIONS,
    MAX_QUERY_REWRITES,
    MAX_SEARCH_ATTEMPTS,
    MAX_SUB_QUESTIONS,
    RetrievalPlan,
    append_trace,
    build_search_tasks,
    build_rewritten_query,
    build_context,
    can_retry_search,
    deduplicate_documents,
    fallback_grade_retrieval,
    fallback_evaluate_answer,
    fallback_retrieval_plan,
    fallback_route,
    make_search_key,
    normalize_result,
    normalize_weeks,
    rerank_documents,
    validate_citations,
    wiki_save_eligible,
)


def test_fallback_route_only_clear_general_conversation_is_general():
    assert fallback_route("안녕하세요") == "general"
    assert fallback_route("고마워") == "general"
    assert fallback_route("사용법 알려줘") == "general"
    assert fallback_route("이 시스템은 뭐 하는 거야?") == "general"


def test_fallback_route_clear_statistics_question_is_statistics():
    assert fallback_route("이번 주 팀별 보고서 제출 현황을 알려줘") == "statistics"
    assert fallback_route("48주차 미제출 팀은 몇 개야?") == "statistics"
    assert fallback_route("이번 주 어느 팀이 제출했어?") == "statistics"


def test_fallback_route_ambiguous_or_domain_question_defaults_to_search():
    assert fallback_route("YIELD팀 이번 주 주요 이슈") == "search"
    assert fallback_route("ALD") == "search"
    assert fallback_route("자세히 알려줘") == "search"


def test_normalize_weeks_accepts_api_string_or_router_list():
    assert normalize_weeks("2026-28") == ["2026-28"]
    assert normalize_weeks("2026-27, 2026-28") == ["2026-27", "2026-28"]
    assert normalize_weeks(["2026-27", "2026-28", "2026-27"]) == [
        "2026-27",
        "2026-28",
    ]
    assert normalize_weeks(None) is None


def test_append_trace_returns_json_serializable_event_without_mutating_input():
    original = [{"event": "start"}]

    updated = append_trace(
        original,
        "retrieval_completed",
        team="YIELD팀",
        weeks=["2026-28"],
        result_count=3,
    )

    assert original == [{"event": "start"}]
    assert updated[-1] == {
        "event": "retrieval_completed",
        "team": "YIELD팀",
        "weeks": ["2026-28"],
        "result_count": 3,
    }


def mail_result(mail_id="mail-1", part_index=0, text="수율 defect 개선"):
    return {
        "score": 10.0,
        "text": text,
        "team": "YIELD팀",
        "week": "2026-28",
        "mail_id": mail_id,
        "html_path": f"mail/{mail_id}.html",
        "part_index": part_index,
        "total_parts": 3,
    }


def test_normalize_result_creates_stable_common_document_schema():
    first = normalize_result(
        "mail",
        mail_result(),
        {"sub_question": "YIELD팀 수율 이슈", "query": "수율 이슈"},
    )
    second = normalize_result(
        "mail",
        mail_result(),
        {"sub_question": "YIELD팀 수율 이슈", "query": "수율 이슈"},
    )

    assert first == second
    assert first["document_id"] == "mail:mail-1:0"
    assert first["source_type"] == "mail"
    assert first["content"] == "수율 defect 개선"
    assert first["team"] == "YIELD팀"
    assert first["week"] == "2026-28"
    assert first["metadata"]["sub_question"] == "YIELD팀 수율 이슈"


def test_deduplicate_documents_removes_same_content_and_limits_mail_chunks():
    documents = [
        normalize_result("mail", mail_result(part_index=0), {}),
        normalize_result("mail", mail_result(part_index=1), {}),
        normalize_result("mail", mail_result(part_index=2, text="다른 수율 내용"), {}),
        normalize_result(
            "wiki",
            {
                "score": 4.0,
                "text": "수율 defect 개선",
                "title": "중복 요약",
                "team": "YIELD팀",
                "week": "2026-28",
            },
            {},
        ),
    ]

    unique, removed_count = deduplicate_documents(documents)

    assert len(unique) == 2
    assert removed_count == 2
    assert [doc["document_id"] for doc in unique] == [
        "mail:mail-1:0",
        "mail:mail-1:2",
    ]


def test_rerank_boosts_exact_team_week_and_respects_limit():
    documents = []
    for index in range(FINAL_DOCUMENT_LIMIT + 3):
        team = "YIELD팀" if index == 9 else "PROCESS팀"
        week = "2026-28" if index == 9 else "2026-20"
        documents.append(
            {
                "document_id": f"mail:m-{index}:0",
                "source_type": "mail",
                "title": f"메일 {index}",
                "content": "수율 defect 개선" if index == 9 else "일반 업무 내용",
                "team": team,
                "week": week,
                "original_score": float(index + 1),
                "rerank_score": None,
                "metadata": {},
            }
        )

    ranked = rerank_documents(
        "YIELD팀 2026-28 수율 defect",
        documents,
        {"team": "YIELD팀", "weeks": ["2026-28"]},
    )

    assert len(ranked) == FINAL_DOCUMENT_LIMIT
    assert ranked[0]["document_id"] == "mail:m-9:0"
    assert ranked[0]["rerank_score"] >= ranked[-1]["rerank_score"]


def test_build_context_assigns_stable_citations_and_honors_token_limit():
    documents = [
        {
            "document_id": f"wiki:{index}",
            "source_type": "wiki",
            "title": f"요약 {index}",
            "content": "가" * 120,
            "team": "YIELD팀",
            "week": "2026-28",
            "original_score": 1.0,
            "rerank_score": 1.0 - index / 10,
            "metadata": {},
        }
        for index in range(3)
    ]

    context, selected = build_context(documents, token_limit=100)

    assert 0 < len(selected) < len(documents)
    assert selected[0]["citation_id"] == "S1"
    assert "[S1]" in context
    assert "[S3]" not in context


def test_build_context_bounds_korean_text_and_keeps_later_evidence():
    documents = [
        {
            "document_id": "wiki:huge",
            "source_type": "wiki",
            "title": "대용량 근거",
            "content": "한글근거" * 20_000,
            "team": "YIELD팀",
            "week": "2026-28",
            "metadata": {},
        },
        {
            "document_id": "wiki:small",
            "source_type": "wiki",
            "title": "후속 근거",
            "content": "중요한 후속 근거",
            "team": "YIELD팀",
            "week": "2026-28",
            "metadata": {},
        },
    ]

    context, selected = build_context(documents, token_limit=500)

    assert len(context) < 2_000
    assert [document["citation_id"] for document in selected] == ["S1", "S2"]
    assert "중요한 후속 근거" in context


def test_retrieval_grader_rejects_empty_results():
    grade = fallback_grade_retrieval(
        "YIELD팀 수율 이슈",
        [],
        {"team": "YIELD팀", "weeks": ["2026-28"]},
        [],
    )

    assert grade.relevant is False
    assert grade.sufficient is False
    assert grade.score == 0.0
    assert grade.missing_information


def test_retrieval_grader_requires_every_comparison_team():
    process_document = {
        "document_id": "mail:process:0",
        "source_type": "mail",
        "title": "PROCESS팀 이슈",
        "content": "PROCESS팀 수율 이슈와 개선",
        "team": "PROCESS팀",
        "week": "2026-28",
        "original_score": 5.0,
        "rerank_score": 1.0,
        "metadata": {},
    }

    grade = fallback_grade_retrieval(
        "PROCESS팀과 YIELD팀 수율 이슈를 비교해줘",
        [process_document],
        {"weeks": ["2026-28"]},
        ["PROCESS팀 수율 이슈", "YIELD팀 수율 이슈"],
    )

    assert grade.relevant is True
    assert grade.sufficient is False
    assert any("YIELD팀" in item for item in grade.missing_information)


def test_retrieval_grader_requires_numeric_evidence_for_count_question():
    document = {
        "document_id": "mail:count:0",
        "source_type": "mail",
        "title": "제출 현황",
        "content": "주간보고 제출 현황을 정리했습니다.",
        "team": "YIELD팀",
        "week": "2026-28",
        "original_score": 5.0,
        "rerank_score": 1.0,
        "metadata": {},
    }

    grade = fallback_grade_retrieval(
        "주간보고 제출 건수는 몇 건이야?",
        [document],
        {"weeks": ["2026-28"]},
        [],
    )

    assert grade.sufficient is False
    assert any("숫자" in item for item in grade.missing_information)


def test_retrieval_grader_flags_conflicting_versions_of_same_numeric_claim():
    documents = [
        {
            "document_id": "mail:1:0",
            "source_type": "mail",
            "title": "보고 A",
            "content": "YIELD팀 defect는 7건입니다.",
            "team": "YIELD팀",
            "week": "2026-28",
            "metadata": {},
        },
        {
            "document_id": "mail:2:0",
            "source_type": "mail",
            "title": "보고 B",
            "content": "YIELD팀 defect는 9건입니다.",
            "team": "YIELD팀",
            "week": "2026-28",
            "metadata": {},
        },
    ]

    grade = fallback_grade_retrieval(
        "YIELD팀 defect 건수는?",
        documents,
        {"team": "YIELD팀", "weeks": ["2026-28"]},
        [],
    )

    assert grade.sufficient is False
    assert any("상충" in item for item in grade.missing_information)


def test_retrieval_grader_rejects_single_incidental_term_overlap():
    document = {
        "document_id": "technical:unrelated",
        "source_type": "technical_document",
        "title": "일반 공정 안내",
        "content": "공정 일정과 교육 장소를 안내합니다.",
        "team": None,
        "week": None,
        "metadata": {},
    }

    grade = fallback_grade_retrieval(
        "ALD 공정 원인과 증착 원리를 설명해줘",
        [document],
        {},
        [],
    )

    assert grade.relevant is False
    assert grade.sufficient is False


def test_search_key_is_stable_for_filter_order():
    first = make_search_key(
        "수율 이슈", "mail", {"team": "YIELD팀", "weeks": ["2026-28", "2026-27"]}
    )
    second = make_search_key(
        " 수율   이슈 ", "mail", {"weeks": ["2026-27", "2026-28"], "team": "YIELD팀"}
    )

    assert first == second


def test_retry_is_bounded_and_rejects_an_executed_query():
    key = make_search_key("수율 이슈", "mail", {"team": "YIELD팀"})

    assert can_retry_search(0, 0, key, []) is True
    assert can_retry_search(MAX_SEARCH_ATTEMPTS, 0, key, []) is False
    assert can_retry_search(0, MAX_QUERY_REWRITES, key, []) is False
    assert can_retry_search(0, 0, key, [key]) is False


def test_query_rewrite_targets_missing_information_and_stops_if_unchanged():
    rewritten = build_rewritten_query(
        "PROCESS팀과 YIELD팀 비교",
        "PROCESS팀 수율 이슈",
        ["YIELD팀 관련 근거 부족", "2026-28 주차 근거 부족"],
    )

    assert rewritten == "PROCESS팀 수율 이슈 YIELD팀 2026-28"
    assert (
        build_rewritten_query(
            "YIELD팀 수율 이슈",
            rewritten,
            ["YIELD팀 관련 근거 부족", "2026-28 주차 근거 부족"],
        )
        is None
    )


def test_planner_selects_statistics_for_submission_counts():
    plan = fallback_retrieval_plan(
        "이번 주 팀별 보고서 제출 현황을 알려줘",
        {"weeks": ["2026-28"]},
    )

    assert plan.intent == "statistics"
    assert plan.sources == ["statistics"]


def test_planner_selects_mail_and_wiki_for_single_team_issue():
    plan = fallback_retrieval_plan(
        "YIELD팀 이번 주 주요 이슈를 알려줘",
        {"team": "YIELD팀", "weeks": ["2026-28"]},
    )

    assert plan.intent == "single_search"
    assert plan.sources == ["mail", "wiki"]
    assert plan.sub_questions == ["YIELD팀 이번 주 주요 이슈를 알려줘"]
    assert plan.search_strategy == "hybrid"


def test_planner_selects_technical_document_for_concept_question():
    plan = fallback_retrieval_plan("ALD 공정 원리가 뭐야", {})

    assert plan.intent == "single_search"
    assert plan.sources == ["technical_document"]
    assert plan.vector_weight > plan.keyword_weight


def test_planner_decomposes_team_comparison_with_bounded_sub_questions():
    plan = fallback_retrieval_plan(
        "PROCESS팀과 YIELD팀의 최근 4주 수율 이슈를 비교해줘",
        {"weeks": ["2026-25", "2026-26", "2026-27", "2026-28"]},
    )

    assert plan.intent == "comparison"
    assert plan.sources == ["mail", "wiki"]
    assert plan.sub_questions == [
        "PROCESS팀 수율 이슈",
        "YIELD팀 수율 이슈",
        "수율 이슈 주차별 변화 2026-25 2026-26 2026-27 2026-28",
        "수율 이슈 공통 원인 후보",
        "PROCESS팀 YIELD팀 수율 이슈 비교",
    ]
    assert len(plan.sub_questions) <= MAX_SUB_QUESTIONS


def test_planner_decomposes_week_trend_and_increases_keyword_weight_for_code():
    plan = fallback_retrieval_plan(
        "TEL-ABC123 최근 추이를 알려줘",
        {"weeks": ["2026-26", "2026-27", "2026-28"]},
    )

    assert plan.intent == "trend"
    assert plan.sub_questions == [
        "TEL-ABC123 2026-26",
        "TEL-ABC123 2026-27",
        "TEL-ABC123 2026-28",
    ]
    assert plan.keyword_weight > plan.vector_weight


def test_build_search_tasks_is_bounded_unique_and_preserves_metadata():
    plan = fallback_retrieval_plan(
        "PROCESS팀과 YIELD팀 수율 이슈를 비교해줘",
        {"weeks": ["2026-27", "2026-28"]},
    )

    tasks = build_search_tasks(plan)

    assert len(tasks) == 4
    assert len({task.search_key for task in tasks}) == len(tasks)
    assert {task.source for task in tasks} == {"mail", "wiki"}
    assert {task.team for task in tasks} == {"PROCESS팀", "YIELD팀"}
    assert all(task.weeks == ["2026-27", "2026-28"] for task in tasks)


def test_explicit_api_team_filter_cannot_be_overridden_by_question_team():
    plan = fallback_retrieval_plan(
        "PROCESS팀 수율 이슈를 알려줘",
        {"team": "YIELD팀", "weeks": ["2026-28"]},
    )

    tasks = build_search_tasks(plan)

    assert tasks
    assert all(task.team == "YIELD팀" for task in tasks)


def test_every_complex_comparison_search_task_stays_within_requested_teams():
    plan = fallback_retrieval_plan(
        "PROCESS팀과 YIELD팀의 최근 4주 수율 이슈를 비교하고 공통 원인을 알려줘",
        {"weeks": ["2026-25", "2026-26", "2026-27", "2026-28"]},
    )

    tasks = build_search_tasks(plan)

    assert tasks
    assert {task.team for task in tasks} <= {"PROCESS팀", "YIELD팀"}
    assert None not in {task.team for task in tasks}


def test_allowed_teams_rejects_team_injected_by_llm_sub_question():
    plan = RetrievalPlan(
        intent="comparison",
        sources=["mail"],
        sub_questions=["QA팀 이슈"],
        filters={"allowed_teams": ["PROCESS팀", "YIELD팀"], "weeks": ["2026-28"]},
        search_strategy="hybrid",
        vector_weight=0.7,
        keyword_weight=0.3,
    )

    tasks = build_search_tasks(plan)

    assert {task.team for task in tasks} == {"PROCESS팀", "YIELD팀"}


def cited_documents():
    return [
        {
            "document_id": "mail:1:0",
            "source_type": "mail",
            "title": "YIELD팀 주간메일",
            "content": "YIELD팀 defect 7건을 확인하고 개선했습니다.",
            "team": "YIELD팀",
            "week": "2026-28",
            "original_score": 5.0,
            "rerank_score": 1.0,
            "citation_id": "S1",
            "metadata": {"mail_id": "1"},
        },
        {
            "document_id": "wiki:2",
            "source_type": "wiki",
            "title": "수율 요약",
            "content": "수율 개선 활동이 진행 중입니다.",
            "team": "YIELD팀",
            "week": "2026-28",
            "original_score": 4.0,
            "rerank_score": 0.8,
            "citation_id": "S2",
            "metadata": {},
        },
    ]


def test_validate_citations_rejects_missing_and_unknown_source_ids():
    documents = cited_documents()

    assert validate_citations("defect 7건입니다 [S1]", documents) == (True, [])
    assert validate_citations("defect가 있습니다", documents)[0] is False
    valid, invalid = validate_citations("defect 7건입니다 [S9]", documents)
    assert valid is False
    assert invalid == ["S9"]


def test_answer_evaluation_rejects_unsupported_number_and_missing_team_answer():
    evaluation = fallback_evaluate_answer(
        "PROCESS팀과 YIELD팀 defect 건수를 비교해줘",
        "YIELD팀 defect는 99건입니다 [S1]",
        cited_documents(),
    )

    assert evaluation.passed is False
    assert any("99" in claim for claim in evaluation.unsupported_claims)
    assert any("PROCESS팀" in item for item in evaluation.missing_answers)
    assert evaluation.citation_valid is True


def test_answer_evaluation_passes_grounded_complete_cited_answer():
    evaluation = fallback_evaluate_answer(
        "YIELD팀 defect 건수는?",
        "YIELD팀 defect는 7건입니다 [S1]",
        cited_documents(),
    )

    assert evaluation.passed is True
    assert evaluation.groundedness_score >= 0.8
    assert evaluation.completeness_score == 1.0


def test_answer_evaluation_requires_explicit_week_condition_in_answer():
    evaluation = fallback_evaluate_answer(
        "YIELD팀 defect 건수는?",
        "YIELD팀 defect는 7건입니다 [S1]",
        cited_documents(),
        {"team": "YIELD팀", "weeks": ["2026-28"]},
    )

    assert evaluation.passed is False
    assert any("2026-28" in item for item in evaluation.missing_answers)


def test_answer_evaluation_rejects_claim_bound_to_wrong_valid_source():
    documents = [
        {
            "document_id": "mail:process:0",
            "source_type": "mail",
            "title": "PROCESS팀 메일",
            "content": "PROCESS팀 defect는 7건입니다.",
            "team": "PROCESS팀",
            "week": "2026-28",
            "citation_id": "S1",
            "metadata": {"mail_id": "process"},
        },
        {
            "document_id": "mail:yield:0",
            "source_type": "mail",
            "title": "YIELD팀 메일",
            "content": "YIELD팀 defect는 9건입니다.",
            "team": "YIELD팀",
            "week": "2026-28",
            "citation_id": "S2",
            "metadata": {"mail_id": "yield"},
        },
    ]

    evaluation = fallback_evaluate_answer(
        "YIELD팀 defect 건수는?",
        "YIELD팀 defect는 7건입니다 [S1]",
        documents,
    )

    assert evaluation.passed is False
    assert any("S1" in claim for claim in evaluation.unsupported_claims)


def test_answer_evaluation_rejects_uncited_factual_claim_and_unrelated_citation():
    documents = cited_documents()

    uncited = fallback_evaluate_answer(
        "YIELD팀 defect 건수는?",
        "YIELD팀 defect는 7건입니다. 개선 활동입니다 [S2]",
        documents,
    )
    unrelated = fallback_evaluate_answer(
        "YIELD팀 defect 원인은?",
        "외계 방사선이 원인입니다 [S1]",
        documents,
    )

    assert uncited.passed is False
    assert any("citation 누락" in claim for claim in uncited.unsupported_claims)
    assert unrelated.passed is False
    assert any("내용 불일치" in claim for claim in unrelated.unsupported_claims)


def test_answer_evaluation_rejects_plain_uncited_claim_and_embellished_citation():
    documents = cited_documents()

    uncited = fallback_evaluate_answer(
        "YIELD팀 수율은?",
        "수율이 크게 악화되었습니다. YIELD팀 defect는 7건입니다 [S1]",
        documents,
    )
    embellished = fallback_evaluate_answer(
        "YIELD팀 defect 원인은?",
        "YIELD팀 defect는 외계 방사선 때문에 7건 발생했습니다 [S1]",
        documents,
    )

    assert uncited.passed is False
    assert any("citation 누락" in claim for claim in uncited.unsupported_claims)
    assert embellished.passed is False
    assert any("내용 불일치" in claim for claim in embellished.unsupported_claims)

    one_term = fallback_evaluate_answer(
        "YIELD팀 defect 건수는?",
        "악화되었습니다. YIELD팀 defect는 7건입니다 [S1]",
        documents,
    )
    assert one_term.passed is False
    assert any("citation 누락" in claim for claim in one_term.unsupported_claims)


def test_wiki_auto_save_is_disabled_by_default_and_revision_is_bounded():
    assert AUTO_WIKI_SAVE_ENABLED is False
    assert MAX_ANSWER_REVISIONS == 1
    assert (
        wiki_save_eligible(
            {
                "route": "search",
                "answer": "YIELD팀 defect는 7건입니다 [S1] [S2]",
                "reranked_results": cited_documents(),
                "groundedness_score": 1.0,
                "citation_valid": True,
                "answer_evaluation": {"passed": True},
                "limited_answer": False,
            }
        )
        is False
    )


def test_wiki_candidate_requires_completed_duplicate_check(monkeypatch):
    monkeypatch.setattr("hybrid_rag.AUTO_WIKI_SAVE_ENABLED", True)
    base_state = {
        "route": "search",
        "answer": "YIELD팀 defect는 7건입니다 [S1] [S2]",
        "reranked_results": cited_documents(),
        "groundedness_score": 1.0,
        "citation_valid": True,
        "answer_evaluation": {"passed": True},
        "limited_answer": False,
    }

    assert wiki_save_eligible(base_state) is False
    assert wiki_save_eligible({**base_state, "wiki_duplicate": True}) is False
    assert wiki_save_eligible({**base_state, "wiki_duplicate": False}) is True
