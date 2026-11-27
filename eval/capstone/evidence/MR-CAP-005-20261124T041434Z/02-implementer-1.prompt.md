## Role
implementer

## Goal
Add Vitest regression tests for the RunSearchError class (tracker-api/src/services/search-runner.ts) in a new test file. Change nothing else.

## Acceptance Criteria
- A new test file exists at tracker-api/tests/services/search-runner.test.ts, mirroring the src path (project decision-002). It mocks '../../src/lib/prisma' the same way tracker-api/tests/services/facebook-scraper.test.ts does and imports RunSearchError from '../../src/services/search-runner'.
- new RunSearchError('boom') is an instance of both RunSearchError and Error, has name === 'RunSearchError' and message === 'boom'.
- new RunSearchError('boom', { cause }) keeps the same cause object (asserted with toBe).
- new RunSearchError('boom') without options has cause undefined.
- The new file contains exactly three tests and does not call runSavedSearch.
- The Orchestrator's measured changed-file list contains only tracker-api/tests/services/search-runner.test.ts, and no dependency is added.
- `npm --prefix tracker-api test` passes, including all existing tests (currently 40 in 5 files).

## Relevant Context
Task identifier: MR-CAP-005

Approved plan:

### Plan
Retrieval worked. It returned two `internal` lessons, both about keeping backend logic reusable across clients. Neither changes this task.

1. Create `tracker-api/tests/services/search-runner.test.ts`.
2. Line 1: `import { describe, expect, it, vi } from 'vitest'`.
3. Before the import under test, add `vi.mock('../../src/lib/prisma', () => ({ prisma: {} }))`. This is the same setup as `tests/services/facebook-scraper.test.ts` lines 3–5. Vitest moves `vi.mock` above the imports, so the stores load without touching a real Prisma client.
4. `import { RunSearchError } from '../../src/services/search-runner'`. Import only this; do not import or call `runSavedSearch`.
5. Add one `describe('RunSearchError', ...)` block with exactly three `it` tests:
   - **Test 1:** `const err = new RunSearchError('boom')`. Assert `toBeInstanceOf(RunSearchError)`, `toBeInstanceOf(Error)`, `err.name` is `'RunSearchError'` and `err.message` is `'boom'`.
   - **Test 2:** `const cause = new Error('root')`. Assert that `new RunSearchError('boom', { cause }).cause` `toBe(cause)`.
   - **Test 3:** Assert that `new RunSearchError('boom').cause` `toBeUndefined()`.
6. Make no other changes: no source edits, no `package.json` or lockfile changes, no new dependencies, no config.

### Files
- `tracker-api/tests/services/search-runner.test.ts` (new): the three regression tests for `RunSearchError`. Its path mirrors `src/services/search-runner.ts` (decision-002). This is the only file to create or change.

### Risks
- **Test discovery:** `tracker-api` has no `vitest.config.*`, so Vitest's default include pattern (`**/*.test.ts`) should find the new file the same way it finds the existing tests.
- **Import side effects:** `search-runner.ts` imports `facebook-scraper` and the stores. The existing scraper test shows the Prisma mock alone is enough, so the risk is low. If the import fails anyway, the Implementer should escalate rather than add more mocks or touch source files.
- **`cause` typing:** `cause` relies on ES2022 Error options, which tsconfig supports. Vitest runs through esbuild without type-checking, so this won't block the tests either way.
- **Test count:** the file must contain exactly three `it` calls. Do not use `it.each` or split assertions into extra tests.

### Acceptance Check
- `npm --prefix tracker-api test` passes with 43 tests in 6 files (the current 40 in 5 plus 3 new), and the new file reports 3 passing tests.
- The Orchestrator's measured changed-file list contains only `tracker-api/tests/services/search-runner.test.ts`, and `package.json` and the lockfile are unchanged.
- Reading the file shows:
  - the `vi.mock('../../src/lib/prisma', ...)` setup and the import from `'../../src/services/search-runner'`
  - `toBe(cause)` for the cause identity check
  - no reference to `runSavedSearch`
  - exactly three `it(` calls

## Approved Files
- `tracker-api/tests/services/search-runner.test.ts`

Modify no other file.

## Previous Result
None.

## Expected Output
The `### Changes Made`, `### Files Modified` and `### Notes` sections from your definition.
