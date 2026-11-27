"""Capstone Milestone 3: deterministic tests for the orchestrated-run evaluator.

The preserved MR-CAP-001 evidence (eval/capstone/evidence/) is evaluated as
recorded and must pass. Tampered copies of it, and small synthetic runs
written in the same shape as scripts/orchestrate.py output, show that each
check fails for the failure it targets. The retry and escalation runs here are
synthetic: no live run has exercised those paths. No model calls are made.
"""

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
EVALUATOR = REPO_ROOT / "eval" / "capstone" / "evaluate_orchestrated_run.py"
EVIDENCE = REPO_ROOT / "eval" / "capstone" / "evidence" / "MR-CAP-001-20261123T200956Z"

sys.dont_write_bytecode = True
_spec = importlib.util.spec_from_file_location("_evaluate_orchestrated_run", EVALUATOR)
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)


def _failed(report):
    return sorted(item["check"] for item in report["checks"] if not item["passed"])


def _edit_json(path, change):
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _edit_events(run_dir, change):
    path = run_dir / "events.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    events = change(events)
    path.write_text("".join(json.dumps(event) + "\n" for event in events))


@pytest.fixture
def evidence_copy(tmp_path):
    target = tmp_path / EVIDENCE.name
    shutil.copytree(EVIDENCE, target)
    return target


# --- the preserved MR-CAP-001 evidence ------------------------------------------------

def test_preserved_mr_cap_001_evidence_passes_every_check():
    report = ev.evaluate(EVIDENCE)
    assert _failed(report) == []
    assert report["passed"] and report["checks_passed"] == report["checks_total"] == 13
    review = next(item for item in report["checks"] if item["check"] == "review_gate")
    assert "Review required: False" in review["explanation"]  # predates the flag: a four-role run


def test_mr_cap_001_metrics_come_from_the_recorded_evidence():
    report = ev.evaluate(EVIDENCE)
    state = json.loads((EVIDENCE / "run.json").read_text())
    metrics = report["metrics"]
    assert [e["role"] for e in metrics["executions"]] == ["planner", "implementer", "tester", "project-manager"]
    assert [e["total_cost_usd"] for e in metrics["executions"]] == [e["total_cost_usd"] for e in state["executions"]]
    assert metrics["totals"]["implementer_attempts"] == 1
    assert metrics["final_test_counts"] == {"total": 27, "passed": 27}
    assert metrics["mcp"]["authorization_denials"] == 0
    assert report["thresholds"].startswith("none applied")


def test_cli_exit_codes(tmp_path, capsys):
    assert ev.main([str(EVIDENCE), "--output", str(tmp_path / "report.json")]) == 0
    assert json.loads((tmp_path / "report.json").read_text())["passed"] is True
    (tmp_path / "not-a-run").mkdir()
    assert ev.main([str(tmp_path / "not-a-run")]) == 2


# --- tampered copies of the real evidence ------------------------------------------

def test_plan_changed_after_approval_fails(evidence_copy):
    with open(evidence_copy / "plan.md", "a") as plan:
        plan.write("\n- `tracker-api/src/services/facebook-scraper.ts`\n")
    assert _failed(ev.evaluate(evidence_copy)) == ["approval_before_implementation"]


def test_approval_recorded_after_implementation_started_fails(evidence_copy):
    def move_approval_last(events):
        approval = [e for e in events if e["event"] == "plan_approved"]
        return [e for e in events if e["event"] != "plan_approved"] + [
            {**approval[0], "timestamp": "2026-11-23T20:42:00+00:00"}]
    _edit_events(evidence_copy, move_approval_last)
    assert _failed(ev.evaluate(evidence_copy)) == ["approval_before_implementation"]


