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