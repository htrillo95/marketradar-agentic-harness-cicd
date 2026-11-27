### Plan
1. In `tracker-api/tests/services/facebook-scraper.test.ts`, add `ScraperError` to the existing import from `'../../src/services/facebook-scraper'` (lines 7–10). The import will then be `buildMarketplaceSearchUrl, ScraperError, type MarketplaceSearchParams`. Don't touch the `vi.mock` call or any other import.
2. After the closing `})` of `describe('buildMarketplaceSearchUrl', ...)` (line 82), add a new top-level `describe('ScraperError', ...)` block with exactly three `it` tests:
   - **Test 1:** create `new ScraperError('boom')`. Check `toBeInstanceOf(ScraperError)`, `toBeInstanceOf(Error)`, `.name` is `'ScraperError'` and `.message` is `'boom'`.
   - **Test 2:** create `const cause = new Error('root')`, then `new ScraperError('boom', { cause })`. Check `expect(err.cause).toBe(cause)`.
   - **Test 3:** create `new ScraperError('boom')` with no options. Check `expect(err.cause).toBeUndefined()`.
3. Leave every existing test exactly as it is. Don't create any new test file, change anything under `tracker-api/src`, or add any dependency.

### Files
- `tracker-api/tests/services/facebook-scraper.test.ts`: add `ScraperError` to the existing import and add the new three-test `describe('ScraperError', ...)` block after the current block. This is the only file to change.

### Risks
- **Retrieval:** the tool worked, but the five lessons it returned are about whether scraping Facebook is feasible at all, not about tests or error classes. None of them affected this plan.
- **Test counts:** the new block uses plain `it`, not `it.each`, so it adds exactly 3 tests. The expected total is 40 tests in 5 files, up from 37.
- **Test 3 could be fragile:** with ES2022, `super(message, undefined)` does not create a `cause` property, so `.cause` is `undefined`. Use `toBeUndefined()`, not a check that the property is absent, so the test doesn't depend on how the runtime handles it.
- **Import side effects:** the module imports `playwright` and `fs` when it loads. The existing tests already load this module under the current Prisma mock, so no new setup is needed.
- **Type-only import:** keep `MarketplaceSearchParams` as `type MarketplaceSearchParams` when editing the import.

### Acceptance Check
- Run `npm --prefix tracker-api test`. It should pass, with 5 test files and 40 tests (37 existing + 3 new), including `ScraperError` with 3 passing tests.
- `git diff --stat` should show only `tracker-api/tests/services/facebook-scraper.test.ts` changed. There should be no changes under `tracker-api/src`, to `package.json` or to the lockfile, and no new files.
- In the diff, the existing `buildMarketplaceSearchUrl` tests (lines 1–82) should be unchanged apart from the extra name in the import.
- The new block should contain exactly three `it(` calls covering: instance of both classes plus `name` and `message`; `cause` checked with `toBe`; and `cause` undefined when no options are passed.