def test_separate_approval_record_must_match_run_json(evidence_copy):
    state = json.loads((evidence_copy / "run.json").read_text())
    (evidence_copy / ev.APPROVAL_RECORD).write_text(json.dumps(state["approval"]))
    assert _failed(ev.evaluate(evidence_copy)) == []
    (evidence_copy / ev.APPROVAL_RECORD).write_text(json.dumps({**state["approval"], "approved_by": "agent"}))
    assert _failed(ev.evaluate(evidence_copy)) == ["approval_before_implementation"]


def test_implementer_change_outside_approved_files_fails(evidence_copy):
    _edit_json(evidence_copy / "run.json", lambda s: s["executions"][1]["files_changed"].append(
        "tracker-api/src/services/facebook-scraper.ts"))
    assert _failed(ev.evaluate(evidence_copy)) == ["file_scope"]


def test_tester_file_change_fails(evidence_copy):
    _edit_json(evidence_copy / "run.json", lambda s: s["executions"][2]["files_changed"].append(
        "tracker-api/tests/services/facebook-scraper.test.ts"))
    assert _failed(ev.evaluate(evidence_copy)) == ["file_scope"]


def test_session_id_mismatch_fails(evidence_copy):
    _edit_json(evidence_copy / "run.json", lambda s: s["executions"][0].update(session_id="other"))
    assert _failed(ev.evaluate(evidence_copy)) == ["real_transcripts_present"]


def test_transcript_outside_run_directory_fails(evidence_copy):
    _edit_json(evidence_copy / "run.json", lambda s: s["executions"][0].update(transcript="../x.jsonl"))
    assert "real_transcripts_present" in _failed(ev.evaluate(evidence_copy))


def test_missing_transcript_fails(evidence_copy):
    (evidence_copy / "03-tester-1.jsonl").unlink()
    assert "real_transcripts_present" in _failed(ev.evaluate(evidence_copy))


def test_recorded_tester_result_contradicting_report_fails(evidence_copy):
    _edit_json(evidence_copy / "run.json", lambda s: s["executions"][2].update(result="FAIL"))
    assert "tester_result_consistent" in _failed(ev.evaluate(evidence_copy))


def test_unverified_outcome_record_fails(evidence_copy):
    _edit_json(evidence_copy / "run.json", lambda s: s["outcome_record"].update(verified=False))
    assert _failed(ev.evaluate(evidence_copy)) == ["outcome_recorded"]


def test_altered_mcp_audit_log_fails(evidence_copy):
    log = evidence_copy / "mcp-audit" / "storage-audit-log.jsonl"
    log.write_text("".join(log.read_text().splitlines(keepends=True)[:-1]))
    assert _failed(ev.evaluate(evidence_copy)) == ["mcp_audit_consistent"]


def test_tool_outside_recorded_grant_fails(evidence_copy):
    _edit_json(evidence_copy / "run.json", lambda s: s["executions"][3].update(allowed_tools=["Read"]))
    assert _failed(ev.evaluate(evidence_copy)) == ["tool_authorization"]


# --- synthetic runs in the orchestrate.py output shape --------------------------------

PLAN = "### Plan\nAdd the test.\n\n### Files\n- `pkg/feature.test.ts`\n"
PROFILES = {
    "planner": ["Read", "Grep", "Glob", "mcp__retrieval__retrieve"],
    "implementer": ["Read", "Grep", "Glob", "Edit", "Write", "mcp__retrieval__retrieve", "mcp__storage__write_entry"],
    "tester": ["Read", "Grep", "Glob", "mcp__retrieval__retrieve", f"Bash({ev.TESTER_COMMAND})", "Skill(run-tests)"],
    "project-manager": ["mcp__storage__write_entry"],
    "reviewer": ["Read", "Grep", "Glob", "mcp__retrieval__retrieve"],
}


def _review_text(verdict, severity="Low"):
    return f"### Findings\n- Severity: {severity}\n  File: pkg/feature.test.ts\n\n### Verdict\n{verdict}\n"


