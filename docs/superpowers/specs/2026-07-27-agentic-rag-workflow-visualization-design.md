# Agentic RAG Workflow Visualization Design

## Goal

Create one interactive HTML visualization that explains both the implemented
Hybrid Agentic RAG graph and a concrete question moving through that graph.
The visual must make planning, decomposition, parallel retrieval, grading,
bounded retry, answer generation, citation validation, one revision, and final
termination understandable without reading source code.

## Audience and success criteria

The primary audience is a developer or evaluator testing the chatbot. The
visual succeeds when the viewer can:

1. distinguish general, statistics, and search routes;
2. follow the bounded search and answer-revision loops;
3. see how one comparison question becomes source-specific search tasks;
4. inspect the important GraphState changes at every step; and
5. understand why the final answer is accepted or restricted.

## Chosen composition

Use a single step-through explainer rather than a static poster or separate
tabs. The surface contains:

- a compact workflow graph showing all nodes and conditional edges;
- previous/next controls and direct step buttons;
- one current-step detail area containing input state, decision, output state,
  and the active transition;
- a parallel-search lane only during the retrieval execution step; and
- a compact legend for normal flow, conditional decisions, retry flow, and
  validation flow.

The current node and traversed edges are emphasized. Inactive graph structure
remains visible so the viewer never loses context.

## Example question

Use this illustrative comparison question:

> 최근 4주간 PROCESS팀과 YIELD팀의 수율 이슈를 비교하고 공통 원인을 알려줘.

The example uses weeks `2026-25` through `2026-28`. It demonstrates the most
important agentic behavior while clearly labeling the retrieved values as an
illustrative execution trace rather than live OpenSearch output.

## Step sequence

1. **Router** — classify as `search`; retain the explicit four-week condition.
2. **Plan retrieval** — choose `mail` and `wiki`, hybrid weights, allowed teams,
   and comparison intent.
3. **Decompose** — create team-specific issue searches, a weekly-change task,
   a common-cause task, and comparison synthesis intent.
4. **Execute searches** — show bounded parallel mail/wiki tasks with preserved
   team and week filters.
5. **Deduplicate and rerank** — normalize results, remove duplicate chunks,
   apply source/lexical/team/week scoring, select documents, and assign stable
   `[S#]` citations.
6. **Grade retrieval** — first pass is insufficient because one requested week
   lacks YIELD evidence.
7. **Rewrite query** — target only the missing `YIELD팀 2026-27` evidence and
   increment the bounded retry counters.
8. **Merge and re-grade** — merge new evidence with prior evidence, rerank, and
   pass relevance and coverage checks.
9. **Generate answer** — answer only from the selected context with citations.
10. **Evaluate answer** — detect one unsupported numeric claim attached to an
    otherwise valid citation.
11. **Revise once** — remove the unsupported number and retain supported team,
    week, issue, and common-cause statements.
12. **Finalize** — citation validity, groundedness, and completeness pass; show
    the final answer and terminal counters.

## State shown per step

Show only fields that explain the transition:

- `route`, `team`, `week`;
- `intent`, `selected_sources`, `sub_questions`;
- `search_query`, `rewritten_query`;
- `search_attempts`, `rewrite_count`, `answer_revision_count`;
- result counts, duplicate count, selected citation IDs;
- `retrieval_grade`, `missing_information`;
- `groundedness_score`, `completeness_score`, `citation_valid`; and
- `unsupported_claims`, `missing_answers`, `final_answer`.

Do not display document bodies beyond short illustrative evidence summaries.

## Interaction and accessibility

- Native buttons provide previous, next, and direct step selection.
- The active step uses `aria-pressed` and a visible label, not color alone.
- Keyboard navigation follows native document order.
- The layout is wide and shallow at desktop width and stacks below 620px.
- The primary interaction updates the graph highlight and current-step detail
  without navigation or network requests.

## Visual language

Use theme variables only. Normal execution uses the primary series, retry edges
use a second series, and answer validation uses a third series. Nodes use subtle
fills with neutral structural edges. The visualization has no nested cards,
fixed viewport sizing, horizontal page overflow, or external data calls.

## Verification

Verify the fragment by rendering it as standalone HTML and checking:

- every step button changes graph and detail content;
- previous/next boundaries work;
- no JavaScript identifier or queried element is missing;
- the desktop and narrow layouts do not overlap or clip; and
- graph labels, counters, citations, and final answer match the implemented
  bounded workflow.
