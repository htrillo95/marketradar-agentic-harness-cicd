## Role
tester

## Goal
Add Vitest regression tests for the RunSearchError class (tracker-api/src/services/search-runner.ts) in a new test file. Change nothing else.

## Acceptance Criteria
- A new test file exists at tracker-api/tests/services/search-runner.test.ts, mirroring the src path (project decision-002). It mocks '../../src/lib/prisma' the same way tracker-api/tests/services/facebook-scraper.test.ts does and imports RunSearchError from '../../src/services/search-runner'.
- new RunSearchError('boom') is an instance of both RunSearchError and Error, has name === 'RunSearchError' and message === 'boom'.
- new RunSearchError('boom', { cause }) keeps the same cause object (asserted with toBe).
- new RunSearchError('boom') without options has cause undefined.
- The new file contains exactly three tests and does not call runSavedSearch.
- The Orchestrator's measured changed-file list contains only tracker-api/tests/services/search-runner.test.ts, and no dependency is added.
- `npm --prefix tracker-api test` passes, including all existing tests (currently 40 in 5 files).

## Relevant Context
Command rules for this session (governance D18):
1. Use the Bash tool only to run the tests, with exactly `npm --prefix tracker-api test`, character for character, or with the run-tests skill, which runs the same command. Run it once.
2. Do not add anything to it: no pipes (`|`, for example `| tail`), no redirects (`>`, `2>&1`), no shell operators (`&&`, `;`, `||`, `&`), no `cd`, and no extra arguments or flags.
3. Do not use Bash for anything else, including repository inspection: no `git`, `ls`, `cat`, `find`, `head`, `grep` or similar commands. For any inspection you need, use only the read-only Read, Grep and Glob tools.
4. Do not verify file changes or repository scope yourself. The Orchestrator performs file-scope verification: it measures which files changed and enforces the approved file list. Rely on the changed-file list it gives you.
5. Do not shorten the output. The tool response returns the complete Vitest output, including the `Test Files` and `Tests` summary lines. Read the results from that response.
6. Any other command is denied before it runs and escalates this run to a human, even if the tests pass. Never retry a denied command, in its original or any modified form, and never treat a denial as permission to run a different command.
7. A denial does not withdraw the authorized test command. If you have not run it yet, run `npm --prefix tracker-api test` exactly once, as in rule 1, so your report contains real test results. The run is still escalated.
8. Report every denial accurately: name the denied command and quote the error verbatim under `### Findings`. If the authorized command itself is denied, errors or cannot run, do not work around it: report FAIL, quote the error and stop.

## Approved Files
Changed files (measured by the Orchestrator): `tracker-api/tests/services/search-runner.test.ts`

## Previous Result
### Changes Made
I added a new Vitest file with three regression tests for `RunSearchError`. I haven't run the test suite: running tests is the Tester's job.

- **Setup:** it mocks `../../src/lib/prisma` with `vi.mock(..., () => ({ prisma: {} }))`, the same way `facebook-scraper.test.ts` does. Then it imports only `RunSearchError` from `../../src/services/search-runner`.
- **One `describe('RunSearchError')` block with exactly three `it` tests:**
  1. `new RunSearchError('boom')` is an instance of both `RunSearchError` and `Error`, its `name` is `'RunSearchError'` and its `message` is `'boom'`.
  2. `new RunSearchError('boom', { cause })` keeps the same cause object (`expect(err.cause).toBe(cause)`).
  3. `new RunSearchError('boom')` with no options has `cause` undefined.
- The file never references `runSavedSearch`, and I made no source, config, `package.json` or lockfile changes.

### Files Modified
- `tracker-api/tests/services/search-runner.test.ts` (new)

### Notes
- **Retrieval:** it worked and returned two `internal` lessons about keeping backend logic reusable across clients. Neither applied to this task.
- **Confirmed before writing:** I read `search-runner.ts` to check how `RunSearchError` is built. It passes `options` through to `super(message, options)` and sets `this.name`. I also checked that the test file didn't already exist.
- **Lesson stored:** I saved it under key `lesson/MR-CAP-005`. It's internal material only: the Prisma mock is all you need to import tracker-api services in tests.
- **Expected result:** the Tester should see 43 tests in 6 files from `npm --prefix tracker-api test`, with 3 new tests in this file.

## Expected Output
The `### Result` (PASS or FAIL), `### Validation Performed`, `### Findings` and `### Failed Acceptance Criteria` sections from your definition.