def _transcript(path, session, text, calls=()):
    records = [{"type": "system", "subtype": "init", "session_id": session, "tools": []}]
    for index, (name, arguments, response) in enumerate(calls):
        tool_id = f"{session}-{index}"
        records.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": tool_id, "name": name, "input": arguments}]}})
        records.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": tool_id, "content": response, "is_error": False}]}})
    records.append({"type": "result", "subtype": "success", "is_error": False, "result": text,
                    "duration_ms": 1000, "total_cost_usd": 0.01, "permission_denials": [],
                    "usage": {"input_tokens": 1, "output_tokens": 2,
                              "cache_read_input_tokens": 3, "cache_creation_input_tokens": 4}})
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def _vitest(result):
    return f"Test Files  1 passed (1)\n      Tests  {'2 passed' if result == 'PASS' else '1 failed | 1 passed'} (2)\n"


def make_run(run_dir, tester_results, status, tester_command=None, reviews=None):
    """Write a run directory the way scripts/orchestrate.py records one.

    With `reviews` (one review text per attempt) the run is a `--review` run;
    a tester result of None means the attempt ended at a review REJECT.
    """
    run_dir.mkdir()
    (run_dir / "mcp-audit").mkdir()
    (run_dir / "plan.md").write_text(PLAN)
    plan_hash = ev.sha256_text(PLAN)
    times = iter(f"2026-11-23T10:00:{second:02d}+00:00" for second in range(60))
    events, executions = [], []

    numbers = iter(range(1, 100))

    def run_role(role, name, text, calls=(), files=(), **recorded):
        label = f"{next(numbers):02d}-{name}"
        start, end = next(times), next(times)
        _transcript(run_dir / f"{label}.jsonl", f"s-{label}", text, calls)
        events.append({"timestamp": start, "event": "role_started", "role": role, "label": label})
        events.append({"timestamp": end, "event": "role_finished", "role": role, "label": label})
        execution = {"role": role, "label": label, "transcript": f"{label}.jsonl", "session_id": f"s-{label}",
                     "allowed_tools": PROFILES[role], "started_at": start, "ended_at": end,
                     "files_changed": list(files), "status": "success", **recorded}
        executions.append(execution)
        return label

    run_role("planner", "planner", PLAN, [("Read", {"file_path": "pkg/feature.ts"}, "x")])
    events.append({"timestamp": next(times), "event": "plan_approved", "approved_by": "human",
                   "plan_sha256": plan_hash})
    for attempt, result in enumerate(tester_results, start=1):
        run_role("implementer", f"implementer-{attempt}", "### Changes Made\nAdded test.",
                 [("Write", {"file_path": "pkg/feature.test.ts", "content": "t"}, "ok")], ["pkg/feature.test.ts"])
        if reviews is not None:
            text = reviews[attempt - 1]
            decision = ev.parse_review_verdict(text)
            run_role("reviewer", f"reviewer-{attempt}", text, [("Read", {"file_path": "pkg/feature.test.ts"}, "t")],
                     verdict=decision["verdict"], explicit_verdict=decision["explicit_verdict"],
                     high_findings=decision["high_findings"])
            events.append({"timestamp": next(times), "event": "review_result", "attempt": attempt, **decision})
            if decision["verdict"] == "REJECT":
                events.append({"timestamp": next(times), "event": "routed_to_implementer",
                               "attempt": attempt, "source": "reviewer"})
                continue
        command = tester_command or ev.TESTER_COMMAND
        run_role("tester", f"tester-{attempt}", f"### Result\n{result}\n",
                 [("Bash", {"command": command}, _vitest(result))], result=result)
        events.append({"timestamp": next(times), "event": "test_result", "attempt": attempt, "result": result})
        if result == "FAIL":
            events.append({"timestamp": next(times), "event": "routed_to_implementer", "attempt": attempt})
    outcome = None
    if status == "complete":
        key = "task-outcome/T-1"
        label = run_role("project-manager", "project-manager", "recorded",
                         [("mcp__storage__write_entry", {"key": key, "value": "COMPLETE\nok"}, "{'ok': True}")])
        review_line = "Code review outcome: APPROVE (Code Reviewer attempt n)" if reviews is not None \
            else "Code review outcome: NOT_RUN."
        (run_dir / f"{label}.prompt.md").write_text(f"Task identifier: T-1\n\n{review_line}\n")
        (run_dir / "mcp-audit" / "storage-audit-log.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [
            {"timestamp": next(times), "event": "storage_write", "operation": "write_entry",
             "role": "project-manager", "outcome": "success"},
            {"timestamp": next(times), "event": "storage_read", "operation": "read_entry",
             "role": "orchestrator", "outcome": "success"}]))
        outcome = {"key": key, "verified": True, "value": "COMPLETE\nok"}
    else:
        (run_dir / "mcp-audit" / "retrieval-audit-log.jsonl").write_text(json.dumps(
            {"timestamp": next(times), "event": "retrieval_success", "operation": "retrieve",
             "role": "planner", "outcome": "retrieval_success"}) + "\n")
    events.append({"timestamp": next(times), "event": f"run_{status}"})
    (run_dir / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    state = {
        "run_id": run_dir.name, "kind": "orchestrated_run",
        "task": {"task_id": "T-1", "goal": "g", "acceptance_criteria": ["c"]},
        "expected_roles": ["planner", "implementer"] + (["reviewer"] if reviews is not None else [])
                          + ["tester", "project-manager"],
        "review_required": reviews is not None,
        "status": status, "status_reason": "synthetic", "executions": executions,
        "plan": {"path": "plan.md", "sha256": plan_hash, "files": ["pkg/feature.test.ts"]},
        "approval": {"approved_by": "human", "plan_sha256": plan_hash, "approved_files": ["pkg/feature.test.ts"],
                     "review_required": reviews is not None},
        "mcp_actions": ev.mcp_actions(run_dir),
    }
    if outcome:
        state["outcome_record"] = outcome
    (run_dir / "run.json").write_text(json.dumps(state, indent=2))
    return run_dir


def test_synthetic_fail_then_pass_retry_passes_every_check(tmp_path):
    report = ev.evaluate(make_run(tmp_path / "retry", ["FAIL", "PASS"], "complete"))
    assert _failed(report) == []
    assert report["metrics"]["totals"]["implementer_attempts"] == 2


def test_synthetic_escalation_is_consistent_but_not_complete(tmp_path):
    report = ev.evaluate(make_run(tmp_path / "escalated", ["FAIL", "FAIL"], "escalated"))
    assert _failed(report) == ["run_completed"]
    explanations = {item["check"]: item["explanation"] for item in report["checks"]}
    assert "routed_to_implementer events 2" in explanations["retry_routing_consistent"]


def test_fail_not_routed_back_to_implementer_fails(tmp_path):
    run_dir = make_run(tmp_path / "unrouted", ["FAIL", "PASS"], "complete")
    _edit_events(run_dir, lambda events: [e for e in events if e["event"] != "routed_to_implementer"])
    assert _failed(ev.evaluate(run_dir)) == ["retry_routing_consistent"]


def test_complete_status_after_final_fail_fails(tmp_path):
    run_dir = make_run(tmp_path / "contradiction", ["FAIL", "FAIL"], "escalated")
    _edit_json(run_dir / "run.json", lambda s: s.update(status="complete"))
    failed = _failed(ev.evaluate(run_dir))
    assert "retry_routing_consistent" in failed and "routing_order" in failed


def test_tester_bash_outside_scoped_command_fails(tmp_path):
    run_dir = make_run(tmp_path / "bash", ["PASS"], "complete", tester_command="npm test; rm -rf pkg")
    failed = _failed(ev.evaluate(run_dir))
    assert "tool_authorization" in failed and "tester_command_evidence" in failed


def test_pass_report_without_passing_test_output_fails(tmp_path):
    run_dir = make_run(tmp_path / "misreport", ["PASS"], "complete")
    transcript = run_dir / "03-tester-1.jsonl"
    transcript.write_text(transcript.read_text().replace("2 passed (2)", "1 failed | 1 passed (2)"))
    assert _failed(ev.evaluate(run_dir)) == ["tester_result_consistent"]


def test_project_manager_running_on_an_escalated_run_fails(tmp_path):
    run_dir = make_run(tmp_path / "pm", ["PASS"], "complete")
    _edit_json(run_dir / "run.json", lambda s: s.update(status="escalated"))
    failed = _failed(ev.evaluate(run_dir))
    assert {"routing_order", "outcome_recorded", "run_completed"} <= set(failed)


# --- opt-in Code Reviewer runs ---------------------------------------------------------

def test_synthetic_review_run_passes_every_check(tmp_path):
    report = ev.evaluate(make_run(tmp_path / "review", ["PASS"], "complete", reviews=[_review_text("APPROVE")]))
    assert _failed(report) == []
    reviewer = next(e for e in report["metrics"]["executions"] if e["role"] == "reviewer")
    assert reviewer["verdict"] == "APPROVE" and reviewer["tools_used"] == ["Read"]


def test_synthetic_review_reject_then_approve_passes_every_check(tmp_path):
    run_dir = make_run(tmp_path / "reject", [None, "PASS"], "complete",
                       reviews=[_review_text("APPROVE", severity="High"), _review_text("APPROVE")])
    report = ev.evaluate(run_dir)
    assert _failed(report) == []
    assert report["metrics"]["totals"]["review_rejections"] == 1
    assert report["metrics"]["totals"]["implementer_attempts"] == 2


def test_synthetic_review_escalation_fails_only_completion(tmp_path):
    run_dir = make_run(tmp_path / "escalated", [None, None], "escalated",
                       reviews=[_review_text("REJECT"), _review_text("REJECT")])
    assert _failed(ev.evaluate(run_dir)) == ["run_completed"]


def test_recorded_verdict_contradicting_the_review_report_fails(tmp_path):
    run_dir = make_run(tmp_path / "misread", ["PASS"], "complete", reviews=[_review_text("APPROVE")])
    transcript = run_dir / "03-reviewer-1.jsonl"  # recorded APPROVE; the report now has a High finding
    transcript.write_text(transcript.read_text().replace("Severity: Low", "Severity: High"))
    assert _failed(ev.evaluate(run_dir)) == ["review_gate"]


def test_tester_after_review_reject_fails(tmp_path):
    run_dir = make_run(tmp_path / "bypass", ["PASS"], "complete", reviews=[_review_text("APPROVE")])
    _edit_json(run_dir / "run.json", lambda s: s["executions"][2].update(verdict="REJECT"))
    failed = _failed(ev.evaluate(run_dir))
    assert "review_gate" in failed


def test_project_manager_not_told_the_review_outcome_fails(tmp_path):
    run_dir = make_run(tmp_path / "unreported", ["PASS"], "complete", reviews=[_review_text("APPROVE")])
    prompt = next(run_dir.glob("*-project-manager.prompt.md"))
    prompt.write_text(prompt.read_text().replace("APPROVE", "NOT_RUN"))
    assert _failed(ev.evaluate(run_dir)) == ["review_gate"]


def test_review_flag_changed_after_approval_fails(tmp_path):
    run_dir = make_run(tmp_path / "flag", ["PASS"], "complete", reviews=[_review_text("APPROVE")])
    _edit_json(run_dir / "run.json", lambda s: s["approval"].update(review_required=False))
    assert "approval_before_implementation" in _failed(ev.evaluate(run_dir))


def test_reviewer_in_a_four_role_run_fails(tmp_path):
    run_dir = make_run(tmp_path / "extra", ["PASS"], "complete", reviews=[_review_text("APPROVE")])
    _edit_json(run_dir / "run.json", lambda s: (s.update(review_required=False),
                                                s["approval"].update(review_required=False)))
    failed = _failed(ev.evaluate(run_dir))
    assert "routing_order" in failed and "review_gate" in failed
