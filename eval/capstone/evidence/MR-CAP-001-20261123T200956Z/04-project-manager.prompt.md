Task identifier: MR-CAP-001

Implementation summary:
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

Code review outcome: NOT_RUN. The CLAUDE.md standard sequence has no review stage, and review was not requested for this task.

Test results (Tester attempt 1, PASS):
### Result
**PASS**

### Validation Performed
1. **Retrieval:** called `mcp__retrieval__retrieve` with query "Vitest regression tests buildMarketplaceSearchUrl tracker-api facebook-scraper.ts tests/services". It worked and returned 4 internal lessons about scraper feasibility, authentication and keeping backend logic reusable. None of them affect this verification.
2. **Memory startup:** read `.memory/SCOPE.md` (it matches `/workspace` and MarketRadar), `.memory/project/MEMORY_INDEX.md` and `decisions/decision-002.md`.
3. **Inspection (read-only):** read `tracker-api/tests/services/facebook-scraper.test.ts` and `buildMarketplaceSearchUrl` with its `MarketplaceSearchParams` type in `tracker-api/src/services/facebook-scraper.ts` (lines 28–50).
4. **Test run:** `npm --prefix tracker-api test` (exactly as required). Output:
   ```
   ✓ tests/store/listings.test.ts (3 tests)
   ✓ tests/store/searches.test.ts (12 tests)
   ✓ tests/services/facebook-scraper.test.ts (12 tests)
   Test Files  3 passed (3)
        Tests  27 passed (27)
   ```

### Findings
- **Starting count confirmed:** the existing suite has 15 tests (3 + 12), and all still pass. The new file adds 12, for 27 in total, which matches what the Implementer expected.
- **File location (criterion 1):** the file is at `tracker-api/tests/services/facebook-scraper.test.ts`, mirroring `src/services/facebook-scraper.ts`, as decision-002 requires.
- **Coverage against the source (criterion 2):**
  - **Location slug:** the source does `location.toLowerCase().replace(/\s+/g, '')`.
    - `'New York City'` gives `newyorkcity`.
    - `' Phil  ADELPHIA\t\n'` gives `philadelphia`, which covers mixed case, repeated spaces, tabs, newlines and spaces at either end.
  - **Search parameters:** `query` (including `'gsxr 1000'` with a space), `exact=false` and `radius=65` are each checked through the parsed `URL.searchParams`.
  - **maxPrice included:** `6500` gives `maxPrice=6500`.
  - **maxPrice omitted:** `it.each` covers `null`, `undefined` and `0`. One more test leaves the key out entirely.
- **Assertion style:** parsing the result as a `URL` makes the tests independent of parameter order and encoding.
- **Prisma mock:** mocking `../../src/lib/prisma` matches decision-002 and is harmless. It is probably not needed, because the source only uses `ListingInput` as a type and that import is likely dropped at compile time.
- **Gaps (not required by the criteria):**
  - No test covers a negative `maxPrice`. Because the source uses `if (params.maxPrice)`, a negative value would still be added to the URL. This was left out by the plan.
  - Non-integer radius values are not tested.
- **Criterion 4 (no `src` changes):** the "test command only" rule meant I could not run `git status` or `git diff`. I'm relying on the Orchestrator's measured list, which names only the new test file. Nothing I read under `tracker-api/src` looked changed, and the existing tests still pass.
- I did not modify, create or delete any file.

### Failed Acceptance Criteria
None.

Requested final status: COMPLETE

Record the outcome with key `task-outcome/MR-CAP-001`.