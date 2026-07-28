# Outlook Folder Setup React Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved B-style two-step Outlook folder setup wizard as an isolated React + Vite SPA using dummy data and a replaceable service boundary.

**Architecture:** A TypeScript service owns dummy connection, validation, and password-free local persistence. React components own only transient form and wizard state. The application lives in `frontend/` and does not modify the existing Python or Streamlit applications.

**Tech Stack:** React 19.2.8, Vite 8.1.5, TypeScript 6.0.3, Vitest 4.1.10, React Testing Library 16.3.2, plain scoped CSS.

## Global Constraints

- Use the approved two-step wizard: connection, then folder selection, then a compact completion state.
- Keep the app under `frontend/`; do not modify existing Python or Streamlit application files.
- Use `#5E6AD2` as the only chromatic UI accent except semantic success/error colors.
- Use a light gray page, white card, 1px neutral borders, 8–12px radii, and restrained shadow.
- Do not add a component framework, router, global state library, or production API client.
- Never store or render the password after the connection step completes.
- Persist only user ID, selected folder IDs, and save timestamp in `localStorage`.
- All user-facing copy is Korean.

---

## File Structure

- Create `frontend/package.json` — scripts and dependency pins.
- Create `frontend/index.html` — Vite entry document.
- Create `frontend/tsconfig.json`, `frontend/tsconfig.app.json`, `frontend/tsconfig.node.json` — strict TypeScript configuration.
- Create `frontend/vite.config.ts` — React and Vitest configuration.
- Create `frontend/eslint.config.js` — TypeScript/React flat ESLint configuration.
- Create `frontend/src/main.tsx` — React root.
- Create `frontend/src/types.ts` — shared mail-source types.
- Create `frontend/src/services/mailSourceService.ts` — dummy async service and persistence boundary.
- Create `frontend/src/services/mailSourceService.test.ts` — service validation and secret-safety tests.
- Create `frontend/src/components/StepIndicator.tsx` — accessible two-step progress indicator.
- Create `frontend/src/components/ConnectionStep.tsx` — credentials form with local password state.
- Create `frontend/src/components/FolderSelectionStep.tsx` — search, selection, navigation, and save form.
- Create `frontend/src/components/CompletionState.tsx` — password-free saved summary.
- Create `frontend/src/App.tsx` — wizard orchestration.
- Create `frontend/src/App.test.tsx` — end-to-end component flow tests.
- Create `frontend/src/styles.css` — approved Linear/Notion-inspired visual system and responsive rules.
- Create `frontend/src/test/setup.ts` — Testing Library cleanup and DOM matchers.

---

### Task 1: Vite Foundation and Dummy Mail Source Service

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/index.html`
- Create: `frontend/tsconfig.json`
- Create: `frontend/tsconfig.app.json`
- Create: `frontend/tsconfig.node.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/eslint.config.js`
- Create: `frontend/src/types.ts`
- Create: `frontend/src/services/mailSourceService.ts`
- Create: `frontend/src/services/mailSourceService.test.ts`
- Create: `frontend/src/test/setup.ts`

**Interfaces:**
- Produces: `MailFolder`, `SavedMailSource`, `StorageLike`, and `DummyMailSourceService`.
- Produces: `connect(userId: string, password: string): Promise<MailFolder[]>`.
- Produces: `saveSelection(userId: string, folderIds: string[]): Promise<SavedMailSource>`.
- Produces: `loadSelection(): SavedMailSource | null`.

- [ ] **Step 1: Create the Vite package and strict test configuration**

Use this package manifest:

```json
{
  "name": "weekly-mail-folder-setup",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "lint": "eslint .",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "dependencies": {
    "@vitejs/plugin-react": "6.0.4",
    "vite": "8.1.5",
    "react": "19.2.8",
    "react-dom": "19.2.8"
  },
  "devDependencies": {
    "@eslint/js": "10.0.1",
    "@testing-library/jest-dom": "7.0.0",
    "@testing-library/react": "16.3.2",
    "@testing-library/user-event": "14.6.1",
    "@types/react": "19.2.17",
    "@types/react-dom": "19.2.3",
    "eslint": "10.8.0",
    "eslint-plugin-react-hooks": "7.1.1",
    "eslint-plugin-react-refresh": "0.5.3",
    "globals": "17.8.0",
    "jsdom": "30.0.0",
    "typescript": "6.0.3",
    "typescript-eslint": "8.65.0",
    "vitest": "4.1.10"
  }
}
```

Use these exact configuration files:

```json
// tsconfig.json
{
  "files": [],
  "references": [{ "path": "./tsconfig.app.json" }, { "path": "./tsconfig.node.json" }]
}
```

```json
// tsconfig.app.json
{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "Bundler",
    "allowImportingTsExtensions": true,
    "verbatimModuleSyntax": true,
    "moduleDetection": "force",
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "noUncheckedSideEffectImports": true
  },
  "include": ["src"]
}
```

```json
// tsconfig.node.json
{
  "compilerOptions": {
    "tsBuildInfoFile": "./node_modules/.tmp/tsconfig.node.tsbuildinfo",
    "target": "ES2023",
    "lib": ["ES2023"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "Bundler",
    "allowImportingTsExtensions": true,
    "verbatimModuleSyntax": true,
    "moduleDetection": "force",
    "noEmit": true,
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "noUncheckedSideEffectImports": true
  },
  "include": ["vite.config.ts"]
}
```

```ts
// vite.config.ts
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    css: true,
  },
})
```

```js
// eslint.config.js
import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist'] },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: { ecmaVersion: 2022, globals: globals.browser },
    plugins: { 'react-hooks': reactHooks, 'react-refresh': reactRefresh },
    rules: {
      ...reactHooks.configs.flat.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
    },
  },
)
```

```ts
// src/test/setup.ts
import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(() => {
  cleanup()
  window.localStorage.clear()
})
```

- [ ] **Step 2: Install dependencies**

Run: `cd frontend && npm install`

Expected: exit code 0 and a new `frontend/package-lock.json`.

- [ ] **Step 3: Write failing service tests**

```ts
import { describe, expect, it } from 'vitest'
import { DummyMailSourceService, type StorageLike } from './mailSourceService'

