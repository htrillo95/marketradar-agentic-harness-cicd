## Role
code reviewer

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
Task identifier: MR-CAP-003

Approved plan:

### Plan
1. Create `tracker-api/tests/routes/health.test.ts`. This also creates the new `tests/routes/` folder, which mirrors `src/routes/health.ts` as decision-002 requires.
2. Imports: `describe`, `it`, `expect`, `beforeAll`, `afterAll` from `vitest`; `express` from `express`; `type { Server }` from `node:http`; `type { AddressInfo }` from `node:net`; and the default router from `'../../src/routes/health'`. Leave off the file extension, as the existing tests do (e.g. `'../../src/store/searches'`). Do not import `src/app.ts` or anything that loads Prisma.
3. In `beforeAll`:
   - Create a fresh `const app = express()` and call `app.use(healthRouter)` so the router is mounted at the root.
   - Start the server on port 0 and host 127.0.0.1, and wait for the `listening` event: `await new Promise<void>((resolve, reject) => { server = app.listen(0, '127.0.0.1', () => resolve()); server.once('error', reject) })`.
   - Read the port from `(server.address() as AddressInfo).port` and build `baseUrl = http://127.0.0.1:${port}`.
4. In `afterAll`, close the server and wait for it, but only if it started: `await new Promise<void>((resolve, reject) => server ? server.close(err => err ? reject(err) : resolve()) : resolve())`. Optionally call `server.closeAllConnections?.()` first so keep-alive sockets from `fetch` can't make the close hang.
5. Test 1: `GET ${baseUrl}/` using the global `fetch`. Check:
   - `res.status` is 200
   - `res.headers.get('content-type')` matches `/application\/json/`
   - `await res.json()` equals `{ status: 'ok' }` (use `toEqual`)
6. Test 2: `fetch(`${baseUrl}/`, { method: 'POST' })`. Check that `res.status` is 404. Read the body (`await res.text()`) so the socket is released.
7. Make no other changes: nothing under `tracker-api/src`, no edits to `package.json` or the lockfile, and no vitest config.

### Files
- `tracker-api/tests/routes/health.test.ts`: **new**. The regression tests for the health router, mounted on its own Express app.

No other files should be created or modified.

### Risks
- **Retrieval:** the lesson search worked but found nothing relevant (it returned lessons about scraping and shared backend logic). The plan uses only the repository files I read.
- **Server not closing:** Node's `fetch` (undici) keeps connections alive, so `server.close()` can wait on them and time out the hook. Calling `closeAllConnections()` (Node ≥ 18.2) before closing, and reading every response body, prevents this.
- **Type-checking:** `tsconfig.json` only includes `src`, so `tsc` doesn't check test files. Vitest (esbuild) strips types without checking them, so a type mistake won't fail the run, but the Implementer should still write strictly typed code. `fetch` types come from `@types/node` 24, so no new dependency or `lib` change is needed.
- **Express 5's 404:** unmatched requests fall through to Express's default final handler, which returns 404 with HTML. The test should check only the status, not the body or content type.
- **Test count:** the suite should go from 35 to 37 tests. If the existing 35 don't currently pass in this environment, that is a separate problem the Tester should report, not something this task fixes.
- **D18 limit:** the Tester may run only `npm --prefix tracker-api test`, so all verification evidence has to come from that one command.

### Acceptance Check
- `npm --prefix tracker-api test` passes: the existing 35 tests plus 2 new ones in `tests/routes/health.test.ts`, with no hang or hook timeout on teardown.
- Reviewing the test file confirms:
  - it imports the default router from `src/routes/health` and does not import `app.ts`
  - it uses `express()`, `listen(0, '127.0.0.1')`, and closes the server in `afterAll`
  - it uses the global `fetch`
  - it asserts 200, a JSON content type and `{ status: 'ok' }` for GET /, and 404 for POST /
- `git status` / `git diff --stat` shows only `tracker-api/tests/routes/health.test.ts` as new, with no changes under `tracker-api/src`, `package.json` or `package-lock.json`. The Orchestrator or Code Reviewer should check this, since the Tester may only run the test command.

## Approved Files
Changed files (measured by the Orchestrator): `tracker-api/tests/routes/health.test.ts`

Review these changes. You do not modify any file.

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
Your findings, each with a `Severity: High`, `Severity: Medium` or `Severity: Low` line, the file and location, what is wrong, why it matters and a short fix; your overall assessment; and a final `### Verdict` section containing exactly APPROVE or REJECT. Any High-severity finding means REJECT.
