### Plan
Retrieval worked. The 4 lessons it returned were about scraper feasibility, authentication and backend reuse, and none of them apply to writing unit tests. The repository has no `vitest.config.*`, so Vitest uses its defaults and will pick up `tests/**/*.test.ts`.

1. Create `tracker-api/tests/services/facebook-scraper.test.ts` (new `services/` folder under `tests/`, matching `src/services/`, per decision-002).
2. Follow the style in `tests/store/searches.test.ts`: import `describe, expect, it, vi` from `vitest`. As a precaution, add `vi.mock('../../src/lib/prisma', () => ({ prisma: {} }))` before importing the code under test. `facebook-scraper.ts` imports `ListingInput` from `../store/listings`, and that file imports `../lib/prisma`. This keeps the test from needing a generated Prisma client or a database.
3. Import `buildMarketplaceSearchUrl` from `../../src/services/facebook-scraper`. Importing `playwright` has no side effects: no browser starts until `scrapeMarketplaceSearch` is called.
4. Parse the result with `new URL(result)` and check `pathname` and `searchParams` instead of comparing whole strings, so parameter order and encoding don't affect the tests. Add these tests inside `describe('buildMarketplaceSearchUrl')`:
   - **Location slug:** e.g. `location: 'New York City'` gives pathname `/marketplace/newyorkcity/search/`. Also cover mixed case plus tabs, newlines and leading or trailing spaces, e.g. `' Phil  ADELPHIA\t'` → `philadelphia`. Check `origin === 'https://www.facebook.com'`.
   - **Search parameters:** `searchParams.get('query')` equals the input, including a query with spaces such as `'gsxr 1000'`. `get('exact') === 'false'` and `get('radius') === '65'`.
   - **maxPrice present:** `maxPrice: 6500` gives `get('maxPrice') === '6500'`.
   - **maxPrice absent:** use `it.each` over `null`, `undefined` and `0`, plus one case with the key left out entirely. Each should give `has('maxPrice') === false`.
5. Do not change anything under `tracker-api/src`, and do not change the existing tests or config.

### Files
- `tracker-api/tests/services/facebook-scraper.test.ts`: **new**. Regression tests for `buildMarketplaceSearchUrl`. This is the only file that should be created or changed.

### Risks
- **Prisma loading through an import:** `ListingInput` is only used as a type, so Vitest probably drops that import, but this isn't guaranteed. The prisma mock in step 2 covers it either way.
- **Negative `maxPrice`:** the source uses `if (params.maxPrice)`, so a negative number would be included. The criteria only cover positive values and null/undefined/0, so the tests should not assert anything about negatives. That would lock in or contradict behaviour nobody has specified.
- **Test count:** the "existing 15 tests" figure comes from the task, and I did not check it. The Tester should confirm the baseline count before and after.
- **Missing dependencies:** if `tracker-api/node_modules` is not installed (vitest, playwright), the test run will fail for reasons unrelated to this change. Run `npm --prefix tracker-api ci` or `npm --prefix tracker-api install` first if needed.

### Acceptance Check
- `tracker-api/tests/services/facebook-scraper.test.ts` exists.
- `npm --prefix tracker-api test` exits 0. The output should show the 2 existing store test files and the new file all passing, with 15 + N tests in total.
- Each criterion has at least one named test: slug lowercased and whitespace removed; query, exact=false and radius present; positive maxPrice included; maxPrice omitted for null, undefined and 0.
- `git status --porcelain tracker-api/src` and `git diff --name-only -- tracker-api/src` both print nothing. The only new path is under `tracker-api/tests/services/`.
- Optional check that the tests can fail: temporarily change the function locally, for example by removing `.toLowerCase()`, and confirm the slug test fails. Then revert the change and do not commit it.