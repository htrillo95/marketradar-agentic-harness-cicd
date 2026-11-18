# Calibration Log

## Near-miss patterns for Module 4 governance

1. The implementer subagent nearly called `delete_entry` on the storage server because its tool grant was temporarily too broad.  
   **Risk:** an implementer with delete access can silently remove project state that other subagents depend on.

2. A reviewer subagent's task output nearly triggered the `run-tests` skill, which is meant for the implementer and tester.  
   **Risk:** a read-only review role that can run tests can change the state of the workspace it is only supposed to inspect.

3. An implementer requested a document tagged `confidential`, above its `internal` ceiling, and the retrieval server refused it.  
   **Risk:** a role that can reach above its classification ceiling can pull sensitive material into a context it was not cleared for.

## Conversion measurements

### Before conversion — handoff validation agent

- Average cycle time: 45 seconds across three runs.
- Token cost: $0.003 per run.
- Deterministic harness checks: 7/7 passing.
- Review latency: about 30 seconds.

### After conversion — deterministic handoff validator in isolation

- Average cycle time: 0.2 seconds across three runs.
- Token cost: $0.
- Deterministic harness checks: 7/7 passing.
- Output variance: zero; `diff` reported no differences across three runs.
- Review latency: about 5 seconds.

### Integrated end-to-end regression check

- Date: 2026-06-06
- Result: 24/24 harness checks passing; no regressions.
- Policy suite: passing.
- Evidence: deterministic validator is wired into orchestration and governance artifacts are in sync.

## Dependency-auditor report step (ADR-003)

Only the report step was converted: `scripts/audit_dependencies_deterministic.py` parses and formats saved outputs of the four allowlisted npm commands. The `dependency-auditor` agent still exists and still runs those commands.

### Before conversion — dependency-auditor agent

- Cycle time: not measured.
- Token usage and cost: not measured.
- Harness checks: none recorded; the step had no calibration runs before conversion.
- Direct output comparison (agent report vs script report for the same npm inputs): not performed.

### After conversion — deterministic report script in isolation

- Dependency-audit tests: `eval/test_deterministic_dependency_audit.py`, 36 passing.
- Deterministic test files together (`eval/test_deterministic.py`, `eval/test_deterministic_router.py`, `eval/test_deterministic_step.py`, `eval/test_deterministic_dependency_audit.py`): 51 passing.
- Output variance: zero; repeated runs on the same inputs produced byte-identical JSON and Markdown reports (covered by `test_cli_two_runs_are_byte_identical` and `test_shuffled_input_gives_identical_output`).
- AI token cost: $0. The script makes no model, network or subprocess calls.
- Cycle time: not measured.
- Inputs: hand-written fixtures in `eval/fixtures/dependency-audit/`; not yet checked against real npm 10/11 output.

### Integrated CI check

- GitHub Actions run #12 for commit `5cbf331` completed successfully in 10m 54s (reported by the repository owner from GitHub; not inspected from the development environment). The run includes the dependency-audit tests in the gating `eval-gate` deterministic step.

### Real npm verification (human-run wrapper)

Reported by the repository owner (run outside the development environment; not re-run here):

- Date: 2026-11-17.
- Command: `bash scripts/run-dependency-audit.sh`, which ran the four allowlisted npm commands and passed their output to `scripts/audit_dependencies_deterministic.py`. The run completed successfully and produced the JSON and Markdown reports.
- All four npm commands produced parseable results, and both `package-lock.json` files loaded successfully.
- tracker-api: 14 vulnerable packages, 8 outdated.
- tracker-client: 22 vulnerable packages, 15 outdated.
- Total: 36 vulnerable packages, 23 outdated.
- No AI model was used to generate the report.

This verifies the deterministic report workflow against real npm output rather than only the hand-written fixtures. It does not establish a measured speed or cost improvement over the original agent, whose runtime and token usage were not measured.

No before-and-after improvement can be stated for this conversion: the agent's runtime, token usage and output were not measured, so there is no baseline to compare against.

## Module 4.3 — End-to-End Regression Status

- Implementation: the deterministic dependency-audit report step is complete: `scripts/audit_dependencies_deterministic.py` and the human-run wrapper `scripts/run-dependency-audit.sh` (ADR-003).
- Passed, per existing recorded evidence:
  - Unit tests: `eval/test_deterministic_dependency_audit.py`, recorded in ADR-003 as 43 tests; the four deterministic test files are recorded there as 58 passed. The "After conversion" entry above records the earlier counts (36 and 51) from before the wrapper tests were added.
  - Policy tests and CI: GitHub Actions run #12 for commit `5cbf331` completed successfully (see "Integrated CI check" above, reported by the repository owner). In `.github/workflows/ci.yml`, the Policy Test Suite (`policy-gate`) is a gating job in that workflow.
  - Real npm verification: the human-run wrapper on 2026-11-17 (see "Real npm verification (human-run wrapper)" above).
- Not completed: the full orchestration regression (at least two holdout tasks and two development tasks run through the complete orchestration after the conversion). No such run has been recorded.
- Blockers in the existing Module 3 orchestration:
  - No reliable end-to-end entry point: orchestration is an interactive Orchestrator session, and roles are otherwise launched one at a time (`claude --agent <role> … -p`, or `scripts/run-agent.sh <role>`).
  - Limited harness coverage: `eval/module3/test_deterministic.py` accepts only the Tester-only lint route (`DEV-LINT-001`), and `eval/module3/module-3.3-evaluation-harness/` has an evaluator only for HO-01. HO-01 and HO-02 were already exercised in Module 3 (`eval/module3/README.md`).
  - Unresolved MCP integration: the agent definitions use Module 3 MCP argument shapes that do not match the Module 4 servers (policy gap D2 in `docs/governance-policy.md`).
  - No orchestrated task routes to `dependency-auditor`, so the regression tasks would not exercise the converted step itself.
- Baseline: no measurements of the original `dependency-auditor` agent exist (runtime, token usage, or output comparison); see "Before conversion — dependency-auditor agent" above.
- ADR-003 remains Proposed pending regression evidence (`docs/adr/ADR-003-dependency-audit-deterministic-conversion.md`).
