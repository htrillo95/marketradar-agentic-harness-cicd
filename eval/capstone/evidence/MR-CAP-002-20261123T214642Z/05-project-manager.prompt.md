Task identifier: MR-CAP-002

Implementation summary:
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

Code review outcome: APPROVE (Code Reviewer attempt 1; explicit verdict APPROVE; 0 High-severity findings).

Code review report:
I found no issues in the two changed files. The fix and the new tests meet every acceptance criterion.

**Retrieval:** `mcp__retrieval__retrieve` ran but returned nothing, so no earlier lessons or standards were used.

### Findings

None.

### What I checked
- **The fix** (`tracker-api/src/services/connections/index.ts:20`): `Object.hasOwn(services, providerId)` only matches keys the registry itself defines. `toString`, `constructor`, `__proto__`, `hasOwnProperty`, `craigslist` and `''` all return `null`, and `'facebook'` still returns `facebookConnectionService`. The registry, `listConnectionServices`, the exports and the return type are unchanged. `tsconfig.json` sets `lib` to `ES2022`, so `Object.hasOwn` type-checks. The `as ProviderId` cast is still needed because `Object.hasOwn` doesn't narrow the type, and it's harmless.
- **The tests** (`tracker-api/tests/services/connections/index.test.ts`):
  - The file mirrors the src path, as decision-002 requires.
  - The `../../../src/...` imports point to the right place.
  - The facebook test and the list test check object identity with `toBe`, not just matching shape.
  - `it.each` covers all six ids that must return `null`.
  - Nothing calls `getConnection`, `connect`, `reconnect` or `disconnect`. When `facebook.ts` loads it only runs `path.join`, so the tests never touch the file system and need no mocking.
- **Test discovery:** there's no `vitest.config.*`, so Vitest's default pattern will pick up the nested file. `tsconfig` only includes `src`, so it doesn't type-check the test files. Existing tests work the same way.

### Observations (not defects)
- I couldn't run `git diff` or the test suite, so I'm relying on the Orchestrator's list of changed files for scope. The Tester should confirm the count goes up from 27 to 35 tests and that nothing fails. The optional revert check would also prove the inherited-name cases catch the old `in` guard.
- The Implementer says it "saved the lesson under `lesson/MR-CAP-002`". That write is outside the reviewed files and I couldn't check it. Per the memory policy, its classification (public or internal) should be confirmed.

### Overall assessment
This is a small, correct fix with focused regression tests. The only application source change is in `index.ts`, and no dependency was added.

### Verdict
APPROVE

Test results (Tester attempt 1, PASS):
### Result
**PASS**

### Validation Performed
- **Retrieval:** I called `mcp__retrieval__retrieve` with a query about the connection-service registry and the test file. It returned two internal lessons (authentication vs scrape access, and shared backend clients). Neither applies to this task.
- **Files read:**
  - `tracker-api/src/services/connections/index.ts` (the change)
  - `tracker-api/tests/services/connections/index.test.ts` (new)
  - `tracker-api/src/services/connections/facebook.ts`, to check what runs when it loads
  - `tracker-api/package.json` and `tracker-api/tsconfig.json`
- **Search:** no `vitest.config.*` or `vite.config.*` exists in `tracker-api/`, so Vitest uses its default test pattern.
- **Test run:** `npm --prefix tracker-api test`
  - 4 test files passed out of 4, and 35 tests passed out of 35.
  - `tests/services/connections/index.test.ts` added 8 tests: 1 for facebook, 6 for the unknown names, and 1 for `listConnectionServices`.
  - The other 27 tests still pass: listings 3, searches 12, facebook-scraper 12.

### Findings
- **Criterion 1 (null for unregistered names):** met. `getConnectionService` now uses `Object.hasOwn(services, providerId)`, so it returns `null` for any name the registry doesn't define itself. The `it.each` test covers `toString`, `constructor`, `__proto__`, `hasOwnProperty`, `craigslist` and `''`, and all six pass with `toBeNull()`.
- **Criterion 2 (facebook and the list):** met. `getConnectionService('facebook')` returns the same object as `facebookConnectionService`. `listConnectionServices()` equals `[facebookConnectionService]`, has length 1, and its first item is that same object.
- **Criterion 3 (test file):** met. The file is at the path that mirrors src, as decision-002 requires. It never calls `getConnection`, `connect`, `reconnect` or `disconnect`, and uses no `vi.mock`. Loading `facebook.ts` only builds a path with `path.join`; the file system is read only inside `resolveConnection`, which these tests never reach.
- **Criterion 4 (scope):** met as far as I could check. The registry, `listConnectionServices`, the exports and the return type are unchanged. `package.json` has no new dependencies. I couldn't run git in this session (only the test command is allowed), so I relied on the Orchestrator's list of two changed files for the claim that nothing else changed.
- **Criterion 5 (test suite):** met. All 35 tests pass, including the original 27.
- **Not checked, low risk:** Vitest doesn't type-check, and `tsc` isn't allowed in this session, so the TypeScript build is unconfirmed. `tsconfig.json` sets `lib: ["ES2022"]`, which includes `Object.hasOwn`, so it should compile. `tsconfig` only covers `src`, so the test file isn't type-checked by the build at all.

### Failed Acceptance Criteria
None.

Requested final status: COMPLETE

Record the outcome with key `task-outcome/MR-CAP-002`.