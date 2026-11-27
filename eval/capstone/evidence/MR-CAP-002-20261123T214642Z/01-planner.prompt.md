## Role
planner

## Goal
Make getConnectionService (tracker-api/src/services/connections/index.ts) return null for any providerId that is not a registered provider, including names inherited from Object.prototype, and add Vitest regression tests for the connection-service registry. Change nothing else.

## Acceptance Criteria
- getConnectionService returns null for any providerId that is not an own key of the registry, including the inherited names toString, constructor, __proto__ and hasOwnProperty, an unknown provider such as craigslist, and the empty string.
- getConnectionService('facebook') returns facebookConnectionService, and listConnectionServices() returns exactly [facebookConnectionService].
- A new test file exists at tracker-api/tests/services/connections/index.test.ts, mirroring the src path (project decision-002), covering the two criteria above. The tests do not call getConnection, connect, reconnect or disconnect, so they do not touch the file system.
- The only application source change is in tracker-api/src/services/connections/index.ts. No route, type or other src file is modified, and no dependency is added.
- `npm --prefix tracker-api test` passes, including the existing 27 tests.

## Relevant Context
getConnectionService currently checks `providerId in services`, which is also true for names inherited from Object.prototype, so GET /connections/toString reaches service.getConnection on a non-service value and returns 500 instead of the route's existing 404 (tracker-api/src/routes/connections.ts). tsconfig targets ES2022, so Object.hasOwn is available. tracker-api uses Vitest 3 (`npm test` runs `vitest run`); existing tests live in tracker-api/tests/store/ and tracker-api/tests/services/. This run uses the opt-in Code Reviewer stage (`plan --review`). Not a frozen holdout task.

## Approved Files
None. You do not modify files. Name every file the Implementer should create or modify under `### Files` as a repository-relative path in backticks.

## Previous Result
None.

## Expected Output
The `### Plan`, `### Files`, `### Risks` and `### Acceptance Check` sections from your definition.
