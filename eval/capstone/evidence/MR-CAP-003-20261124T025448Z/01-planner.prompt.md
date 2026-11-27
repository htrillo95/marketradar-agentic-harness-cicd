## Role
planner

## Goal
Add Vitest regression tests for the health route (tracker-api/src/routes/health.ts). Change nothing else.

## Acceptance Criteria
- A new test file exists at tracker-api/tests/routes/health.test.ts, mirroring the src path (project decision-002).
- The test mounts the default-exported health router on a fresh express() app; it does not import tracker-api/src/app.ts. The app listens on 127.0.0.1 with port 0 and the server is always closed after the tests.
- GET / on the mounted router returns status 200, a JSON content type, and a body equal to { "status": "ok" }.
- A request the router does not handle (POST /) returns 404.
- No file under tracker-api/src is modified and no dependency is added; requests use Node's built-in fetch.
- `npm --prefix tracker-api test` passes, including the existing 35 tests.

## Relevant Context
tracker-api/src/routes/health.ts exports an Express 5 Router with a single GET / handler returning res.json({ status: 'ok' }); src/app.ts mounts it at /health but also loads Prisma and every other router, so the test should not import app.ts. tracker-api uses Vitest 3 (`npm test` runs `vitest run`) with no vitest config, so the default include pattern applies; existing tests live in tracker-api/tests/store/ and tracker-api/tests/services/. Node 22 provides fetch. This is the live validation run for governance item D18: the orchestrated Tester may run only `npm --prefix tracker-api test`. This run uses the opt-in Code Reviewer stage (`plan --review`). Not a frozen holdout task.

## Approved Files
None. You do not modify files. Name every file the Implementer should create or modify under `### Files` as a repository-relative path in backticks.

## Previous Result
None.

## Expected Output
The `### Plan`, `### Files`, `### Risks` and `### Acceptance Check` sections from your definition.