const memoryStorage = (): StorageLike => {
  const values = new Map<string, string>()
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  }
}

describe('DummyMailSourceService', () => {
  it('returns stable Outlook folders and rejects missing credentials', async () => {
    const service = new DummyMailSourceService(memoryStorage())
    await expect(service.connect('', 'password')).rejects.toThrow('사용자 ID를 입력해 주세요.')
    await expect(service.connect('user@company.com', '')).rejects.toThrow('비밀번호를 입력해 주세요.')
    await expect(service.connect('user@company.com', 'dummy-password')).resolves.toMatchObject([
      { id: 'inbox', name: 'Inbox', defaultSelected: true },
      { id: 'weekly-reports', name: 'Weekly Reports', defaultSelected: true },
    ])
  })

  it('stores only valid folder selections and never stores a password', async () => {
    const storage = memoryStorage()
    const service = new DummyMailSourceService(storage)
    await service.connect('user@company.com', 'super-secret')
    const saved = await service.saveSelection('user@company.com', ['inbox', 'weekly-reports'])

    expect(saved.folderIds).toEqual(['inbox', 'weekly-reports'])
    expect(storage.getItem('weekly-mail:mail-source')).not.toContain('super-secret')
    await expect(service.saveSelection('user@company.com', [])).rejects.toThrow('폴더를 하나 이상 선택해 주세요.')
    await expect(service.saveSelection('user@company.com', ['unknown'])).rejects.toThrow('선택할 수 없는 폴더가 포함되어 있습니다.')
  })
})
```

- [ ] **Step 4: Run the service test to verify it fails**

Run: `cd frontend && npm test -- src/services/mailSourceService.test.ts`

Expected: FAIL because `mailSourceService.ts` does not exist.

- [ ] **Step 5: Implement the shared types and dummy service**

```ts
export interface MailFolder {
  id: string
  name: string
  path: string
  itemCount: number
  defaultSelected: boolean
}

export interface SavedMailSource {
  userId: string
  folderIds: string[]
  savedAt: string
}
```

```ts
import type { MailFolder, SavedMailSource } from '../types'

export interface StorageLike {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
}

const STORAGE_KEY = 'weekly-mail:mail-source'
const FOLDERS: MailFolder[] = [
  { id: 'inbox', name: 'Inbox', path: 'Inbox', itemCount: 1248, defaultSelected: true },
  { id: 'weekly-reports', name: 'Weekly Reports', path: 'Inbox / Weekly Reports', itemCount: 186, defaultSelected: true },
  { id: 'project-alpha', name: 'Project Alpha', path: 'Projects / Alpha', itemCount: 72, defaultSelected: false },
  { id: 'team-updates', name: 'Team Updates', path: 'Inbox / Team Updates', itemCount: 94, defaultSelected: false },
  { id: 'archive-2026', name: 'Archive 2026', path: 'Archive / 2026', itemCount: 324, defaultSelected: false },
]

export class DummyMailSourceService {
  constructor(private readonly storage: StorageLike = window.localStorage) {}

