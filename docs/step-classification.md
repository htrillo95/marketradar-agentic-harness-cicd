# Step Classification

This document is updated after every calibration cycle. Steps that cross the stability threshold are promoted to candidate status. A step that has been a candidate for more than two calibration cycles without meeting all four signals is reviewed for re-scoping.

## Step: JSON schema validation

- Status: Converted to deterministic (ADR-001, 2026-06-06).
- Stability: harness scores 14/16, 15/16, 14/16 across the last three calibration runs. Consistent.
- Repeatability: three runs on the same holdout input produced identical output.
- Specifiability: validate the output document against `schemas/handoff.json` and reject it if any required field is missing, null, or empty.
- Run rate: every workflow invocation.
- Preserved edge case: present-but-empty required fields are rejected by `scripts/validate_handoff_deterministic.py` and covered by `eval/test_deterministic_step.py`.
- Recommendation: converted.

## Step: Planner

- Recommendation: not a candidate. Needs judgment and synthesis to break a task into subtasks; output is not specifiable in advance.
- Next review: 2026-09-04.

## Step: Release note draft

- Recommendation: weak candidate. Output is prose that needs human review and is not yet repeatable across runs.
- Candidate since: 2026-06-04. Next review: 2026-07-04.

## Step: Path-based routing

- Recommendation: converted to deterministic sample (ADR-002). It uses explicit path rules with a default for unmatched paths.
- Preserved edge case: unmatched paths route to `implementer` instead of failing silently.

## Step: Dependency audit report

- Status: Converted to deterministic (ADR-003, 2026-11-17). Only the report step was converted; the `dependency-auditor` agent still runs the four npm commands.
- Stability: no harness calibration scores exist for this step; determinism is shown by tests (byte-identical output across runs).
- Repeatability: the same inputs produce identical JSON and Markdown.
- Specifiability: the `## Output` section of `.claude/agents/dependency-auditor.md`.
- Run rate: on demand.
- Preserved edge cases: npm error objects, empty or invalid JSON and the npm v6 format are reported as errors, never as zero counts; `npm outdated` `{}` means none outdated; npm's own exit codes are ignored. Covered by `eval/test_deterministic_dependency_audit.py`.
- Recommendation: converted.
