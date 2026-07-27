# Agentic RAG Workflow Visualization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an interactive HTML step-through that shows the implemented Hybrid Agentic RAG graph and one comparison question moving through planning, bounded retrieval, retry, answer validation, revision, and finalization.

**Architecture:** Store the editable HTML fragment in the thread-scoped Codex visualization directory. The fragment contains one inline SVG graph, a serializable `steps` dataset, and local JavaScript that updates node/edge emphasis and one current-step detail area. Render the fragment into a standalone document under `docs/` and verify both interaction behavior and responsive layout without network calls.

**Tech Stack:** HTML fragment, theme-aware CSS, inline SVG, vanilla JavaScript, Codex visualization render helper.

## Global Constraints

- Use the approved example question about PROCESS팀 and YIELD팀 across weeks `2026-25` through `2026-28`.
- Show the implemented bounds: `MAX_SEARCH_ATTEMPTS=2`, `MAX_QUERY_REWRITES=2`, and `MAX_ANSWER_REVISIONS=1`.
- Label all retrieved values as an illustrative trace rather than live OpenSearch output.
- Use only theme variables for colors and native buttons for interaction.
- Do not use `fetch`, XHR, WebSocket, fixed viewport sizing, or horizontal page overflow.
- Keep the fragment under 2 MB and usable from 320px width upward.
- Show only short evidence summaries, never full document bodies.

---

### Task 1: Interactive workflow fragment

**Files:**
- Create: `/Users/daehwankim/.codex/visualizations/2026/07/27/019fa36c-b170-7aa3-a494-c9ce7063ca80/agentic-rag-workflow.html`

**Interfaces:**
- Consumes: the node names and loop bounds implemented in `rag_api_opensearch_v3.py` and `hybrid_rag.py`.
- Produces: a standalone HTML fragment rooted at `#agentic-rag-workflow`, with `steps: Step[]` and `renderStep(index: number): void`.

- [ ] **Step 1: Create the fragment shell and workflow graph**

Create a literal HTML fragment with this structural interface:

```html
<section id="agentic-rag-workflow" aria-label="Hybrid Agentic RAG 실행 흐름">
  <div class="viz-controls" aria-label="실행 단계 선택"></div>
  <svg role="img" aria-labelledby="rag-flow-title rag-flow-desc"></svg>
  <div id="rag-step-detail" class="card" aria-live="polite"></div>
  <div class="viz-row" aria-label="단계 이동">
    <button type="button" class="btn" id="rag-prev">이전</button>
    <button type="button" class="btn btn-primary" id="rag-next">다음</button>
  </div>
</section>
```

The SVG must contain labeled nodes for `START`, `router`, `general answer`, `statistics`, `plan retrieval`, `execute searches`, `grade retrieval`, `rewrite query`, `limited answer`, `generate answer`, `evaluate answer`, `revise answer`, `finalize`, and `END`. Give every node `data-node="<stable-id>"` and every transition `data-edge="<from>-<to>"` so JavaScript can apply active state without querying label text.

- [ ] **Step 2: Add the complete illustrative execution dataset**

Define exactly twelve ordered step objects:

```javascript
const steps = [
  { id: "route", node: "router", edge: "start-router", title: "1. 질문 분석 및 라우팅", decision: "search", counters: { search: 0, rewrite: 0, revision: 0 } },
  { id: "plan", node: "plan", edge: "router-plan", title: "2. 검색 계획", decision: "comparison · mail + wiki", counters: { search: 0, rewrite: 0, revision: 0 } },
  { id: "decompose", node: "plan", edge: "router-plan", title: "3. 복합 질문 분해", decision: "5 sub-questions", counters: { search: 0, rewrite: 0, revision: 0 } },
  { id: "search-1", node: "execute", edge: "plan-execute", title: "4. 병렬 검색", decision: "10 bounded source tasks", counters: { search: 1, rewrite: 0, revision: 0 } },
  { id: "rerank-1", node: "execute", edge: "plan-execute", title: "5. 중복 제거 및 리랭킹", decision: "14 → 8 documents · S1…S8", counters: { search: 1, rewrite: 0, revision: 0 } },
  { id: "grade-1", node: "grade", edge: "execute-grade", title: "6. 검색 충분성 평가", decision: "insufficient · YIELD팀 2026-27 근거 부족", counters: { search: 1, rewrite: 0, revision: 0 } },
  { id: "rewrite", node: "rewrite", edge: "grade-rewrite", title: "7. 부족 정보 중심 재작성", decision: "YIELD팀 2026-27 수율 이슈 원인 개선", counters: { search: 1, rewrite: 1, revision: 0 } },
  { id: "search-2", node: "execute", edge: "rewrite-execute", title: "8. 추가 검색·병합·재평가", decision: "sufficient · prior + new evidence", counters: { search: 2, rewrite: 1, revision: 0 } },
  { id: "answer", node: "answer", edge: "grade-answer", title: "9. 근거 기반 초안 생성", decision: "S1, S3, S6 citations", counters: { search: 2, rewrite: 1, revision: 0 } },
  { id: "evaluate", node: "evaluate", edge: "answer-evaluate", title: "10. 답변 검증", decision: "fail · unsupported number", counters: { search: 2, rewrite: 1, revision: 0 } },
  { id: "revise", node: "revise", edge: "evaluate-revise", title: "11. 답변 1회 수정", decision: "remove unsupported 12.4%", counters: { search: 2, rewrite: 1, revision: 1 } },
  { id: "finalize", node: "finalize", edge: "evaluate-finalize", title: "12. 검증 통과 및 종료", decision: "grounded · complete · citations valid", counters: { search: 2, rewrite: 1, revision: 1 } }
];
```