  async connect(userId: string, password: string): Promise<MailFolder[]> {
    if (!userId.trim()) throw new Error('사용자 ID를 입력해 주세요.')
    if (!password) throw new Error('비밀번호를 입력해 주세요.')
    return FOLDERS.map((folder) => ({ ...folder }))
  }

  async saveSelection(userId: string, folderIds: string[]): Promise<SavedMailSource> {
    if (folderIds.length === 0) throw new Error('폴더를 하나 이상 선택해 주세요.')
    const validIds = new Set(FOLDERS.map(({ id }) => id))
    if (folderIds.some((id) => !validIds.has(id))) throw new Error('선택할 수 없는 폴더가 포함되어 있습니다.')
    const saved = { userId: userId.trim(), folderIds: [...new Set(folderIds)], savedAt: new Date().toISOString() }
    this.storage.setItem(STORAGE_KEY, JSON.stringify(saved))
    return saved
  }

  loadSelection(): SavedMailSource | null {
    const raw = this.storage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as SavedMailSource) : null
  }
}
```

- [ ] **Step 6: Run service tests, typecheck, and lint**

Run: `cd frontend && npm test -- src/services/mailSourceService.test.ts && npm run build && npm run lint`

Expected: all service tests pass; TypeScript, Vite build, and ESLint exit 0.

- [ ] **Step 7: Commit the foundation**

```bash
git add frontend/package.json frontend/package-lock.json frontend/index.html frontend/tsconfig*.json frontend/vite.config.ts frontend/eslint.config.js frontend/src/types.ts frontend/src/services frontend/src/test
git commit -m "feat(ui): add dummy mail source service"
```

---

### Task 2: Two-Step Wizard Behavior

**Files:**
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/App.test.tsx`
- Create: `frontend/src/components/StepIndicator.tsx`
- Create: `frontend/src/components/ConnectionStep.tsx`
- Create: `frontend/src/components/FolderSelectionStep.tsx`
- Create: `frontend/src/components/CompletionState.tsx`

**Interfaces:**
- Consumes: `DummyMailSourceService`, `MailFolder`, and `SavedMailSource` from Task 1.
- Produces: `App({ service? }: { service?: DummyMailSourceService })` for production and deterministic tests.
- Produces: connection → folder selection → completion transitions without retaining a password in `App` state.

- [ ] **Step 1: Write the failing wizard flow test**

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import App from './App'
import { DummyMailSourceService } from './services/mailSourceService'

