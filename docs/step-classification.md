# Step Classification

This document is updated after every calibration cycle. Steps that cross the stability threshold are promoted to candidate status. A step that has been a candidate for more than two calibration cycles without meeting all four signals is reviewed for re-scoping.

Review schedule: weak candidates are reviewed monthly; steps that are not currently candidates are reviewed every 3 months; converted steps are re-evaluated when their stated review trigger occurs. Classifications are also reviewed after every calibration cycle.

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
- Next review: 2027-02-18.

## Step: Release note draft

- Recommendation: weak candidate. Output is prose that needs human review and is not yet repeatable across runs.
- Candidate since: 2026-06-04. Next review: 2026-12-18.

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
- Run path: `bash scripts/run-dependency-audit.sh` (human-run) produces the inputs and runs the script; npm failures are reported as errors.
- Review trigger: re-evaluate the deterministic implementation if `npm audit --json` or `npm outdated --json` changes its output format.
- Recommendation: converted.

## Step: Advisory code review

- Recommendation: not a candidate. `marketradar-code-reviewer` in the `advisory-review` CI job makes open-ended judgments on code changes; it stays advisory.
- Next review: 2027-02-18.

## Step: Rubric judge

- Recommendation: not a candidate. The advisory rubric step in `eval-gate` is model-judged quality scoring by design; it stays advisory behind the deterministic pre-check.
- Next review: 2027-02-18.

## Step: Implementer

- Recommendation: not a candidate. Writes code from an approved plan.
- Next review: 2027-02-18.

## Step: Tester

- Recommendation: not a candidate as a whole. Test execution is already deterministic, but judging results against acceptance criteria needs reasoning.
- Next review: 2027-02-18.

## Step: Project manager

- Recommendation: weak candidate. The outcome rule (never complete if review or testing failed) is rule-based, but the step depends on storage MCP calls that do not match the Module 4 server (policy gap D2).
- Next review: 2026-12-18.

## Step: Dependency audit command execution

- Recommendation: retained. The `dependency-auditor` agent runs four fixed commands, and `scripts/run-dependency-audit.sh` runs them for the report path; the agent remains the governed fallback because the policy and its tests require the role.
- Next review: 2027-02-18.

## Step: Project Manager task-outcome reporting (Module 4.3 Exercise 1)

- Description: the final step of the full orchestration path (Planner -> Implementer -> Code Reviewer -> Tester -> Project Manager; `docs/module3/holdout-task-set.md` line 23, HO-02). It receives the task identifier, implementation summary, code review outcome, test results and requested final status as free text from the Orchestrator, decides the task outcome, and returns `### Task Outcome`, `### Evidence`, `### Storage Record` and `### Blockers` (`.claude/agents/project-manager.md` lines 14–17, 28, 30–53). This entry covers the outcome decision and report only, not the storage write.
- Stability: insufficient evidence. Zero recorded runs: in HO-02, the only holdout that routes to this step, "The Project Manager never ran" (`docs/module3/calibration-log.md` line 308).
- Repeatability: insufficient evidence. No repeated runs exist.
- Specifiability: partial. The completion rule ("Never mark a task complete if review or testing failed", `.claude/agents/project-manager.md` line 17) and the output headings (lines 43–53) are defined, but interpreting free-text evidence ("Determine the task outcome from the evidence", line 16) and detecting contradictory evidence (line 28) require judgment.
- Run rate: low. Zero recorded executions; HO-02 is the only holdout whose path reaches this step, and that run stopped at the human-approval checkpoint (`docs/module3/calibration-log.md` lines 305–308).
- Known agent judgment / edge-case input: HO-05. Both code reviewers returned `VERDICT: APPROVE with conditions` with different conditions, and the final report withheld approval and deferred to the human (`docs/module3/calibration-log.md` lines 316 and 318). This is a recorded workflow edge case: a review outcome that is neither a pass nor a fail. There is no evidence that the Project Manager agent itself handled it; HO-05's path does not include the Project Manager, and no Project Manager transcript exists.
- Recommendation: weak candidate.
- Explanation: the core decision is a fixed rule with defined output headings, so part of the step could be expressed as code. But there is no stability or repeatability evidence, because the step has never run in any recorded log or harness result. Its inputs are free text, so mapping a result such as HO-05's "approve with conditions" to complete or not complete still needs judgment. Until the step has recorded runs and structured inputs, the evidence does not support a strong-candidate rating.
