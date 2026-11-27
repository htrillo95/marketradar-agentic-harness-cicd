## Role
planner

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
ScraperError (tracker-api/src/services/facebook-scraper.ts lines 21-26) extends Error, passes (message, options) to super and sets this.name = 'ScraperError'; the scraper throws it at line 390. No test covers it. tracker-api/tests/services/facebook-scraper.test.ts already mocks '../../src/lib/prisma' and imports buildMarketplaceSearchUrl from the same module, so no new setup is needed. tsconfig targets ES2022, so Error options with cause are supported. tracker-api uses Vitest 3 (`npm test` runs `vitest run`). This is the live validation run for the D18 Tester command rules: the orchestrated Tester may run only `npm --prefix tracker-api test`. This run uses the opt-in Code Reviewer stage (`plan --review`). Not a frozen holdout task.

## Approved Files
None. You do not modify files. Name every file the Implementer should create or modify under `### Files` as a repository-relative path in backticks.

## Previous Result
None.

## Expected Output
The `### Plan`, `### Files`, `### Risks` and `### Acceptance Check` sections from your definition.
