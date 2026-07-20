# Content-First Roll-up Wiki Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task.

**Goal:** Turn the current graph-oriented workspace into a Medium-like wiki where Domain, Tech, LOTCD, Topic, Team, and Week selections each open one synthesized document, with hierarchical roll-ups and source-mail citations.

**Architecture:** Keep JSON as the approved source of truth and derive deterministic read-time projections. Category projections use one shared selector: Domain includes every descendant, Tech includes direct Tech items plus descendant LOTCD items, and LOTCD includes exact items. The React client renders those projections as a single article and exposes source evidence through a sanitized, authenticated HTML endpoint that opens in a new tab.

**Tech Stack:** FastAPI, Pydantic v2, JSON stores, pytest, React 19, TypeScript, Vite, Vitest, Testing Library.

---

## Assumptions and success criteria

- Existing classification and topic JSON schemas remain the system of record; this change does not reclassify agendas.
- `z-ai/glm-4.7-flash` remains the configured generation model. Wiki reads do not invoke the model.
- Direct evidence and descendant evidence are both included in parent documents and are visibly distinguishable.
- The Library displays exactly one mode at a time: Topic, LOTCD, Team, or Week.
- LOTCD mode displays a `Domain → Tech → LOTCD` tree; every node is navigable and opens one synthesized document.
- Every article has inline citation affordances and a bottom reference list. “원본 메일 보기” opens a sanitized `body.html` view in a new tab and focuses the cited quote when possible.
- Existing Graph mode remains available; selecting a graph node opens its document preview.
- Verification is intentionally focused: projection/API tests, affected frontend tests, production build, and one browser smoke test.

## Task 1: Add generic category roll-up projections

**Files:**
- Modify: `knowledge_models.py`
- Modify: `wiki_projections.py`
- Test: `tests/test_wiki_projections.py`

1. Add failing tests for Domain, Tech, and LOTCD projections. Use topics that are direct to a parent and topics assigned to descendant paths. Assert included topic IDs, scope level, breadcrumb, direct/rolled-up counts, sections, and evidence provenance.
2. Add a generic category projection model with `scope_level`, nullable `tech`/`lotcd`, breadcrumb, synthesized summary/sections, topic IDs, and reference groups. Preserve the existing leaf response fields where practical to avoid unnecessary client breakage.
3. Implement a shared path matcher:
   - Domain: target path domain matches.
   - Tech: domain and tech match.
   - LOTCD: domain, tech, and lotcd match.
   Mark an item direct only when its classification target ends at the selected level; otherwise mark it rolled up.
4. Keep ordering deterministic by state priority, last-updated week, then topic ID. Deduplicate evidence by agenda ID.
5. Run `PYTHONPATH=. pytest -q tests/test_wiki_projections.py`.
6. Commit only Task 1 files.

## Task 2: Expose Domain, Tech, and LOTCD API routes

**Files:**
- Modify: `knowledge_api.py`
- Test: `tests/test_knowledge_api.py`

1. Add failing authenticated API tests for:
   - `GET /api/knowledge/wiki/lotcd/{domain}`
   - `GET /api/knowledge/wiki/lotcd/{domain}/{tech}`
   - `GET /api/knowledge/wiki/lotcd/{domain}/{tech}/{lotcd}`
   - invalid or unknown scopes returning 404/422 without path leakage.
2. Route all three endpoints through the shared category projection builder. Define the fixed-depth routes explicitly so the leaf route remains unambiguous.
3. Run `PYTHONPATH=. pytest -q tests/test_knowledge_api.py tests/test_wiki_projections.py`.
4. Commit only Task 2 files.

## Task 3: Add safe original-mail reference viewing

**Files:**
- Create: `source_mail.py`
- Modify: `knowledge_api.py`
- Modify: `wiki_projections.py`
- Modify: `knowledge_models.py`
- Test: `tests/test_source_mail.py`
- Test: `tests/test_knowledge_api.py`
- Modify: `scripts/seed_demo_wiki.py`

1. Add failing tests for safe source resolution, traversal rejection, sanitization of scripts/forms/iframes/event handlers/remote resources, quote focus markup, fallback banner, CSP headers, missing files, and unknown agenda IDs.
2. Resolve evidence by agenda ID from approved archived evidence, then resolve `body.html` relative to its stored `source_path`. Enforce that the resolved file stays within the configured mail-data root; never accept a filesystem path from the request.
3. Sanitize mail HTML with a small allowlist parser. Preserve readable mail structure and data-URI images while stripping executable elements, active attributes, remote loads, and arbitrary links.
4. Wrap the sanitized result with a strict CSP and insert `<mark id="agenda-source">` around the cited quote when it can be matched. Otherwise show the expected quote in a non-script fallback banner at the top.
5. Add `GET /api/knowledge/evidence/{agenda_id}/mail` returning `text/html`. Add an availability flag and URL to projected references without exposing `source_path` to the browser.
6. Extend demo seeding to write owned `body.html` fixtures so the feature is browser-verifiable and removable by the existing demo manifest.
7. Run `PYTHONPATH=. pytest -q tests/test_source_mail.py tests/test_knowledge_api.py tests/test_wiki_projections.py tests/test_seed_demo_wiki.py`.
8. Commit only Task 3 files.

