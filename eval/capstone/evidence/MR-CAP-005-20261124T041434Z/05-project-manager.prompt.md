Task identifier: MR-CAP-005

Implementation summary:
### Changes Made
I added a new Vitest file with three regression tests for `RunSearchError`. I haven't run the test suite: running tests is the Tester's job.

- **Setup:** it mocks `../../src/lib/prisma` with `vi.mock(..., () => ({ prisma: {} }))`, the same way `facebook-scraper.test.ts` does. Then it imports only `RunSearchError` from `../../src/services/search-runner`.
- **One `describe('RunSearchError')` block with exactly three `it` tests:**
  1. `new RunSearchError('boom')` is an instance of both `RunSearchError` and `Error`, its `name` is `'RunSearchError'` and its `message` is `'boom'`.
  2. `new RunSearchError('boom', { cause })` keeps the same cause object (`expect(err.cause).toBe(cause)`).
  3. `new RunSearchError('boom')` with no options has `cause` undefined.
- The file never references `runSavedSearch`, and I made no source, config, `package.json` or lockfile changes.

### Files Modified
- `tracker-api/tests/services/search-runner.test.ts` (new)

### Notes
- **Retrieval:** it worked and returned two `internal` lessons about keeping backend logic reusable across clients. Neither applied to this task.
- **Confirmed before writing:** I read `search-runner.ts` to check how `RunSearchError` is built. It passes `options` through to `super(message, options)` and sets `this.name`. I also checked that the test file didn't already exist.
- **Lesson stored:** I saved it under key `lesson/MR-CAP-005`. It's internal material only: the Prisma mock is all you need to import tracker-api services in tests.
- **Expected result:** the Tester should see 43 tests in 6 files from `npm --prefix tracker-api test`, with 3 new tests in this file.

Code review outcome: APPROVE (Code Reviewer attempt 1; explicit verdict APPROVE; 0 High-severity findings).

Code review report:
## Review: MR-CAP-005 — regression tests for `RunSearchError`

**Retrieval:** it worked and returned two `internal` lessons about keeping backend logic reusable across clients. Neither applies to a test-only change.

**What I checked:**
- **Mock and import:** `tracker-api/tests/services/search-runner.test.ts` lines 3–7 mock `../../src/lib/prisma` the same way as `facebook-scraper.test.ts` lines 3–5. The file then imports only `RunSearchError` from `../../src/services/search-runner`. `runSavedSearch` is never mentioned.
- **Test count:** there is exactly one `describe` with three `it` calls, and no `it.each`.
- **Assertions:** they match the source. The constructor at `search-runner.ts:17-22` passes `options` to `super(message, options)` and sets `this.name = 'RunSearchError'`. So all three checks hold: the instance and name check (test 1), the `toBe(cause)` identity check (test 2), and `toBeUndefined()` when no options are given (test 3). `tsconfig.json` targets `ES2022`, so `cause` is typed correctly. Its `include` is `["src"]`, so `tsc` won't type-check the tests anyway.
- **Scope:** the measured changed-file list contains only the new test file. No source, config or dependency files changed.

**Findings:** none at High, Medium or Low severity.

**Note (not a defect in the change):** the Implementer says it saved an internal lesson under `lesson/MR-CAP-005`. Nothing by that name is in the working tree. Only the existing orchestration and eval fixtures mention the task ID, so the lesson was probably stored through the MCP storage server, which fits the rule that internal material stays out of committed files. I can't confirm that from here.

**Overall assessment:** the file is small, correct and stays within scope. It meets every acceptance criterion I can check by reading the code. I didn't run the tests, since that is the Tester's job, so the expected 43 tests in 6 files still needs the Tester to confirm it.

### Verdict
APPROVE

Test results (Tester attempt 1, PASS):
### Result
**PASS.** The new test file meets all seven acceptance criteria, and the full suite passes with 43 tests in 6 files.

### Validation Performed
- **Retrieval:** I called `mcp__retrieval__retrieve` with "tracker-api search-runner RunSearchError vitest tests prisma mock". It returned two `internal` chunks from `lesson-shared-backend-clients.md`, about keeping backend logic reusable across clients. Neither applied to this task.
- **Files read (Read and Grep only):**
  - `tracker-api/tests/services/search-runner.test.ts` (the new file)
  - `tracker-api/tests/services/facebook-scraper.test.ts` (lines 1–12, to compare the mock)
  - `tracker-api/src/services/search-runner.ts` (the `RunSearchError` definition)
- **Test run:** I ran `npm --prefix tracker-api test` once, exactly as authorized. Output:
  ```
   ✓ tests/store/searches.test.ts (12 tests) 8ms
   ✓ tests/routes/health.test.ts (2 tests) 26ms
   ✓ tests/store/listings.test.ts (3 tests) 3ms
   ✓ tests/services/facebook-scraper.test.ts (15 tests) 3ms
   ✓ tests/services/connections/index.test.ts (8 tests) 2ms
   ✓ tests/services/search-runner.test.ts (3 tests) 2ms

   Test Files  6 passed (6)
        Tests  43 passed (43)
  ```

### Findings
Each criterion checked against the file and the test output:

1. **Location, mock and import:** The file is at `tracker-api/tests/services/search-runner.test.ts`, which mirrors the src path. Its mock, `vi.mock('../../src/lib/prisma', () => ({ prisma: {} }))`, is the same as lines 3–5 of `facebook-scraper.test.ts`. It imports `RunSearchError` from `'../../src/services/search-runner'`.
2. **Instance, name and message:** Test 1 checks that the error is an instance of both `RunSearchError` and `Error`, that `name` is `'RunSearchError'` and that `message` is `'boom'`.
3. **Cause kept:** Test 2 checks `expect(err.cause).toBe(cause)`.
4. **No cause without options:** Test 3 checks `expect(err.cause).toBeUndefined()`.
5. **Exactly three tests, no `runSavedSearch`:** The file has exactly three `it` tests in one `describe`, and Vitest reports 3 tests for it. The file never mentions `runSavedSearch`.
6. **Scope:** Your measured changed-file list contains only the new test file, so no dependency, `package.json` or lockfile changed. I relied on your list and did not check scope myself, per D18 rule 4.
7. **Suite:** All 40 existing tests in 5 files still pass, and the 3 new ones bring the total to 43 tests in 6 files, which matches what the Implementer predicted.

There were no command denials or errors.

One thing worth checking: the Implementer says it saved an `internal` lesson under the key `lesson/MR-CAP-005`. CLAUDE.md says internal material may only be stored in a non-committed location. That file isn't on your changed-file list, so it is probably fine, but you may want to confirm where that store lives. It does not affect this result.

### Failed Acceptance Criteria
None.

Requested final status: COMPLETE

Record the outcome with key `task-outcome/MR-CAP-005`.