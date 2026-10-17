# Fast investigative Agent implementation plan

Approved scope: speed-first only; preserve four indices, ACL, explicit filters and detailed mode.
Architecture: initial validated plan/search -> LangGraph 1.0.1 prebuilt create_react_agent -> optional one batch of two searches -> structured cited answer. Existing SDK retained; fast Agent model is configurable separately. No extra grading call. Maximum three LLM calls including interpretation, existing 20-second deadline.
References: Onyx chat/llm_loop.py and search/search_tool.py (2026-10-17); no source copied. Installed LangGraph prebuilt is used rather than adding a second framework. Its API is deprecated in favor of LangChain create_agent in newer stacks; pin/migrate deliberately, do not mix unsupported dependency versions.

- [x] Regression test for actual ToolNode execution, locked sender and bounded calls.
- [x] Request-local runner and source-qualified stable evidence IDs.
- [x] Owner-bound search history, last answer and unresolved questions for follow-ups.
- [x] Read retrieved parent chunks with original ACL, bounded size and time.
- [x] Test duplicate searches, ACL expansion, SDK roundtrip and full regression suite.
- [x] Run existing Kim-test stored mail investigation, follow-ups and repeated prompt; final fresh conversation measured 10.18s / 8.61s / 10.97s. Existing alias resolves to a dummy-named index, so this is real integration against stored test documents, not production-mail verification. No new documents were seeded.
