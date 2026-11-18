# ADR-003: Convert dependency-auditor report step to deterministic code

## Status

Proposed

Acceptance is pending the integrated end-to-end regression check required by Module 4.3: at least two holdout tasks and two development tasks run through the complete orchestration after this conversion, with evaluation and policy results recorded in `docs/calibration-log.md`. No such check has been recorded yet; a successful GitHub Actions run does not substitute for it.

## Context

The `dependency-auditor` agent runs only four fixed commands: `npm audit --json` and `npm outdated --json`, each with `--prefix tracker-api` or `--prefix tracker-client`. The PreToolUse hook `scripts/enforce_bash_scope.py` denies every other Bash command from this role. The agent then reformats the JSON these commands return into the report specified by the "## Output" section of `.claude/agents/dependency-auditor.md`. Parsing and formatting that JSON is structural and needs no judgment.

## Decision

Move the parsing and formatting to `scripts/audit_dependencies_deterministic.py`. The script uses the standard library only and runs no subprocess, network call or npm command. Its inputs are the saved outputs of the four allowlisted commands, plus optional lockfiles. It writes a JSON report and a Markdown report.

The agent stays, unchanged in tools, permissions and Bash hook, as the governed way to run the four commands live. Its "## Output" section is the specification the script must match; a test parses those headings and checks that the Markdown report contains each of them.

`scripts/run-dependency-audit.sh` is the human-run report path, invoked as `bash scripts/run-dependency-audit.sh [OUT_DIR]` (OUT_DIR defaults to a new temporary directory). It runs the four allowlisted commands, saves their output to OUT_DIR (by default a new temporary directory outside the repository), and passes it to the script. npm failures (missing npm, no output, npm error objects) reach the script as unreadable or error inputs and are reported as errors. The agent's tools, permissions and Bash hook are unchanged, because the wrapper runs outside the agent.

## Alternatives considered

- Keep the agent doing everything. Rejected. The report step is structural reformatting of machine-readable JSON.
- Let the agent run the script. Rejected. It would widen the agent's Bash grant beyond the four commands.
- Have the script run npm itself. Rejected. Results would depend on the network and the registry and could not be reproduced offline. The reporting script stays pure; npm runs only in the separate wrapper `scripts/run-dependency-audit.sh`.
- Retire the agent. Rejected. The governance policy and its tests require the `dependency-auditor` role.

## Consequences

- The report is deterministic and can be tested offline.
- Unreadable or error inputs are reported as errors and never shown as zero counts; the script exits 1 on any such input and 0 otherwise.
- Inputs are produced by `scripts/run-dependency-audit.sh`, run by a human; the agent cannot redirect command output to a file, and the Orchestrator's Bash is not granted by the policy (D14).
- Prioritising or explaining findings is no longer done by a model; recommendations are one templated line per finding and still require human approval.
- The fixtures are hand-written. Real npm 10/11 output should be checked against the script before relying on it.
- The installed version of a vulnerable package needs a lockfile; without one it is reported as "unknown" with the vulnerable range.

## Related decisions

Follows ADR-001 and ADR-002. Unlike those, only one step of the role is converted; the agent is kept for running the commands.

## Evidence

- Tests: `eval/test_deterministic_dependency_audit.py` (43 tests) with fixtures in `eval/fixtures/dependency-audit/`. Coverage includes npm error objects, empty, invalid and missing input files, `npm outdated` returning `{}` (none outdated), rejection of the npm v6 audit format, exit code 1 on bad input, byte-identical output across two runs, identical output for reordered input, the agent's "## Output" headings, a guard that the script imports no subprocess or network modules, and wrapper tests for `scripts/run-dependency-audit.sh` using a fake npm (success, npm failure without output, npm error object, npm missing, the exact four commands, writes only inside the output directory, and no mutating npm commands).
- CI: since commit 4e7aa36, these tests run in the gating `eval-gate` deterministic step of `.github/workflows/ci.yml`. The local run of the four deterministic test files gave 58 passed.
- Real npm verification: on 2026-11-17 the repository owner ran `bash scripts/run-dependency-audit.sh` against real npm output; all four commands produced parseable results, both lockfiles loaded, and the JSON and Markdown reports were generated without an AI model. Recorded in `docs/calibration-log.md` ("Real npm verification (human-run wrapper)").
- No cost or latency measurements were taken for this conversion.
- No baseline measurements of the original agent (runtime, token usage, or a direct output comparison) were collected, so no before-and-after improvement is claimed.
- Integrated end-to-end regression check: pending (see Status).
