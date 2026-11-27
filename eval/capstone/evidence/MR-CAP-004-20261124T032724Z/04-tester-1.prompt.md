## Role
tester

## Goal
Add Vitest regression tests for the ScraperError class (tracker-api/src/services/facebook-scraper.ts) to the existing scraper test file. Change nothing else.

## Acceptance Criteria
- A new describe('ScraperError', ...) block is added to the existing tracker-api/tests/services/facebook-scraper.test.ts (which mirrors the src path, project decision-002), and ScraperError is added to that file's existing import from '../../src/services/facebook-scraper'. No existing test in the file is changed and no new test file is created.
- new ScraperError('boom') is an instance of both ScraperError and Error, has name === 'ScraperError' and message === 'boom'.
- new ScraperError('boom', { cause }) keeps the same cause object (asserted with toBe).
- new ScraperError('boom') without options has cause undefined.
- The new describe block adds exactly three tests.
- No file under tracker-api/src is modified and no dependency is added.
- `npm --prefix tracker-api test` passes, including all existing tests (currently 37 in 5 files).

## Relevant Context
Command rules for this session (governance D18):
1. Run the tests with the Bash tool using exactly `npm --prefix tracker-api test`, character for character, or with the run-tests skill, which runs the same command.
2. Do not add anything to it: no pipes (`|`, for example `| tail`), no redirects (`>`, `2>&1`), no shell operators (`&&`, `;`, `||`, `&`), no `cd`, and no extra arguments or flags. Any other command is denied before it runs and escalates this run to a human, even if the tests pass.
3. Do not shorten the output. The tool response returns the complete Vitest output, including the `Test Files` and `Tests` summary lines. Read the results from that response.
4. If a command is denied, do not retry it in any modified form and do not try another command.
5. If the exact command is denied, errors or cannot run, do not work around it. Report FAIL, quote the error verbatim under `### Findings`, and stop.

## Approved Files
Changed files (measured by the Orchestrator): `tracker-api/tests/services/facebook-scraper.test.ts`

## Previous Result
### Changes Made
I added `ScraperError` regression tests to the existing scraper test file. I didn't run the tests; per my role, that's left to the Tester.

- **Import:** `ScraperError` is now part of the existing import from `'../../src/services/facebook-scraper'`. The import reads `buildMarketplaceSearchUrl, ScraperError, type MarketplaceSearchParams`, and `MarketplaceSearchParams` stays a type-only import.
- **New tests:** a new top-level `describe('ScraperError', ...)` block follows the `buildMarketplaceSearchUrl` block. It has exactly three plain `it` tests:
  1. `new ScraperError('boom')` is an instance of both `ScraperError` and `Error`, its name is `'ScraperError'` and its message is `'boom'`.
  2. `new ScraperError('boom', { cause })` keeps the same `cause` object, checked with `toBe`.
  3. `new ScraperError('boom')` with no options has `cause` undefined, checked with `toBeUndefined()`.

### Files Modified
- `tracker-api/tests/services/facebook-scraper.test.ts`

### Notes
- **Other files:** I left existing tests, the `vi.mock` call, everything under `tracker-api/src` and all dependencies unchanged, and created no new files.
- **Expected result:** the Tester should see 40 tests in 5 files (37 existing + 3 new).
- **Source checked:** the class in the source (`super(message, options)`, then `this.name = 'ScraperError'`) behaves the way these tests expect.
- **Retrieval:** the tool worked, but the four lessons it returned (two chunks from one file) are about whether Facebook scraping is feasible and how the backend is split up. None of them applied to this task.
- **Lesson stored:** `lesson/MR-CAP-004` (internal; no sensitive content).

## Expected Output
The `### Result` (PASS or FAIL), `### Validation Performed`, `### Findings` and `### Failed Acceptance Criteria` sections from your definition.
