"""Capstone Milestone 2: orchestration entry point and permission boundaries.

Deterministic tests for scripts/orchestrate.py, the approval-gate hook, the
project MCP registration and the agent definitions. Claude Code is replaced by
a fake binary (ORCHESTRATOR_CLAUDE_BIN) that emits stream-json transcripts and
records how it was launched, so routing, approval enforcement and per-role
launch settings are checked without model calls. Live execution evidence is
recorded separately (docs/module3/migration-notes.md §8).
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ORCHESTRATE = REPO_ROOT / "scripts" / "orchestrate.py"
APPROVAL_HOOK = REPO_ROOT / "scripts" / "enforce_approval_gate.py"
AGENTS_DIR = REPO_ROOT / ".claude" / "agents"
ORCHESTRATED_ROLES = ["planner", "implementer", "tester", "project-manager", "reviewer"]
REVIEWER_AGENT = "marketradar-code-reviewer"

PLAN = """### Plan
1. Add the test.

### Files
- `pkg/feature.test.ts` - new regression test

### Risks
None.

### Acceptance Check
Run the tests.
"""

FAKE_CLAUDE = r'''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
role = args[args.index("--agent") + 1]
scenario_path = Path(os.environ["FAKE_CLAUDE_SCENARIO"])
scenario = json.loads(scenario_path.read_text())
calls_path = scenario_path.with_name("calls.jsonl")
calls = [json.loads(l) for l in calls_path.read_text().splitlines()] if calls_path.exists() else []
index = sum(1 for c in calls if c["role"] == role)
with open(calls_path, "a") as log:
    log.write(json.dumps({"role": role, "agent_role_env": os.environ.get("AGENT_ROLE"),
                         "run_dir_env": os.environ.get("ORCHESTRATION_RUN_DIR"),
                         "argv": args, "prompt": args[args.index("-p") + 1]}) + "\n")
response = scenario[role][min(index, len(scenario[role]) - 1)]
for path, content in response.get("write", {}).items():
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content)
print(json.dumps({"type": "system", "subtype": "init", "session_id": f"fake-{role}-{index}",
                  "model": "fake", "tools": []}))
for number, call in enumerate(response.get("bash", [])):
    tool_id = f"toolu_fake_{role}_{index}_{number}"
    print(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": call["command"]}}]}}))
    print(json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tool_id, "is_error": call.get("is_error", False),
         "content": call.get("output", "Tests  1 passed (1)")}]}}))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "result": response["text"], "duration_ms": 1, "total_cost_usd": 0,
                  "usage": {}, "permission_denials": []}))
'''


@pytest.fixture
def harness(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "pkg").mkdir()
    (repo / "pkg" / "feature.ts").write_text("export const x = 1\n")
    (repo / ".mcp.json").write_text((REPO_ROOT / ".mcp.json").read_text())
    fake = tmp_path / "fake_claude.py"
    fake.write_text(FAKE_CLAUDE)
    launcher = tmp_path / "claude"
    launcher.write_text(f"#!/bin/sh\nexec {sys.executable} {fake} \"$@\"\n")
    launcher.chmod(0o755)
    scenario = tmp_path / "scenario" / "scenario.json"
    scenario.parent.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_REPO_ROOT", str(repo))
    monkeypatch.setenv("ORCHESTRATOR_CLAUDE_BIN", str(launcher))
    monkeypatch.setenv("ORCHESTRATION_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("STORAGE_STORE", str(tmp_path / "store.db"))
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", str(scenario))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    spec = importlib.util.spec_from_file_location(f"_orchestrate_{uuid.uuid4().hex}", ORCHESTRATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    task = tmp_path / "task.json"
    task.write_text(json.dumps({"task_id": "T-1", "goal": "Add a regression test.",
                                "acceptance_criteria": ["the test exists", "tests pass"]}))

    class Harness:
        orchestrate = module

        def scenario(self, **responses):
            scenario.write_text(json.dumps(responses))

        def calls(self):
            path = scenario.with_name("calls.jsonl")
            return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []

        def plan(self, *extra):
            assert module.main(["plan", "--task", str(task), *extra]) == 0
            return next((tmp_path / "runs").iterdir())

        def state(self, run_dir):
            return json.loads((run_dir / "run.json").read_text())

        def events(self, run_dir):
            return [json.loads(l) for l in (run_dir / "events.jsonl").read_text().splitlines()]

        def repo(self):
            return repo

    return Harness()


TESTER_COMMAND = "npm --prefix tracker-api test"
EXACT_RUN = {"command": TESTER_COMMAND}


def _tester(result, bash=(EXACT_RUN,)):
    return {"text": f"### Result\n{result}\n\n### Validation Performed\nnpm test\n\n### Findings\n{result}",
            "bash": list(bash)}


IMPLEMENT = {"text": "### Changes Made\nAdded test.", "write": {"pkg/feature.test.ts": "test\n"}}


def _approve(harness, run_dir):
    return harness.orchestrate.main(["approve", "--run-dir", str(run_dir), "--approved-by", "human"])


# --- human approval ---------------------------------------------------------------

def test_plan_stops_for_human_approval(harness):
    harness.scenario(planner=[{"text": PLAN}])
    run_dir = harness.plan()
    state = json.loads((run_dir / "run.json").read_text())
    assert state["status"] == "awaiting_approval"
    assert state["plan"]["files"] == ["pkg/feature.test.ts"]
    assert [c["role"] for c in harness.calls()] == ["planner"]


def test_run_refuses_without_approval(harness):
    harness.scenario(planner=[{"text": PLAN}], implementer=[IMPLEMENT], tester=[_tester("PASS")])
    run_dir = harness.plan()
    assert harness.orchestrate.main(["run", "--run-dir", str(run_dir)]) == 2
    assert [c["role"] for c in harness.calls()] == ["planner"]
    assert not (run_dir.parent.parent / "repo" / "pkg" / "feature.test.ts").exists()


def test_run_refuses_when_plan_changed_after_approval(harness):
    harness.scenario(planner=[{"text": PLAN}], implementer=[IMPLEMENT], tester=[_tester("PASS")])
    run_dir = harness.plan()
    assert _approve(harness, run_dir) == 0
    (run_dir / "plan.md").write_text(PLAN + "\n- `pkg/feature.ts`\n")
    assert harness.orchestrate.main(["run", "--run-dir", str(run_dir)]) == 2
    assert "implementer" not in [c["role"] for c in harness.calls()]


# --- routing -------------------------------------------------------------------------

def test_tester_fail_routes_back_to_implementer_then_completes(harness):
    harness.scenario(planner=[{"text": PLAN}], implementer=[IMPLEMENT, IMPLEMENT],
                     tester=[_tester("FAIL"), _tester("PASS")],
                     **{"project-manager": [{"text": "### Task Outcome\nT-1 COMPLETE"}]})
    run_dir = harness.plan()
    _approve(harness, run_dir)
    assert harness.orchestrate.main(["run", "--run-dir", str(run_dir)]) == 0
    calls = harness.calls()
    assert [c["role"] for c in calls] == [
        "planner", "implementer", "tester", "implementer", "tester", "project-manager"]
    assert "Tester FAIL report (attempt 1)" in calls[3]["prompt"]
    state = json.loads((run_dir / "run.json").read_text())
    assert state["status"] == "complete"
    assert [e.get("result") for e in state["executions"] if e["role"] == "tester"] == ["FAIL", "PASS"]


def test_repeated_failure_escalates_without_project_manager(harness):
    harness.scenario(planner=[{"text": PLAN}], implementer=[IMPLEMENT], tester=[_tester("FAIL")])
    run_dir = harness.plan()
    _approve(harness, run_dir)
    assert harness.orchestrate.main(["run", "--run-dir", str(run_dir), "--max-attempts", "2"]) == 1
    assert [c["role"] for c in harness.calls()].count("implementer") == 2
    assert "project-manager" not in [c["role"] for c in harness.calls()]
    assert json.loads((run_dir / "run.json").read_text())["status"] == "escalated"


def test_implementer_change_outside_approved_files_escalates(harness):
    harness.scenario(planner=[{"text": PLAN}],
                     implementer=[{"text": "done", "write": {"pkg/feature.ts": "changed\n"}}],
                     tester=[_tester("PASS")])
    run_dir = harness.plan()
    _approve(harness, run_dir)
    assert harness.orchestrate.main(["run", "--run-dir", str(run_dir)]) == 1
    assert "tester" not in [c["role"] for c in harness.calls()]
    state = json.loads((run_dir / "run.json").read_text())
    assert state["status"] == "escalated" and "pkg/feature.ts" in state["status_reason"]


def test_tester_that_modifies_files_is_escalated(harness):
    harness.scenario(planner=[{"text": PLAN}], implementer=[IMPLEMENT],
                     tester=[{**_tester("PASS"), "write": {"pkg/feature.ts": "tester edit\n"}}])
    run_dir = harness.plan()
    _approve(harness, run_dir)
    assert harness.orchestrate.main(["run", "--run-dir", str(run_dir)]) == 1
    assert "project-manager" not in [c["role"] for c in harness.calls()]


def test_unparseable_tester_result_counts_as_fail():
    spec = importlib.util.spec_from_file_location("_orch_parse", ORCHESTRATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.parse_test_result("### Result\nLooks fine to me") == "FAIL"
    assert module.parse_test_result("### Result\n**PASS**\n") == "PASS"


# --- per-role launch settings ---------------------------------------------------

def test_each_role_launches_with_its_own_server_side_identity(harness):
    harness.scenario(planner=[{"text": PLAN}], implementer=[IMPLEMENT], tester=[_tester("PASS")],
                     **{"project-manager": [{"text": "recorded"}]})
    run_dir = harness.plan()
    _approve(harness, run_dir)
    harness.orchestrate.main(["run", "--run-dir", str(run_dir)])
    for call in harness.calls():
        argv = call["argv"]
        assert call["agent_role_env"] == call["role"]
        assert argv[argv.index("--agent") + 1] == call["role"]
        assert "--strict-mcp-config" in argv
        assert Path(argv[argv.index("--mcp-config") + 1]).name == ".mcp.json"
        assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
        allowed = argv[argv.index("--allowedTools") + 1].split(",")
        assert allowed == harness.orchestrate.ROLE_PROFILES[call["role"]]["allowed"]


def _module():
    spec = importlib.util.spec_from_file_location("_orch_module", ORCHESTRATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _definition(role):
    """Agent definition text for a server-side role (reviewer -> marketradar-code-reviewer)."""
    return (AGENTS_DIR / f"{_module().agent_name(role)}.md").read_text()


def _profiles():
    spec = importlib.util.spec_from_file_location("_orch_profiles", ORCHESTRATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ROLE_PROFILES


@pytest.mark.parametrize("role", ORCHESTRATED_ROLES)
def test_role_mcp_grants_match_server_allow_lists(role):
    storage = json.loads((REPO_ROOT / "mcp-servers/storage/allow-list.json").read_text())
    retrieval = json.loads((REPO_ROOT / "mcp-servers/retrieval/allow-list.json").read_text())
    allowed = set(_profiles()[role]["allowed"])
    granted_storage = {op for op, roles in storage.items() if isinstance(roles, list) and role in roles}
    assert {t.split("__")[2] for t in allowed if t.startswith("mcp__storage__")} == granted_storage
    retrieve_granted = bool(retrieval["retrieve"].get(role, {}).get("granted"))
    assert ("mcp__retrieval__retrieve" in allowed) == retrieve_granted


@pytest.mark.parametrize("role", ORCHESTRATED_ROLES)
def test_role_grants_stay_within_agent_definition(role):
    text = _definition(role)
    tools = {t.strip() for t in re.search(r"^tools:(.*)$", text, re.M).group(1).split(",")}
    for grant in _profiles()[role]["allowed"]:
        assert grant.split("(")[0] in tools, f"{role} grant {grant} is not in its definition"


def test_tester_bash_is_limited_to_the_run_tests_command():
    bash = [g for g in _profiles()["tester"]["allowed"] if g.startswith("Bash")]
    assert bash == ["Bash(npm --prefix tracker-api test)"]


# --- registration and agent definitions --------------------------------------------

def test_mcp_registration_points_at_module4_servers_with_env_role():
    config = json.loads((REPO_ROOT / ".mcp.json").read_text())["mcpServers"]
    assert set(config) == {"storage", "retrieval"}
    for name, server in config.items():
        assert server["args"] == [f"mcp-servers/{name}/server.py"]
        assert (REPO_ROOT / server["args"][0]).is_file()
        # Role comes only from the launching process; unset means "" and is denied.
        assert server["env"] == {"AGENT_ROLE": "${AGENT_ROLE:-}"}


@pytest.mark.parametrize("role", ORCHESTRATED_ROLES)
def test_agent_definitions_use_module4_tool_signatures(role):
    text = _definition(role)
    for module3_argument in ("calling_role", "project_id", "classification_ceiling", "entry_type", "entry_id"):
        assert module3_argument not in text
    if "mcp__retrieval__retrieve" in text:
        assert "query:" in text and "top_k:" in text
    if "mcp__storage__write_entry" in text:
        assert "key:" in text and "value:" in text


# --- approval-gate hook -------------------------------------------------------------

def _hook(payload, tmp_path):
    env = {**os.environ, "APPROVAL_AUDIT_LOG": str(tmp_path / "approval-audit.jsonl")}
    completed = subprocess.run([sys.executable, str(APPROVAL_HOOK)], input=json.dumps(payload),
                               capture_output=True, text=True, env=env, check=True)
    return json.loads(completed.stdout) if completed.stdout.strip() else None


@pytest.mark.parametrize("payload", [
    {"tool_name": "Bash", "tool_input": {"command": "python3 scripts/orchestrate.py approve --run-dir x --approved-by me"}},
    {"tool_name": "Bash", "tool_input": {"command": "echo '{}' > /runs/x/approval.json"}},
    {"tool_name": "Write", "tool_input": {"file_path": "/runs/x/approval.json", "content": "{}"}},
    {"tool_name": "Edit", "tool_input": {"file_path": "/runs/x/approval.json"}},
    {"tool_name": "Bash", "tool_input": {}},
])
def test_approval_gate_denies_agent_approval(payload, tmp_path):
    output = _hook(payload, tmp_path)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    record = json.loads((tmp_path / "approval-audit.jsonl").read_text().splitlines()[-1])
    assert record["event"] == "approval_gate_denied"


@pytest.mark.parametrize("payload", [
    {"tool_name": "Bash", "tool_input": {"command": "python3 scripts/orchestrate.py run --run-dir x"}},
    {"tool_name": "Bash", "tool_input": {"command": "npm --prefix tracker-api test"}},
    {"tool_name": "Write", "tool_input": {"file_path": "tracker-api/tests/a.test.ts"}},
])
def test_approval_gate_allows_other_calls(payload, tmp_path):
    assert _hook(payload, tmp_path) is None


def test_approval_gate_hook_is_registered():
    settings = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text())
    entries = settings["hooks"]["PreToolUse"]
    gate = [e for e in entries if any("enforce_approval_gate.py" in h["command"] for h in e["hooks"])]
    assert gate and set(gate[0]["matcher"].split("|")) >= {"Bash", "Write", "Edit"}


# --- Code Reviewer stage (opt-in: plan --review) --------------------------------------

def _review(verdict, *severities):
    findings = "\n".join(f"- Severity: {s}\n  File: pkg/feature.test.ts\n  Issue: x" for s in severities)
    return {"text": f"### Findings\n{findings or 'None.'}\n\nOverall: ok.\n\n### Verdict\n{verdict}\n"}


PM_DONE = {"project-manager": [{"text": "### Task Outcome\nT-1 COMPLETE"}]}


def _labels(state):
    return [e["label"] for e in state["executions"]]


def test_review_is_off_by_default_and_the_four_role_run_is_unchanged(harness):
    harness.scenario(planner=[{"text": PLAN}], implementer=[IMPLEMENT, IMPLEMENT],
                     tester=[_tester("FAIL"), _tester("PASS")], **PM_DONE)
    run_dir = harness.plan()
    _approve(harness, run_dir)
    assert harness.orchestrate.main(["run", "--run-dir", str(run_dir)]) == 0
    calls = harness.calls()
    assert [c["role"] for c in calls] == [
        "planner", "implementer", "tester", "implementer", "tester", "project-manager"]
    state = harness.state(run_dir)
    assert state["review_required"] is False and state["approval"]["review_required"] is False
    assert state["expected_roles"] == ["planner", "implementer", "tester", "project-manager"]
    assert _labels(state) == ["01-planner", "02-implementer-1", "03-tester-1",
                              "04-implementer-2", "05-tester-2", "06-project-manager"]
    assert "Code review outcome: NOT_RUN" in calls[-1]["prompt"]
    events = harness.events(run_dir)
    assert not [e for e in events if e["event"] == "review_result"]
    routed = [e for e in events if e["event"] == "routed_to_implementer"]
    assert len(routed) == 1 and "source" not in routed[0]


def test_run_approved_before_the_review_stage_existed_still_runs(harness):
    """run.json and the approval record without a review flag (e.g. MR-CAP-001) mean a four-role run."""
    harness.scenario(planner=[{"text": PLAN}], implementer=[IMPLEMENT], tester=[_tester("PASS")], **PM_DONE)
    run_dir = harness.plan()
    _approve(harness, run_dir)
    state = harness.state(run_dir)
    state.pop("review_required")
    state["approval"].pop("review_required")
    (run_dir / "run.json").write_text(json.dumps(state))
    (run_dir / "approval.json").write_text(json.dumps(state["approval"]))
    assert harness.orchestrate.main(["run", "--run-dir", str(run_dir)]) == 0
    assert [c["role"] for c in harness.calls()] == ["planner", "implementer", "tester", "project-manager"]


def test_review_approve_runs_reviewer_between_implementer_and_tester(harness):
    harness.scenario(planner=[{"text": PLAN}], implementer=[IMPLEMENT], tester=[_tester("PASS")],
                     **{REVIEWER_AGENT: [_review("APPROVE", "Low")]}, **PM_DONE)
    run_dir = harness.plan("--review")
    assert harness.state(run_dir)["expected_roles"] == [
        "planner", "implementer", "reviewer", "tester", "project-manager"]
    _approve(harness, run_dir)
    assert harness.state(run_dir)["approval"]["review_required"] is True
    assert harness.orchestrate.main(["run", "--run-dir", str(run_dir)]) == 0
    calls = harness.calls()
    assert [c["role"] for c in calls] == ["planner", "implementer", REVIEWER_AGENT, "tester", "project-manager"]
    review_call = calls[2]
    assert review_call["agent_role_env"] == "reviewer"
    argv = review_call["argv"]
    assert argv[argv.index("--allowedTools") + 1].split(",") == harness.orchestrate.ROLE_PROFILES["reviewer"]["allowed"]
    assert "--strict-mcp-config" in argv and argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert "### Verdict" in review_call["prompt"] and "pkg/feature.test.ts" in review_call["prompt"]
    assert "Code review outcome: APPROVE" in calls[-1]["prompt"]
    assert "Severity: Low" in calls[-1]["prompt"]
    state = harness.state(run_dir)
    assert state["status"] == "complete"
    assert _labels(state) == ["01-planner", "02-implementer-1", "03-reviewer-1", "04-tester-1", "05-project-manager"]
    reviewer = state["executions"][2]
    assert reviewer["role"] == "reviewer" and reviewer["agent"] == REVIEWER_AGENT
    assert (reviewer["verdict"], reviewer["explicit_verdict"], reviewer["high_findings"]) == ("APPROVE", "APPROVE", 0)
    assert "tool_calls" in reviewer and (run_dir / "03-reviewer-1.prompt.md").exists()
    review_events = [e for e in harness.events(run_dir) if e["event"] == "review_result"]
    assert [(e["verdict"], e["reasons"]) for e in review_events] == [("APPROVE", [])]


@pytest.mark.parametrize("first_review", [
    _review("APPROVE", "High"),          # a High-severity finding rejects despite APPROVE
    _review("REJECT", "Low"),            # an explicit REJECT verdict
], ids=["high-severity", "explicit-reject"])
def test_review_reject_routes_back_to_implementer_before_testing(harness, first_review):
    harness.scenario(planner=[{"text": PLAN}], implementer=[IMPLEMENT, IMPLEMENT], tester=[_tester("PASS")],
                     **{REVIEWER_AGENT: [first_review, _review("APPROVE")]}, **PM_DONE)
    run_dir = harness.plan("--review")
    _approve(harness, run_dir)
    assert harness.orchestrate.main(["run", "--run-dir", str(run_dir)]) == 0
    calls = harness.calls()
    assert [c["role"] for c in calls] == [
        "planner", "implementer", REVIEWER_AGENT, "implementer", REVIEWER_AGENT, "tester", "project-manager"]
    assert "Code Reviewer REJECT report (attempt 1)" in calls[3]["prompt"]
    routed = [e for e in harness.events(run_dir) if e["event"] == "routed_to_implementer"]
    assert [(e["attempt"], e.get("source")) for e in routed] == [(1, "reviewer")]
    review_events = [e for e in harness.events(run_dir) if e["event"] == "review_result"]
    assert [e["verdict"] for e in review_events] == ["REJECT", "APPROVE"]
    state = harness.state(run_dir)
    assert [e.get("verdict") for e in state["executions"] if e["role"] == "reviewer"] == ["REJECT", "APPROVE"]
    assert "Code review outcome: APPROVE (Code Reviewer attempt 2" in calls[-1]["prompt"]


def test_repeated_review_rejection_escalates_at_the_attempt_limit(harness):
    harness.scenario(planner=[{"text": PLAN}], implementer=[IMPLEMENT], tester=[_tester("PASS")],
                     **{REVIEWER_AGENT: [_review("REJECT")]}, **PM_DONE)
    run_dir = harness.plan("--review")
    _approve(harness, run_dir)
    assert harness.orchestrate.main(["run", "--run-dir", str(run_dir), "--max-attempts", "2"]) == 1
    roles = [c["role"] for c in harness.calls()]
    assert roles.count("implementer") == 2 and roles.count(REVIEWER_AGENT) == 2
    assert "tester" not in roles and "project-manager" not in roles
    state = harness.state(run_dir)
    assert state["status"] == "escalated"
    assert state["status_reason"] == "code review still rejecting after 2 implementer attempts"


def test_review_rejection_and_test_failure_share_one_attempt_limit(harness):
    harness.scenario(planner=[{"text": PLAN}], implementer=[IMPLEMENT], tester=[_tester("FAIL")],
                     **{REVIEWER_AGENT: [_review("APPROVE"), _review("REJECT")]}, **PM_DONE)
    run_dir = harness.plan("--review")
    _approve(harness, run_dir)
    assert harness.orchestrate.main(["run", "--run-dir", str(run_dir), "--max-attempts", "2"]) == 1
    assert [c["role"] for c in harness.calls()] == [
        "planner", "implementer", REVIEWER_AGENT, "tester", "implementer", REVIEWER_AGENT]
    assert harness.state(run_dir)["status"] == "escalated"


def test_reviewer_that_modifies_files_is_escalated(harness):
    harness.scenario(planner=[{"text": PLAN}], implementer=[IMPLEMENT], tester=[_tester("PASS")],
                     **{REVIEWER_AGENT: [{**_review("APPROVE"), "write": {"pkg/feature.ts": "reviewer edit\n"}}]})
    run_dir = harness.plan("--review")
    _approve(harness, run_dir)
    assert harness.orchestrate.main(["run", "--run-dir", str(run_dir)]) == 1
    assert "tester" not in [c["role"] for c in harness.calls()]
    assert "reviewer modified files" in harness.state(run_dir)["status_reason"]


def test_run_refuses_when_review_requirement_changed_after_approval(harness):
    harness.scenario(planner=[{"text": PLAN}], implementer=[IMPLEMENT], tester=[_tester("PASS")])
    run_dir = harness.plan("--review")
    assert _approve(harness, run_dir) == 0
    state = harness.state(run_dir)
    state["review_required"] = False
    (run_dir / "run.json").write_text(json.dumps(state))
    assert harness.orchestrate.main(["run", "--run-dir", str(run_dir)]) == 2
    assert "implementer" not in [c["role"] for c in harness.calls()]


def test_run_refuses_review_run_without_approval(harness):
    harness.scenario(planner=[{"text": PLAN}], implementer=[IMPLEMENT], tester=[_tester("PASS")])
    run_dir = harness.plan("--review")
    assert harness.orchestrate.main(["run", "--run-dir", str(run_dir)]) == 2
    assert [c["role"] for c in harness.calls()] == ["planner"]


@pytest.mark.parametrize("report, verdict, explicit, high", [
    ("- Severity: Medium\n\n### Verdict\nAPPROVE", "APPROVE", "APPROVE", 0),
    ("### Verdict\nAPPROVE with conditions", "APPROVE", "APPROVE", 0),
    ("- **Severity:** High\n\n### Verdict\nAPPROVE", "REJECT", "APPROVE", 1),
    ("| Severity | High |\n\n### Verdict\nAPPROVE", "REJECT", "APPROVE", 1),
    ("### Verdict\n**REJECT**", "REJECT", "REJECT", 0),
    ("Looks fine overall.", "REJECT", None, 0),          # no readable verdict: fail closed
    ("### Verdict\nLGTM", "REJECT", None, 0),
])
def test_parse_review_verdict(report, verdict, explicit, high):
    decision = _module().parse_review_verdict(report)
    assert (decision["verdict"], decision["explicit_verdict"], decision["high_findings"]) == (verdict, explicit, high)
    assert bool(decision["reasons"]) == (verdict == "REJECT")


def _load_run_reviewer():
    spec = importlib.util.spec_from_file_location("_run_reviewer", REPO_ROOT / "scripts" / "run-reviewer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reviewer_profile_matches_the_ci_advisory_reviewer_grants():
    reviewer = _load_run_reviewer()
    profile = _profiles()["reviewer"]
    assert profile["allowed"] == reviewer.ALLOWED_TOOLS.split(",")
    assert profile["disallowed"] == reviewer.DISALLOWED_TOOLS.split(",")
    assert _module().agent_name("reviewer") == reviewer.REVIEWER_AGENT == REVIEWER_AGENT
    assert reviewer.MCP_AGENT_ROLE == "reviewer"


# --- D18: exact Tester command (hook scope and orchestrator safeguard) ---------------

BASH_HOOK = REPO_ROOT / "scripts" / "enforce_bash_scope.py"


def _load(path):
    spec = importlib.util.spec_from_file_location(f"_load_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PIPED = {"command": f"{TESTER_COMMAND} 2>&1 | tail -40"}


def _run_and_state(harness, *tester_responses, review=False):
    reviewer = {REVIEWER_AGENT: [_review("APPROVE")]} if review else {}
    harness.scenario(planner=[{"text": PLAN}], implementer=[IMPLEMENT, IMPLEMENT],
                     tester=list(tester_responses), **reviewer, **PM_DONE)
    run_dir = harness.plan(*(["--review"] if review else []))
    _approve(harness, run_dir)
    code = harness.orchestrate.main(["run", "--run-dir", str(run_dir)])
    return code, run_dir, harness.state(run_dir)


def test_tester_command_constant_matches_the_hook_and_the_grant():
    hook = _load(BASH_HOOK)
    assert _module().TESTER_COMMAND == hook.ORCHESTRATED_TESTER_COMMAND == TESTER_COMMAND
    assert f"Bash({TESTER_COMMAND})" in _profiles()["tester"]["allowed"]


@pytest.mark.parametrize("review", [False, True], ids=["four-role", "five-role"])
def test_exact_command_pass_completes_and_records_the_check(harness, review):
    code, run_dir, state = _run_and_state(harness, _tester("PASS"), review=review)
    assert code == 0 and state["status"] == "complete"
    roles = [e["role"] for e in state["executions"]]
    assert roles == ["planner", "implementer"] + (["reviewer"] if review else []) + ["tester", "project-manager"]
    tester = next(e for e in state["executions"] if e["role"] == "tester")
    assert tester["command_check"] == {"unauthorized": [], "authorized_successes": 1, "parse_error": None}
    checks = [e for e in harness.events(run_dir) if e["event"] == "tester_command_check"]
    assert [(c["attempt"], c["unauthorized"], c["authorized_successes"]) for c in checks] == [(1, [], 1)]


def test_every_child_session_carries_the_orchestration_marker(harness):
    code, run_dir, _ = _run_and_state(harness, _tester("PASS"), review=True)
    assert code == 0
    calls = harness.calls()
    assert {c["run_dir_env"] for c in calls} == {str(run_dir)}
    assert {c["agent_role_env"] for c in calls} == {
        "planner", "implementer", "reviewer", "tester", "project-manager"}


@pytest.mark.parametrize("bash", [
    (), ({**EXACT_RUN, "is_error": True},),
], ids=["no-command", "command-errored"])
@pytest.mark.parametrize("review", [False, True], ids=["four-role", "five-role"])
def test_pass_without_successful_exact_command_is_not_accepted(harness, bash, review):
    code, run_dir, state = _run_and_state(harness, _tester("PASS", bash=bash), review=review)
    assert code == 1 and state["status"] == "escalated"
    assert "reported PASS without a successful" in state["status_reason"]
    assert "project-manager" not in [e["role"] for e in state["executions"]]
    assert "outcome_record" not in state


@pytest.mark.parametrize("bash", [
    (EXACT_RUN, PIPED),
    (PIPED,),
    ({**PIPED, "is_error": True}, EXACT_RUN),
    ({"command": "git diff"}, EXACT_RUN),
], ids=["exact-then-piped", "piped-only-mr-cap-002", "denied-attempt-then-exact", "other-command"])
@pytest.mark.parametrize("result", ["PASS", "FAIL"])
def test_any_other_tester_command_escalates(harness, bash, result):
    code, run_dir, state = _run_and_state(harness, _tester(result, bash=bash), _tester("PASS"))
    assert code == 1 and state["status"] == "escalated"
    assert "Bash commands other than" in state["status_reason"]
    assert [e["role"] for e in state["executions"]] == ["planner", "implementer", "tester"]
    tester = state["executions"][-1]
    assert tester["result"] == result
    assert tester["command_check"]["unauthorized"] == [c["command"] for c in bash if c != EXACT_RUN]


def test_fail_with_exact_command_is_still_routed_back(harness):
    code, run_dir, state = _run_and_state(harness, _tester("FAIL"), _tester("PASS"))
    assert code == 0 and state["status"] == "complete"
    assert [e.get("result") for e in state["executions"] if e["role"] == "tester"] == ["FAIL", "PASS"]


def _hook_decision(env, command, **tool_input):
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": command, **tool_input}}
    run = subprocess.run([sys.executable, "-I", str(BASH_HOOK)], input=json.dumps(payload),
                         capture_output=True, text=True, env=env, timeout=30)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)["hookSpecificOutput"]["permissionDecision"] if run.stdout.strip() else None


def test_hook_enforces_exact_command_in_the_orchestrated_tester_environment(tmp_path):
    orchestrate = _module()
    run_dir = tmp_path / "run"
    env = orchestrate.child_environment("tester", run_dir)
    assert _hook_decision(env, TESTER_COMMAND) is None
    assert _hook_decision(env, PIPED["command"]) == "deny"
    assert _hook_decision(env, TESTER_COMMAND, run_in_background=True) == "deny"
    records = [json.loads(l) for l in (run_dir / "hook-audit" / "bash-audit-log.jsonl").read_text().splitlines()]
    assert [(r["event"], r["role"], r["command"]) for r in records] == [
        ("bash_command_authorized", "tester", TESTER_COMMAND),
        ("bash_command_denied", "tester", PIPED["command"]),
        ("bash_command_denied", "tester", TESTER_COMMAND)]


@pytest.mark.parametrize("role", ["planner", "implementer", "reviewer", "project-manager"])
def test_hook_exact_scope_applies_only_to_the_tester_child(tmp_path, role):
    env = _module().child_environment(role, tmp_path / "run")
    assert _hook_decision(env, "git diff") is None


# --- D18: Tester command rules in the orchestrated Tester prompt ---------------------

def _section_text(prompt, heading):
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", prompt, re.S | re.M)
    return match.group(1).strip() if match else None


@pytest.mark.parametrize("review", [False, True], ids=["four-role", "five-role"])
def test_tester_prompt_carries_the_command_rules_only_for_the_tester(harness, review):
    code, run_dir, _ = _run_and_state(harness, _tester("PASS"), review=review)
    assert code == 0
    rules = harness.orchestrate.TESTER_COMMAND_RULES
    calls = harness.calls()
    tester_prompts = [c["prompt"] for c in calls if c["role"] == "tester"]
    assert len(tester_prompts) == 1
    assert _section_text(tester_prompts[0], "Relevant Context") == rules
    assert tester_prompts[0].count(rules) == 1
    for call in calls:
        if call["role"] != "tester":
            assert "Command rules for this session" not in call["prompt"], call["role"]
            assert rules not in call["prompt"], call["role"]
    saved = (run_dir / next(e["label"] for e in harness.state(run_dir)["executions"]
                            if e["role"] == "tester")).with_suffix(".prompt.md").read_text()
    assert saved == tester_prompts[0]


def test_tester_rules_are_present_on_every_tester_attempt(harness):
    code, _, _ = _run_and_state(harness, _tester("FAIL"), _tester("PASS"))
    assert code == 0
    prompts = [c["prompt"] for c in harness.calls() if c["role"] == "tester"]
    assert len(prompts) == 2
    assert all(_section_text(p, "Relevant Context") == _module().TESTER_COMMAND_RULES for p in prompts)
    assert "Tester FAIL report (attempt 1)" not in prompts[1]


def test_tester_command_rules_state_each_required_rule():
    rules = _module().TESTER_COMMAND_RULES
    assert f"exactly `{TESTER_COMMAND}`, character for character" in rules
    for operator in ("`|`", "| tail", "`>`", "`2>&1`", "`&&`", "`;`", "`||`", "`&`", "`cd`",
                     "extra arguments or flags"):
        assert operator in rules, operator
    assert "Read the results from that response" in rules
    assert "Never retry a denied command, in its original or any modified form" in rules
    assert "report FAIL, quote the error and stop" in rules
    assert [line[:2] for line in rules.splitlines()[1:]] == [f"{n}." for n in range(1, 9)]
    assert rules.count(f"`{TESTER_COMMAND}`") == 2  # rule 1 and rule 7, both the exact command


def _rule(number):
    lines = _module().TESTER_COMMAND_RULES.splitlines()[1:]
    return next(line for line in lines if line.startswith(f"{number}. "))


def test_gap1_bash_is_only_for_the_authorized_command():
    """MR-CAP-004 gap (a): the Tester used Bash for `git status` to check scope itself."""
    assert _rule(1).startswith("1. Use the Bash tool only to run the tests")
    rule = _rule(3)
    assert "Do not use Bash for anything else, including repository inspection" in rule
    for command in ("`git`", "`ls`", "`cat`", "`find`", "`head`", "`grep`"):
        assert command in rule, command
    assert "use only the read-only Read, Grep and Glob tools" in rule


def test_gap1_scope_verification_belongs_to_the_orchestrator():
    rule = _rule(4)
    assert "Do not verify file changes or repository scope yourself" in rule
    assert "The Orchestrator performs file-scope verification" in rule
    assert "enforces the approved file list" in rule


def test_gap1_tester_prompt_carries_the_measured_changed_files(harness):
    code, _, _ = _run_and_state(harness, _tester("PASS"), review=True)
    assert code == 0
    prompt = next(c["prompt"] for c in harness.calls() if c["role"] == "tester")
    assert _section_text(prompt, "Approved Files") == (
        "Changed files (measured by the Orchestrator): `pkg/feature.test.ts`")
    assert "Rely on the changed-file list it gives you" in _section_text(prompt, "Relevant Context")


def test_gap2_denial_is_never_retried_or_treated_as_permission():
    rule = _rule(6)
    assert "Any other command is denied before it runs and escalates this run to a human" in rule
    assert "Never retry a denied command, in its original or any modified form" in rule
    assert "never treat a denial as permission to run a different command" in rule


def test_gap2_authorized_command_still_runs_once_after_a_denial():
    """MR-CAP-004 gap (b): rule 4 was read as forbidding the authorized command after a denial."""
    rule = _rule(7)
    assert "A denial does not withdraw the authorized test command" in rule
    assert f"run `{TESTER_COMMAND}` exactly once" in rule
    assert "The run is still escalated" in rule
    assert "do not try another command" not in _module().TESTER_COMMAND_RULES


def test_gap2_denials_are_reported_accurately():
    rule = _rule(8)
    assert "Report every denial accurately: name the denied command and quote the error verbatim" in rule
    assert "do not work around it" in rule


MR_CAP_004_GIT = {"command": "git -C /workspace status --porcelain", "is_error": True,
                  "output": "PreToolUse:Bash hook error: bash_command_denied: ..."}


@pytest.mark.parametrize("bash, result, successes", [
    ((MR_CAP_004_GIT,), "FAIL", 0),             # MR-CAP-004 as it happened
    ((MR_CAP_004_GIT, EXACT_RUN), "PASS", 1),   # the behaviour rule 7 now asks for
], ids=["mr-cap-004-stopped", "denied-then-exact-run"])
@pytest.mark.parametrize("review", [False, True], ids=["four-role", "five-role"])
def test_denied_inspection_command_still_escalates(harness, bash, result, successes, review):
    """The rules change does not weaken the safeguard: any denied command escalates."""
    code, run_dir, state = _run_and_state(harness, _tester(result, bash=bash), _tester("PASS"),
                                          review=review)
    assert code == 1 and state["status"] == "escalated"
    assert "git -C /workspace status --porcelain" in state["status_reason"]
    tester = state["executions"][-1]
    assert tester["role"] == "tester" and tester["result"] == result
    assert tester["command_check"] == {"unauthorized": ["git -C /workspace status --porcelain"],
                                       "authorized_successes": successes, "parse_error": None}
    assert "project-manager" not in [e["role"] for e in state["executions"]]
    assert [c["role"] for c in harness.calls()].count("tester") == 1


@pytest.mark.parametrize("command", [
    "git -C /workspace status --porcelain", "git status", "git diff --stat", "ls tracker-api/src",
    "cat tracker-api/package.json", "find tracker-api -name '*.ts'", "head -5 tracker-api/package.json",
    "grep -r ScraperError tracker-api/src",
])
def test_hook_denies_repository_inspection_for_the_orchestrated_tester(tmp_path, command):
    env = _module().child_environment("tester", tmp_path / "run")
    assert _hook_decision(env, command) == "deny"


@pytest.mark.parametrize("operator", ["2>&1 | tail -40", "| tail", "> out.txt", "2>&1", "&& true",
                                      "; true", "|| true", "&", "-- --reporter=verbose"])
def test_every_operator_the_rules_forbid_is_denied_by_the_hook(tmp_path, operator):
    env = _module().child_environment("tester", tmp_path / "run")
    assert _hook_decision(env, f"{TESTER_COMMAND} {operator}") == "deny"
    assert _hook_decision(env, f"cd tracker-api && npm test") == "deny"


# --- parse_plan_files: only item-leading paths are approved (MR-CAP-005 defect) -------

MR_CAP_005_FILES = (
    "- `tracker-api/tests/services/search-runner.test.ts` (new): regression tests for "
    "`RunSearchError`. Its path mirrors `src/services/search-runner.ts` (decision-002).\n\n"
    "No other files should be created or modified."
)


def _plan_with_files(files_body):
    return f"### Plan\n1. Do it.\n\n### Files\n{files_body}\n\n### Risks\nNone.\n\n### Acceptance Check\nRun tests.\n"


def test_parse_plan_files_ignores_explanatory_paths_mr_cap_005():
    parse = _module().parse_plan_files
    assert parse(_plan_with_files(MR_CAP_005_FILES)) == ["tracker-api/tests/services/search-runner.test.ts"]


@pytest.mark.parametrize("run", ["MR-CAP-001-20261123T200956Z", "MR-CAP-002-20261123T214642Z",
                                 "MR-CAP-003-20261124T025448Z", "MR-CAP-004-20261124T032724Z"])
def test_parse_plan_files_reproduces_every_preserved_live_plan(run):
    """The fix must not change the approved list of any real plan (read-only replay)."""
    evidence = REPO_ROOT / "eval" / "capstone" / "evidence" / run
    recorded = json.loads((evidence / "run.json").read_text())["plan"]["files"]
    assert _module().parse_plan_files((evidence / "plan.md").read_text()) == recorded


@pytest.mark.parametrize("body, expected", [
    ("- `pkg/a.ts` - new regression test", ["pkg/a.ts"]),
    ("- `pkg/a.ts`: **new**. Tests for `b`.\n- `pkg/b.ts` (new): see `pkg/c.ts`.", ["pkg/a.ts", "pkg/b.ts"]),
    ("* `pkg/a.ts`\n+ `pkg/b.ts`", ["pkg/a.ts", "pkg/b.ts"]),
    ("1. `pkg/a.ts` - first\n2) `pkg/b.ts` - second", ["pkg/a.ts", "pkg/b.ts"]),
    ("- **`pkg/a.ts`** - bold path", ["pkg/a.ts"]),
    ("- pkg/a.ts - bare path", ["pkg/a.ts"]),
    ("- New files:\n  - `pkg/a.ts`\n  - `pkg/b.ts`", ["pkg/a.ts", "pkg/b.ts"]),
    ("- `./pkg/a.ts`\n- `pkg/a.ts` again", ["pkg/a.ts"]),
], ids=["dash", "two-items-with-explanations", "star-plus", "numbered", "bold", "bare", "nested", "dedupe"])
def test_parse_plan_files_accepts_listed_formats(body, expected):
    assert _module().parse_plan_files(_plan_with_files(body)) == expected


@pytest.mark.parametrize("body", [
    "No other files should be created or modified; `pkg/a.ts` stays as it is.",
    "`pkg/a.ts` is the only file.",
    "- Only test files change; see `pkg/a.ts`.",
    "- `a.ts` - no directory",
    "",
], ids=["prose", "unlisted-line", "item-not-led-by-path", "no-directory", "empty"])
def test_parse_plan_files_rejects_unlisted_or_unsafe_paths(body):
    assert _module().parse_plan_files(_plan_with_files(body)) == []


def test_parse_plan_files_only_reads_the_files_section():
    plan = _plan_with_files("- `pkg/a.ts` - new").replace("1. Do it.", "1. Edit `pkg/other.ts`.")
    plan = plan.replace("None.", "- `pkg/risk.ts` might break")
    assert _module().parse_plan_files(plan) == ["pkg/a.ts"]


def test_plan_step_records_only_the_listed_file_for_the_mr_cap_005_plan(harness):
    harness.scenario(planner=[{"text": _plan_with_files(
        "- `pkg/feature.test.ts` (new): tests. Its path mirrors `src/feature.ts` (decision-002).\n\n"
        "No other files should be created or modified.")}])
    run_dir = harness.plan("--review")
    assert harness.state(run_dir)["plan"]["files"] == ["pkg/feature.test.ts"]
    _approve(harness, run_dir)
    assert harness.state(run_dir)["approval"]["approved_files"] == ["pkg/feature.test.ts"]


def test_plan_without_a_listed_file_still_escalates(harness):
    harness.scenario(planner=[{"text": _plan_with_files("Only `pkg/feature.test.ts` changes.")}])
    assert harness.orchestrate.main(["plan", "--task", str(harness.repo().parent / "task.json")]) == 1
    run_dir = next((harness.repo().parent / "runs").iterdir())
    state = harness.state(run_dir)
    assert state["status"] == "escalated" and state["status_reason"] == "planner returned no file list"


def test_explanatory_path_is_not_an_approved_file_for_the_implementer(harness):
    """End to end: writing the explanatory path escalates as out of scope."""
    harness.scenario(
        planner=[{"text": _plan_with_files(
            "- `pkg/feature.test.ts` (new): tests. Its path mirrors `src/feature.ts`.")}],
        implementer=[{"text": "### Changes Made\nx", "write": {"pkg/feature.test.ts": "t\n", "src/feature.ts": "x\n"}}],
        tester=[_tester("PASS")])
    run_dir = harness.plan()
    _approve(harness, run_dir)
    assert harness.orchestrate.main(["run", "--run-dir", str(run_dir)]) == 1
    state = harness.state(run_dir)
    assert state["status"] == "escalated" and "src/feature.ts" in state["status_reason"]
