## Role
planner

## Goal
Add Vitest regression tests for buildMarketplaceSearchUrl (exported from tracker-api/src/services/facebook-scraper.ts). Do not change application source code.

## Acceptance Criteria
- A new test file exists at tracker-api/tests/services/facebook-scraper.test.ts, mirroring the src path (project decision-002).
- Tests cover: location slug is lowercased with whitespace removed; query, exact=false and radius appear as URL search parameters; maxPrice is included when it is a positive number; maxPrice is omitted when it is null, undefined or 0.
- `npm --prefix tracker-api test` passes, including the existing 15 tests.
- No file under tracker-api/src is modified.

## Relevant Context
tracker-api uses Vitest 3 (`npm test` runs `vitest run`); existing tests live in tracker-api/tests/store/. Not a frozen holdout task.

## Approved Files
None. You do not modify files. Name every file the Implementer should create or modify under `### Files` as a repository-relative path in backticks.

## Previous Result
None.

## Expected Output
The `### Plan`, `### Files`, `### Risks` and `### Acceptance Check` sections from your definition.