describe('Outlook folder setup wizard', () => {
  it('connects, selects folders, saves, and shows a password-free summary', async () => {
    const user = userEvent.setup()
    render(<App service={new DummyMailSourceService(window.localStorage)} />)

    await user.type(screen.getByLabelText('사용자 ID'), 'user@company.com')
    await user.type(screen.getByLabelText('비밀번호'), 'super-secret')
    await user.click(screen.getByRole('button', { name: '연결하고 다음' }))

    expect(await screen.findByRole('heading', { name: '수집할 폴더 선택' })).toBeInTheDocument()
    await user.click(screen.getByLabelText(/Project Alpha/))
    await user.click(screen.getByRole('button', { name: /선택한 3개 폴더 저장/ }))

    expect(await screen.findByRole('heading', { name: '설정이 저장되었습니다' })).toBeInTheDocument()
    expect(screen.getByText('Project Alpha')).toBeInTheDocument()
    expect(screen.queryByText('super-secret')).not.toBeInTheDocument()
    expect(window.localStorage.getItem('weekly-mail:mail-source')).not.toContain('super-secret')
  })

  it('filters folders and allows returning to the connection step', async () => {
    const user = userEvent.setup()
    render(<App service={new DummyMailSourceService(window.localStorage)} />)
    await user.type(screen.getByLabelText('사용자 ID'), 'user@company.com')
    await user.type(screen.getByLabelText('비밀번호'), 'dummy')
    await user.click(screen.getByRole('button', { name: '연결하고 다음' }))
    await user.type(await screen.findByLabelText('폴더 검색'), 'weekly')
    expect(screen.getByText('Weekly Reports')).toBeInTheDocument()
    expect(screen.queryByText('Project Alpha')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '이전' }))
    expect(screen.getByRole('heading', { name: 'Outlook 계정 연결' })).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run the wizard test to verify it fails**

Run: `cd frontend && npm test -- src/App.test.tsx`

Expected: FAIL because `App.tsx` and the wizard components do not exist.

- [ ] **Step 3: Implement the progress and connection components**

`StepIndicator` renders an ordered list with `aria-current="step"` on the active item. `ConnectionStep` owns both controlled inputs, calls `onConnect(userId, password)`, clears its password state in `finally`, disables submission while pending, and renders caught error text with `role="alert"`.

Use these exact props:

```ts
interface ConnectionStepProps {
  onConnect: (userId: string, password: string) => Promise<void>
}

interface StepIndicatorProps {
  currentStep: 1 | 2
}
```

- [ ] **Step 4: Implement folder selection and completion components**

Use these exact props:

```ts
interface FolderSelectionStepProps {
  folders: MailFolder[]
  selectedIds: string[]
  onSelectionChange: (ids: string[]) => void
  onBack: () => void
  onSave: () => Promise<void>
}

interface CompletionStateProps {
  userId: string
  folders: MailFolder[]
  saved: SavedMailSource
  onEdit: () => void
}
```

`FolderSelectionStep` filters case-insensitively by `name` or `path`, displays `itemCount.toLocaleString('ko-KR')`, preserves selections hidden by search, disables save when nothing is selected, and shows caught save errors with `role="alert"`.

- [ ] **Step 5: Implement App orchestration without password retention**

`App` stores only `phase`, `userId`, `folders`, `selectedIds`, and `saved`. Its connection callback receives the password, passes it directly to `service.connect`, and never writes it to state. On successful connection, initialize `selectedIds` from `defaultSelected`. On back, return to connection and discard fetched folders. On edit after completion, return to folder selection.

- [ ] **Step 6: Run wizard tests**

Run: `cd frontend && npm test -- src/App.test.tsx`

Expected: both wizard flow tests pass.

- [ ] **Step 7: Run all tests and commit behavior**

Run: `cd frontend && npm test`

Expected: service and App suites pass.

```bash
git add frontend/src/main.tsx frontend/src/App.tsx frontend/src/App.test.tsx frontend/src/components
git commit -m "feat(ui): add Outlook folder setup wizard"
```

---

### Task 3: Approved Visual Design, Accessibility, and Production Verification

**Files:**
- Create: `frontend/src/styles.css`
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/components/StepIndicator.tsx`
- Modify: `frontend/src/components/ConnectionStep.tsx`
- Modify: `frontend/src/components/FolderSelectionStep.tsx`
- Modify: `frontend/src/components/CompletionState.tsx`

**Interfaces:**
- Consumes: completed wizard behavior from Task 2.
- Produces: responsive approved B-style layout with visible focus, status, error, loading, empty-filter, and completion states.

- [ ] **Step 1: Add failing accessibility assertions**

Extend `App.test.tsx` with assertions that the first step exposes `aria-current="step"`, all form inputs have labels, folder results live in a `fieldset` with legend `Outlook 폴더`, connection errors use `role="alert"`, and the completion summary uses `role="status"`.

- [ ] **Step 2: Run tests to verify the assertions fail**

Run: `cd frontend && npm test -- src/App.test.tsx`

Expected: FAIL on missing fieldset, status, or progress semantics.

- [ ] **Step 3: Implement semantic markup and the visual system**

Define these CSS tokens and apply them consistently:

```css
:root {
  font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: #202124;
  background: #f6f7f9;
  font-synthesis: none;
  --accent: #5e6ad2;
  --accent-hover: #4f59bd;
  --ink: #202124;
  --muted: #737780;
  --line: #e3e5e8;
  --surface: #ffffff;
  --success: #168653;
  --error: #b42318;
}
```

The page uses a centered `min(100% - 32px, 800px)` shell, 40–64px vertical padding, a 12px radius white card, 1px border, and `0 20px 55px rgba(31, 35, 48, 0.08)` shadow. Inputs and buttons have at least 42px height. Use `:focus-visible` with a 3px translucent accent ring. At `max-width: 640px`, stack form actions and remove nonessential card padding. Respect `prefers-reduced-motion`.

- [ ] **Step 4: Run tests, lint, and production build**

Run: `cd frontend && npm test && npm run lint && npm run build`

Expected: all commands exit 0 and `frontend/dist/index.html` exists.

- [ ] **Step 5: Start the app and inspect both viewport sizes**

Run: `cd frontend && npm run dev -- --host 127.0.0.1`

Inspect desktop at 1440×900 and mobile at 390×844. Verify connection, search, selection, back, save, edit, validation, keyboard focus order, and that no password appears in localStorage.

- [ ] **Step 6: Commit the finished UI**

```bash
git add frontend
git commit -m "feat(ui): finish folder setup experience"
```

---

## Final Verification

Run:

```bash
cd frontend
npm test
npm run lint
npm run build
```

Expected:

- All Vitest suites pass.
- ESLint reports zero errors.
- TypeScript and Vite production build succeed.
- `dist/` is generated and contains the SPA entry assets.
- Saved localStorage JSON contains `userId`, `folderIds`, and `savedAt`, with no password field or submitted password value.
