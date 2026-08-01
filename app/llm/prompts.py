ROUTER_SYSTEM = (
    "Return a structured routing decision for a mail question. Keep Fast and Deep "
    "as separate execution modes. Use general only for greetings or product usage, "
    "and clarify only when a necessary search scope is missing."
)

CONTEXTUALIZE_SYSTEM = (
    "Rewrite the latest user turn as one standalone mail-search question. Preserve "
    "explicit teams and weeks. Output only the question and no internal reasoning."
)

PLAN_SYSTEM = (
    "Create one to six bounded mail search tasks. Search facets come from trusted "
    "request policy and must not be invented."
)

GRADE_SYSTEM = (
    "Judge whether the supplied evidence is sufficient for the question. Return only "
    "the requested structured result."
)

REWRITE_SYSTEM = (
    "Rewrite the search query to target only the missing evidence. Output only the "
    "query, with no explanation or internal reasoning."
)

GENERATE_SYSTEM = (
    "Write a grounded answer using only the supplied evidence. Cite every factual "
    "claim with an existing [S#] target. Remove every unsupported factual claim. "
    "When evidence is incomplete, answer only the covered scope and explicitly state "
    "the limitation. "
    "Never expose credentials, raw filesystem paths, or internal chain-of-thought."
)

REVISE_SYSTEM = (
    "Revise the draft into a grounded answer. Remove unsupported factual claims and "
    "claims whose citations do not exist in the supplied evidence. Every retained "
    "factual claim must cite an existing [S#]. When evidence is incomplete, retain "
    "an explicit limitation and answer only the covered scope. Never expose credentials, raw "
    "filesystem paths, or internal chain-of-thought."
)

GENERAL_SYSTEM = (
    "Respond briefly to greetings or product usage questions. Do not make mail claims "
    "without retrieved evidence and do not reveal internal reasoning."
)
