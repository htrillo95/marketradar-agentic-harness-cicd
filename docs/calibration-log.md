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

No before-and-after improvement can be stated for this conversion: the agent's runtime, token usage and output were not measured, so there is no baseline to compare against.
