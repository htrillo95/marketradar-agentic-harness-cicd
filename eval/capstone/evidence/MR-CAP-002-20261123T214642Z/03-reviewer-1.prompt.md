## Role
code reviewer

## Goal
Make getConnectionService (tracker-api/src/services/connections/index.ts) return null for any providerId that is not a registered provider, including names inherited from Object.prototype, and add Vitest regression tests for the connection-service registry. Change nothing else.

## Acceptance Criteria
- getConnectionService returns null for any providerId that is not an own key of the registry, including the inherited names toString, constructor, __proto__ and hasOwnProperty, an unknown provider such as craigslist, and the empty string.
- getConnectionService('facebook') returns facebookConnectionService, and listConnectionServices() returns exactly [facebookConnectionService].
- A new test file exists at tracker-api/tests/services/connections/index.test.ts, mirroring the src path (project decision-002), covering the two criteria above. The tests do not call getConnection, connect, reconnect or disconnect, so they do not touch the file system.
- The only application source change is in tracker-api/src/services/connections/index.ts. No route, type or other src file is modified, and no dependency is added.
- `npm --prefix tracker-api test` passes, including the existing 27 tests.

## Relevant Context
Task identifier: MR-CAP-002

Approved plan:

### Plan
Retrieval note: `mcp__retrieval__retrieve` ran but returned nothing, so no earlier lessons were used. I continued from the repository context.

1. **Fix the lookup** in `tracker-api/src/services/connections/index.ts`. Change the guard in `getConnectionService` from `providerId in services` to `Object.hasOwn(services, providerId)`. Keep the same return shape: `services[providerId as ProviderId]` or `null`. Don't change the `services` registry, `listConnectionServices` or the exports. ES2022 is the target, so `Object.hasOwn` type-checks without adding a lib.
2. **Add a regression test file** at `tracker-api/tests/services/connections/index.test.ts`. This mirrors the src path (decision-002) and follows the import style in `tests/services/facebook-scraper.test.ts`:
   - Import `describe, expect, it` from `vitest`.
   - Import `getConnectionService` and `listConnectionServices` from `../../../src/services/connections`.
   - Import `facebookConnectionService` from `../../../src/services/connections/facebook`.
   - No `vi.mock` is needed. `facebook.ts` only imports `fs`/`path` and calls `path.join` when loaded. It does no file-system I/O and no Prisma.
3. **Write the test cases:**
   - `getConnectionService('facebook')` returns `facebookConnectionService` (`toBe`).
   - Use `it.each` over `['toString', 'constructor', '__proto__', 'hasOwnProperty', 'craigslist', '']`, asserting `toBeNull()` for each.
   - `listConnectionServices()` `toEqual([facebookConnectionService])`, plus `toHaveLength(1)` and `[0]` `toBe(facebookConnectionService)`, so the check is identity and not just deep equality.
   - Don't call `getConnection`, `connect`, `reconnect` or `disconnect` anywhere.
4. Change nothing else: no routes, types, other src files or package.json.

### Files
- `tracker-api/src/services/connections/index.ts`: switch to an own-key check with `Object.hasOwn` so names inherited from `Object.prototype` return `null`. This is the only application source change.
- `tracker-api/tests/services/connections/index.test.ts` (new): Vitest regression tests for the registry lookup and listing.

### Risks
- **`__proto__` behaviour:** `services` is an object literal, so `'__proto__' in services` is true, but `Object.hasOwn(services, '__proto__')` is false. The fix handles it. A test makes sure it stays that way.
- **Equality check:** `toEqual` on objects with function properties could pass for a structurally similar object. That's why the identity assertions (`toBe`) are added alongside it.
- **Relative import depth:** the test is three levels deep (`tests/services/connections/`), so imports need `../../../src/...`. A wrong path fails at collection time.
- **Vitest discovery:** the default include pattern should pick up the new nested file. I didn't open a vitest config to confirm there's no custom `include` that would exclude it. The Implementer or Tester should check the test count goes up.
- **Typing:** `providerId as ProviderId` is still needed after the `hasOwn` guard, because `Object.hasOwn` doesn't narrow types. That's harmless.

### Acceptance Check
- `npm --prefix tracker-api test` passes: the existing 27 tests plus the new ones, all green.
- Revert check (optional, Tester only): with the old `in` guard, the inherited-name cases (`toString`, `constructor`, `__proto__`, `hasOwnProperty`) fail. This shows the tests really cover the bug.
- `git diff --stat` shows changes only in `tracker-api/src/services/connections/index.ts` and the new test file. No changes to `package.json` or the lockfile.
- Grep the new test file for `getConnection(`, `connect(`, `reconnect(` and `disconnect(`. There should be no matches.
- Optional: run `tsc --noEmit` in tracker-api to confirm `Object.hasOwn` type-checks.

## Approved Files
Changed files (measured by the Orchestrator): `tracker-api/src/services/connections/index.ts`, `tracker-api/tests/services/connections/index.test.ts`

Review these changes. You do not modify any file.

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
Your findings, each with a `Severity: High`, `Severity: Medium` or `Severity: Low` line, the file and location, what is wrong, why it matters and a short fix; your overall assessment; and a final `### Verdict` section containing exactly APPROVE or REJECT. Any High-severity finding means REJECT.
