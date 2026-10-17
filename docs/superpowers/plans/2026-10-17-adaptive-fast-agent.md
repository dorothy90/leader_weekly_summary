# Adaptive fast RAG plan

User approved: parallel query expansion, relevant passage selection, time-budgeted loop, real stored Kim-test mail continuous test.

Design: expand up to two queries in existing interpretation; base hybrid plus two keyword lanes concurrently; weighted parent RRF. Retain explicit constraints/ACL. LangGraph prebuilt loop up to three batches of two searches. Model selects identified passages; final input rebuilt from selected passages and execution facts. Stop on no new passage, duplicate query, round cap or remaining time; reserve six seconds within existing20sec deadline. No added model call for initial expansion. Detailed mode unchanged.

Validation: failing tests first for parallel lanes/filter preservation, loop continuation/no-progress, passage exclusion and budget exit. Run regression, then same-conversation existing stored-mail investigation/follow-ups, inspect quality and timings. Store transparent report including corpus limits; do not seed data.

Reviewed fixes: preserve new matching chunks from existing parents; bound restoration; cap initial lanes4sec retaining successful results; select passages alongside search to avoid extra calls on no progress; prevent inferred body-name changes. Final live report is saved in the task outputs directory.
