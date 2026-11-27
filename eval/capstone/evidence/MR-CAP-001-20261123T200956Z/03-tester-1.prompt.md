## Role
tester

## Goal
Add Vitest regression tests for buildMarketplaceSearchUrl (exported from tracker-api/src/services/facebook-scraper.ts). Do not change application source code.

## Acceptance Criteria
- A new test file exists at tracker-api/tests/services/facebook-scraper.test.ts, mirroring the src path (project decision-002).
- Tests cover: location slug is lowercased with whitespace removed; query, exact=false and radius appear as URL search parameters; maxPrice is included when it is a positive number; maxPrice is omitted when it is null, undefined or 0.
- `npm --prefix tracker-api test` passes, including the existing 15 tests.
- No file under tracker-api/src is modified.

## Relevant Context
Run the tests with exactly `npm --prefix tracker-api test` (or the run-tests skill); other commands are not permitted in this session.

## Approved Files
Changed files (measured by the Orchestrator): `tracker-api/tests/services/facebook-scraper.test.ts`

## Previous Result
I added the new test file for `buildMarketplaceSearchUrl` with 12 tests. I haven't run anything: under my role the Tester does that, so `npm --prefix tracker-api test` passing is still unconfirmed.

### Changes Made
- Created `tracker-api/tests/services/facebook-scraper.test.ts`, in a new `tests/services/` folder that mirrors `src/services/` (decision-002).
- I followed the style of `tests/store/searches.test.ts` and mocked `../../src/lib/prisma` before importing the code under test. This keeps the test from needing Prisma or a database if the `store/listings` import is loaded at runtime.
- Each test turns the result into a `URL` and checks `origin`, `pathname` and `searchParams`, so parameter order and encoding don't matter. The tests are inside `describe('buildMarketplaceSearchUrl')`:
  - **Location slug** (3 tests):
    - The origin is `https://www.facebook.com`.
    - `'New York City'` becomes `/marketplace/newyorkcity/search/`.
    - `' Phil  ADELPHIA\t\n'` becomes `/marketplace/philadelphia/search/`.
  - **Search parameters** (4 tests):
    - `query` is passed through, including a query with a space (`'gsxr 1000'`).
    - `exact === 'false'`.
    - `radius === '65'`.
  - **maxPrice** (5 tests):
    - `6500` gives `maxPrice === '6500'`.
    - An `it.each` over `null`, `undefined` and `0` checks `maxPrice` is left out each time.
    - One more case leaves the `maxPrice` key out entirely and checks the same thing.
- I saved one lesson to storage under `lesson/MR-CAP-001`: mock Prisma before importing the scraper, and compare parsed URLs instead of raw strings.

### Files Modified
- `tracker-api/tests/services/facebook-scraper.test.ts` (new; the only file touched)

### Notes
- **Retrieval:** it worked and returned 4 lessons about scraper feasibility, authentication and backend reuse. None of them applied to this task.
- **No source changes:** nothing under `tracker-api/src`, the existing tests or the config was changed.
- **Expected total:** if the existing count really is 15, the full run should show 27 tests (15 + 12). The Tester should confirm the starting count of 15.
- **Negative `maxPrice`:** no test covers it, as the plan specified. The source uses `if (params.maxPrice)`, so a negative value would still be added to the URL.
- **Dependencies:** if `tracker-api/node_modules` is missing, the Tester needs to run `npm --prefix tracker-api ci` before the test run.

## Expected Output
The `### Result` (PASS or FAIL), `### Validation Performed`, `### Findings` and `### Failed Acceptance Criteria` sections from your definition.
