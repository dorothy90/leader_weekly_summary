# Search-first routing with safe general fallback

## Goal

Auto mode should search the user's mail index for ordinary information requests before answering from general model knowledge. Conversational turns must remain fast and search-free. When mail search returns no evidence, the API may provide a clearly labelled, safety-limited general answer instead of only returning `NO_EVIDENCE`.

## Routing policy

1. Keep deterministic system intents (`diagnostic`, `corpus_info`) unchanged.
2. Keep explicit `response_mode=fast|deep` authoritative.
3. Route greetings, gratitude, personal/name statements, identity questions, conversational small talk, and product-usage questions to `general` without retrieval.
4. Keep the existing deterministic Deep constraints for multi-period, multi-team, report, presentation, trend, and root-cause work.
5. In Auto mode, treat every other factual or information-seeking request as `fast`, including previously unseen subjects such as `장례식장 정보 알려줘`.
6. The deterministic search-first classification overrides a model-produced `general` or `clarify` decision for a non-conversational knowledge request. This prevents a brittle or overly conservative Router model from bypassing retrieval.
7. If the Router call fails, the same policy applies: known conversation goes to General; otherwise the request goes to Fast rather than defaulting to General.

## No-evidence behavior

The safe fallback applies only when all of the following are true:

- the request used `response_mode=auto`;
- the selected route was `fast`;
- Fast retrieval completed and returned `NO_EVIDENCE`;
- there was no dependency, timeout, ACL, or citation-validation failure.

The API then performs one general-generation call with a dedicated no-evidence prompt. That prompt must:

- state that no relevant mail evidence was found;
- answer only with stable, general guidance;
- never invent mail contents, venue names, addresses, prices, availability, recent events, or user-specific facts;
- ask for missing scope or explain the limitation when the request requires current, local, or private data;
- retain multi-turn conversation context.

Explicit Fast mode remains grounded-only: `response_mode=fast` continues to return the existing limited `NO_EVIDENCE` result and never falls back to general knowledge.

## Response and diagnostics

- Preserve the Router decision as `route=fast` and the executed retrieval system as `fast_rag`.
- Preserve the real search count and `evidence_count=0`.
- Keep the result visibly limited because it is not mail-grounded.
- Add a disclosure explaining that mail search returned no evidence and a general fallback was used.
- Include the fallback general-generation node in node diagnostics.
- Allow the safe fallback answer into conversation history so later turns can refer to it, while retaining the disclosure and lack of evidence.

## Examples

| Input | Route | Result |
|---|---|---|
| `안녕` | General | No search |
| `내 이름은 대환` | General | No search; saved for recall |
| `무슨 일을 할 수 있어?` | General | Product explanation, no search |
| `장례식장 정보 알려줘` | Fast | Search mail; if empty, labelled general guidance or a scope question |
| `서울 장례식장 최신 목록 알려줘` | Fast | Search mail; if empty, do not invent venues and explain that current/local data is unavailable |
| `지난 8주 수율 이슈를 팀별로 분석해줘` | Deep | Existing Deep workflow |
| explicit Fast + no evidence | Fast | Existing grounded-only `NO_EVIDENCE` result |

## Testing

Tests must be added before production changes and demonstrate:

1. Router failure sends an unseen knowledge request to Fast.
2. A valid model General/Clarify decision cannot bypass Fast for the same request.
3. Greetings, name memory, identity, small talk, and product-use questions still use General.
4. Auto Fast with no evidence performs the safe fallback, discloses it, preserves zero evidence/search diagnostics, and stores the final answer in history.
5. Explicit Fast with no evidence does not perform the fallback.
6. Dependency errors, timeouts, ACL failures, and invalid citations never trigger the fallback.
7. Current/local questions do not produce fabricated specific facts in deterministic fallback tests.
8. Existing Fast, Deep, ACL, multi-turn, API-contract, frontend, and build suites remain green.

## Out of scope

- Adding web search or a public funeral-home database.
- Changing OpenSearch index mappings or embedding models.
- Changing the `user_id` ACL policy.
- Merging Fast and Deep into one adaptive workflow.