## Task 4: Make Library mode-exclusive and category-hierarchical

**Files:**
- Modify: `web/src/types/knowledge.ts`
- Modify: `web/src/api/knowledge.ts`
- Modify: `web/src/hooks/useWikiCollection.ts`
- Modify: `web/src/components/wiki/KnowledgeTree.tsx`
- Modify: `web/src/pages/WikiWorkspacePage.tsx`
- Test: `web/src/components/wiki/KnowledgeTree.test.tsx`
- Test: `web/src/pages/WikiWorkspacePage.test.tsx`

1. Add failing tests proving that only the active Library appears and LOTCD mode renders navigable Domain, Tech, and LOTCD levels.
2. Add the generic category response type and a category API function accepting one, two, or three path segments.
3. Update the collection hook so Domain and Tech routes fetch documents instead of showing an empty collection state.
4. Pass active mode to the Library and fetch only the data required for that mode. Remove the simultaneous four-root list and build-status chrome from the Library.
5. Run `npm test --prefix web -- KnowledgeTree.test.tsx WikiWorkspacePage.test.tsx`.
6. Commit only Task 4 files, including the already-started workspace corrections when they are directly covered by these tests.

## Task 5: Render one content-first synthesized document

**Files:**
- Modify: `web/src/components/wiki/WikiDocumentPane.tsx`
- Modify: `web/src/components/wiki/WikiTopicDocument.tsx`
- Modify: `web/src/pages/WikiWorkspacePage.tsx`
- Modify: `web/src/styles/wiki.css`
- Test: `web/src/components/wiki/WikiDocumentPane.test.tsx`
- Test: `web/src/components/wiki/WikiTopicDocument.test.tsx`
- Test: `web/src/pages/WikiWorkspacePage.test.tsx`

1. Add failing tests for one article per selection, parent roll-up labels, inline citations, the bottom reference section, and new-tab original-mail links (`target="_blank"`, `rel="noopener noreferrer"`).
2. Normalize Topic/Category/Team/Week projections into one document layout: eyebrow and breadcrumb, title, short deck, updated metadata, table of contents, narrative sections, related topics, and references.
3. Show direct and rolled-up contributions with quiet text labels rather than dashboard cards. Citation buttons scroll to the matching bottom reference; source links open the mail endpoint in a new tab.
4. Keep Graph as a separate view. In Docs mode the center contains only the selected synthesized article; in Graph mode the right preview appears only after selecting a node.
5. Run `npm test --prefix web -- WikiDocumentPane.test.tsx WikiTopicDocument.test.tsx WikiWorkspacePage.test.tsx`.
6. Commit only Task 5 files.

## Task 6: Simplify the shell to Medium-like reading UI

**Files:**
- Modify: `web/src/components/WikiShell.tsx` or the actual shell component found in the project
- Modify: `web/src/pages/WikiWorkspacePage.tsx`
- Modify: `web/src/styles/wiki.css`
- Test: `web/src/pages/WikiWorkspacePage.test.tsx`

1. Add/adjust tests for the essential shell: compact header, mode switch, single Library column, centered reading column, and Graph controls. Avoid tests coupled to exact decorative markup.
2. Remove the dark utility rail and card-heavy/status-heavy chrome from the workspace. Use a warm paper background, restrained borders, readable serif article typography, compact sans-serif navigation, and a reading width around 720–780px.
3. Preserve keyboard focus, semantic headings, responsive collapse, and reduced-motion behavior.
4. Run `npm test --prefix web -- WikiWorkspacePage.test.tsx` and `npm run build --prefix web`.
5. Commit only Task 6 files.

## Task 7: Focused end-to-end verification

**Files:**
- Modify only files required by failures directly caused by Tasks 1–6.

1. Seed demo data into temporary directories and run the preview server against them.
2. Browser-smoke these paths:
   - LOTCD Library shows only Domain/Tech/LOTCD hierarchy.
   - Domain, Tech, and LOTCD nodes each open one synthesized article.
   - Parent articles include rolled-up descendant content.
   - Topic/Team/Week mode switches replace the Library contents.
   - Inline citation reaches the reference section.
   - “원본 메일 보기” opens a new tab with sanitized mail and highlighted/fallback quote.
   - Graph mode still renders and node selection opens the document preview.
3. Run focused full suites:
   - `PYTHONPATH=. pytest -q`
   - `npm test --prefix web`
   - `npm run build --prefix web`
4. Review `git diff --check`, `git status --short`, and the final diff for unrelated changes. Do not add `.superpowers/brainstorm/` artifacts.
5. Use `verification-before-completion`, then commit any narrow verification fixes.
6. Use `finishing-a-development-branch` to present the verified branch state without merging or deleting the worktree unless explicitly requested.

