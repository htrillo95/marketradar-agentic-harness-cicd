# ADR-004: Convert Project Manager task-outcome decision and report to deterministic code

## Status

Proposed

The step is classified as a weak candidate (`docs/step-classification.md`, "Project Manager task-outcome reporting (Module 4.3 Exercise 1)"). No deterministic replacement has been implemented, integrated or measured. Acceptance depends on the prerequisites in the Decision section being met and on recorded evidence that the replacement matches the agent on the same inputs.

## Context

The `project-manager` agent (`.claude/agents/project-manager.md`) is the last step of the full orchestration path: Planner -> Implementer -> Code Reviewer -> Tester -> Project Manager (`docs/module3/holdout-task-set.md` line 23, HO-02). It receives five inputs from the Orchestrator: task identifier, implementation summary, code review outcome, test results and requested final status (lines 30–37). It decides the task outcome and returns `### Task Outcome`, `### Evidence`, `### Storage Record` and `### Blockers` (lines 39–53). It also records the outcome with `mcp__storage__write_entry` (lines 18–24).

Part of the step is a fixed rule: "Never mark a task complete if review or testing failed" (line 17). Other parts need judgment: "Determine the task outcome from the evidence provided" (line 16), and reporting missing or contradictory evidence as a blocker instead of guessing (line 28). The inputs have no defined format.

The step has never run in any recorded log or harness result. In HO-02, the only holdout whose path reaches it, the orchestrator stopped at the human-approval checkpoint, and "The Project Manager never ran" (`docs/module3/calibration-log.md` lines 305–308).

HO-05 recorded the kind of input this step would have to classify. Both code reviewers returned `VERDICT: APPROVE with conditions`, with different conditions, and the final report withheld approval and deferred to the human (`docs/module3/calibration-log.md` lines 316–318). The Project Manager did not execute during HO-05: HO-05's path is Planner -> Code Reviewer A and Code Reviewer B -> Planner (`docs/module3/holdout-task-set.md` line 57), and no Project Manager transcript exists.

## Decision

Propose moving the task-outcome decision and the four-heading report to deterministic code, only after these prerequisites are met:

1. Structured inputs. The Orchestrator passes explicit verdicts instead of free text: a review outcome of `APPROVE`, `REJECT` or `CONDITIONAL`, and a test outcome of `PASS`, `FAIL` or `NOT_VERIFIED`, alongside the task identifier and requested final status.
2. Execution evidence. Recorded Project Manager runs exist on the same inputs, including at least one passing, one failing and one ambiguous case, so the replacement can be compared with the agent.

Under this proposal the code would apply these rules in order, and the first matching rule decides:

1. Escalate to a human, without deciding, if a required input is missing or the inputs contradict each other. Contradictory means more than one review verdict is supplied and they differ, or two `CONDITIONAL` verdicts carry different conditions, as in HO-05.
2. Mark the task not complete if either outcome is a failure (`REJECT` or `FAIL`), even if the other outcome is `CONDITIONAL` or `NOT_VERIFIED`. Any unresolved item is listed under `### Blockers`.
3. Escalate to a human, without deciding, if either outcome is `CONDITIONAL` or `NOT_VERIFIED`. HO-05's conflicting conditional approvals are escalated, not converted into complete or not complete.
4. Mark the task complete only if the review outcome is `APPROVE` and the test outcome is `PASS`.

The requested final status does not change the decision. If it differs from the decided outcome, the difference is reported under `### Blockers`.

The storage write (`mcp__storage__write_entry`) is out of scope. It stays with the agent or the Orchestrator and remains blocked by policy gap D2 (`docs/governance-policy.md`).

## Alternatives considered

- Keep the agent for the whole step. This is the current state. It keeps judgment for free-text and ambiguous inputs, but the step has no run evidence and its output is not verifiable without runs.
- Convert now, using the existing free-text inputs. Rejected. Mapping free-text review and test summaries to an outcome needs judgment, and there is no recorded run to compare a replacement against.
- Convert the whole step, including the storage write. Rejected. The write depends on the storage MCP interface, which does not match the Module 4 server (policy gap D2), so it cannot be specified or tested now.
- Retire the step and let the Orchestrator report the outcome. Not chosen. It removes the "never complete on failure" check from a separate role without adding evidence.

## Consequences

Anticipated, not measured:

- The completion rule would give the same result for the same structured inputs.
- Ambiguous outcomes would always reach a human, instead of depending on the agent's interpretation.
- The Evidence summary would become templated text, losing the agent's prose summary.

Required changes if the proposal proceeds:

- The Orchestrator must produce structured verdicts. That is a change to the handoff, not only to this step.
- Human escalation must stay in place for ambiguous, missing or contradictory inputs.
- The storage write remains a separate, unresolved dependency (D2).

## Related decisions

Follows ADR-001, ADR-002 and ADR-003. Like ADR-003, it converts only part of a role. Unlike them, the step has no recorded runs before conversion.

## Evidence

Recorded evidence:

- Classification: `docs/step-classification.md`, "Project Manager task-outcome reporting (Module 4.3 Exercise 1)": weak candidate; stability and repeatability have insufficient evidence; specifiability is partial; run rate is low, with zero recorded executions.
- Agent definition: `.claude/agents/project-manager.md` (inputs at lines 30–37, decision rules at lines 16–17 and 28, outputs at lines 39–53, storage write at lines 18–24).
- No execution: `docs/module3/calibration-log.md` lines 305–308 (HO-02 stopped at the human-approval checkpoint; the Project Manager never ran).
- Ambiguous review outcome: `docs/module3/calibration-log.md` lines 316–318 (HO-05: both reviewers approved with different conditions; approval was withheld for the human). The Project Manager did not execute during HO-05 (`docs/module3/holdout-task-set.md` line 57).

Not available:

- No Project Manager runs, baseline cycle time, token cost or harness scores exist, so no baseline or savings are stated.
- No deterministic replacement has been implemented, integrated or measured.