Extend each object with Korean `input`, `action`, `output`, `state`, and optional `tasks` or `evidence` arrays exactly matching the approved design. The final step must show a concise answer with `[S1]`, `[S3]`, and `[S6]` citations and scores `groundedness=0.94`, `completeness=1.00`, `citation_valid=true`, clearly marked as illustrative.

- [ ] **Step 3: Implement deterministic interaction**

Implement this public behavior:

```javascript
let currentStep = 0;

function renderStep(index) {
  currentStep = Math.max(0, Math.min(steps.length - 1, index));
  const step = steps[currentStep];
  // Update aria-pressed on direct step buttons.
  // Apply .is-active to step.node and step.edge.
  // Apply .is-traversed to every prior step node/edge.
  // Replace #rag-step-detail with current input/action/output/state.
  // Disable previous at index 0 and next at the final index.
}
```

Bind direct step buttons and the `rag-prev`/`rag-next` native buttons. The detail renderer must use DOM creation or escaped static strings only; no user input is accepted.

- [ ] **Step 4: Add responsive, theme-aware styling**

Use `var(--background)`, `var(--foreground)`, `var(--card)`, `var(--card-foreground)`, `var(--muted)`, `var(--muted-foreground)`, `var(--border)`, `var(--primary)`, `var(--primary-foreground)`, and `var(--viz-series-1)` through `--viz-series-3`. At widths below `620px`, stack detail columns and hide only nonessential SVG annotations; keep node labels, step controls, and current counters visible.

- [ ] **Step 5: Perform static fragment checks**

Run:

```bash
test -s /Users/daehwankim/.codex/visualizations/2026/07/27/019fa36c-b170-7aa3-a494-c9ce7063ca80/agentic-rag-workflow.html
rg -n 'id="agentic-rag-workflow"|function renderStep|const steps =|rag-prev|rag-next|data-node|data-edge' /Users/daehwankim/.codex/visualizations/2026/07/27/019fa36c-b170-7aa3-a494-c9ce7063ca80/agentic-rag-workflow.html
```

Expected: the file is nonempty and every required root, dataset, interaction function, control, node hook, and edge hook is found.

### Task 2: Standalone rendering and visual verification

**Files:**
- Create: `docs/agentic-rag-workflow.html`
- Verify: `/Users/daehwankim/.codex/visualizations/2026/07/27/019fa36c-b170-7aa3-a494-c9ce7063ca80/agentic-rag-workflow.html`

**Interfaces:**
- Consumes: the fragment and `renderStep(index)` from Task 1.
- Produces: a standalone browser-openable document with identical behavior.

- [ ] **Step 1: Render the standalone document**

Run:

```bash
python3 /Users/daehwankim/.codex/plugins/cache/openai-bundled/visualize/1.0.12/skills/visualize/scripts/render.py \
  /Users/daehwankim/.codex/visualizations/2026/07/27/019fa36c-b170-7aa3-a494-c9ce7063ca80/agentic-rag-workflow.html \
  /Users/daehwankim/Documents/weekly_mail_agent/docs/agentic-rag-workflow.html
```

Expected: exit code 0 and `docs/agentic-rag-workflow.html` is nonempty.

- [ ] **Step 2: Verify JavaScript structure and content bounds**

Run:

```bash
test "$(wc -c < docs/agentic-rag-workflow.html)" -lt 2097152
rg -n 'illustrative|예시 실행|MAX_SEARCH_ATTEMPTS|2026-27|unsupported|citation_valid' docs/agentic-rag-workflow.html
```

Expected: the standalone file is below 2 MB and contains the example label, retry bound, missing-week rewrite, unsupported-claim validation, and final citation state.

- [ ] **Step 3: Serve and visually inspect desktop and narrow layouts**

Run the render helper with `--serve`, open the provided local URL, and verify:

```text
Desktop: graph labels and edges do not overlap; the active node and current detail agree.
Narrow: controls wrap, detail columns stack, and no horizontal page scrollbar appears.
Interaction: direct step 7, previous, next, and final-step buttons update the graph and counters.
```

- [ ] **Step 4: Run final project regression checks**

Run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests
```

Expected: the existing `111` tests or more pass with zero failures; the visualization introduces no application-code changes.
