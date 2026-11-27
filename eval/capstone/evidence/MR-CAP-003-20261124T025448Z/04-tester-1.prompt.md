## Role
tester

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
Run the tests with exactly `npm --prefix tracker-api test` (or the run-tests skill); other commands are not permitted in this session.

## Approved Files
Changed files (measured by the Orchestrator): `tracker-api/tests/routes/health.test.ts`

## Previous Result
### Changes Made
I added `tracker-api/tests/routes/health.test.ts`, which adds two regression tests for the health router. I haven't run them, so they're unverified until the Tester runs `npm --prefix tracker-api test`. The file matches the existing test style (single quotes, no semicolons, imports without file extensions).

- **Setup:** `beforeAll` mounts the default-exported router from `'../../src/routes/health'` on a fresh `express()` app. It listens on `127.0.0.1` port 0 and waits for the `listening` event (an `error` event fails the setup instead). It then builds `baseUrl` from the assigned port.
- **Teardown:** `afterAll` does nothing if the server never started. Otherwise it calls `closeAllConnections?.()` and then waits for `server.close()`, rejecting if close returns an error.
- **Test 1:** `GET /` using the global `fetch` should return status 200, a content type matching `/application\/json/`, and a body equal to `{ status: 'ok' }`.
- **Test 2:** `POST /` should return 404. The test checks the status only and reads the body so the connection is freed.
- The file does not import `src/app.ts` or Prisma.

### Files Modified
- `tracker-api/tests/routes/health.test.ts` (new; this also creates the `tests/routes/` folder)

### Notes
- **Prior lessons:** the lesson search worked but found nothing relevant (it returned lessons about keeping backend logic separate from the web client).
- **Scope:** nothing under `tracker-api/src`, `package.json` or the lockfile changed, and I didn't add a vitest config. The Orchestrator or Code Reviewer should confirm this with `git status`, since the Tester may only run the test command.
- **Expected result:** 37 tests (the existing 35 plus these 2). Test files aren't type-checked by `tsc`, but the new file is written with strict types anyway.
- **Lesson saved:** stored at `lesson/MR-CAP-003`. It says to mount a router on a fresh Express app to test it without loading Prisma, and to close open connections before shutting the server down so teardown doesn't hang.

## Expected Output
The `### Result` (PASS or FAIL), `### Validation Performed`, `### Findings` and `### Failed Acceptance Criteria` sections from your definition.
