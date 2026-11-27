## Role
tester

## Goal
Make getConnectionService (tracker-api/src/services/connections/index.ts) return null for any providerId that is not a registered provider, including names inherited from Object.prototype, and add Vitest regression tests for the connection-service registry. Change nothing else.

## Acceptance Criteria
- getConnectionService returns null for any providerId that is not an own key of the registry, including the inherited names toString, constructor, __proto__ and hasOwnProperty, an unknown provider such as craigslist, and the empty string.
- getConnectionService('facebook') returns facebookConnectionService, and listConnectionServices() returns exactly [facebookConnectionService].
- A new test file exists at tracker-api/tests/services/connections/index.test.ts, mirroring the src path (project decision-002), covering the two criteria above. The tests do not call getConnection, connect, reconnect or disconnect, so they do not touch the file system.
- The only application source change is in tracker-api/src/services/connections/index.ts. No route, type or other src file is modified, and no dependency is added.
- `npm --prefix tracker-api test` passes, including the existing 27 tests.

## Relevant Context
Run the tests with exactly `npm --prefix tracker-api test` (or the run-tests skill); other commands are not permitted in this session.

## Approved Files
Changed files (measured by the Orchestrator): `tracker-api/src/services/connections/index.ts`, `tracker-api/tests/services/connections/index.test.ts`

## Previous Result
I changed the provider lookup and added the regression tests. I didn't run the test suite, because test runs are the Tester's job.

### Changes Made
- **Fixed the lookup** in `tracker-api/src/services/connections/index.ts`. `getConnectionService` now checks `Object.hasOwn(services, providerId)` instead of `providerId in services`. Names inherited from `Object.prototype` (such as `toString` and `__proto__`) now return `null`. The return value, the `services` registry, `listConnectionServices` and the exports are unchanged.
- **Added `tracker-api/tests/services/connections/index.test.ts`** (new file), which mirrors the src path as decision-002 requires:
  - `getConnectionService('facebook')` returns the same object as `facebookConnectionService` (`toBe`).
  - `it.each` over `toString`, `constructor`, `__proto__`, `hasOwnProperty`, `craigslist` and `''` checks that each returns `toBeNull()`.
  - `listConnectionServices()` equals `[facebookConnectionService]`, has length 1, and its first item is the same object (`toBe`).
  - The tests never call `getConnection`, `connect`, `reconnect` or `disconnect`, and use no `vi.mock`.

### Files Modified
- `tracker-api/src/services/connections/index.ts` (modified)
- `tracker-api/tests/services/connections/index.test.ts` (created)

### Notes
- Retrieval ran but found no earlier lessons, so I worked from the plan and the repository.
- `tracker-api/tsconfig.json` sets both target and lib to ES2022, so `Object.hasOwn` should type-check. I didn't run `tsc` to confirm.
- There is no `vitest.config.*` in `tracker-api/`, so Vitest's default include pattern should pick up the new nested test file. The Tester should check that the count goes up from the existing 27 tests (about 8 new ones are expected).
- `facebook.ts` only calls `path.join` when it loads and does no file I/O or Prisma access, so the tests need no mocking.
- I saved the lesson under `lesson/MR-CAP-002`.
- No other files, routes, types or dependencies were changed.

## Expected Output
The `### Result` (PASS or FAIL), `### Validation Performed`, `### Findings` and `### Failed Acceptance Criteria` sections from your definition.
