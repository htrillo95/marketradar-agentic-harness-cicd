#!/usr/bin/env python3
"""Governed MarketRadar orchestration entry point (Capstone Milestone 2).

Runs the CLAUDE.md standard sequence with real Claude Code sessions:

    plan     Planner -> stop for human approval
             (--review opts this run into the Code Reviewer stage)
    approve  a human records approval of the exact plan (hook-protected)
    run      Implementer -> Tester, FAIL routed back to the Implementer,
             escalation after --max-attempts; Project Manager on PASS.
             With review: Implementer -> Code Reviewer -> Tester; a REJECT
             (any High-severity finding, an explicit REJECT verdict, or no
             readable verdict) is routed back to the Implementer and counts
             against the same --max-attempts limit.

Each role runs as its own headless `claude --agent <role>` process with
AGENT_ROLE=<role> in its environment. The project `.mcp.json` passes that
value to the storage and retrieval servers, which authorize on it. Child
sessions load only those two servers (--strict-mcp-config) and run in
`dontAsk` mode with a per-role --allowedTools list, so anything not listed
is denied.

Tester command (governance D18): every child gets ORCHESTRATION_RUN_DIR, which
puts the Tester under the exact-command scope of scripts/enforce_bash_scope.py,
so any Bash command other than TESTER_COMMAND is denied before it runs. After
each Tester attempt the orchestrator also checks the transcript. Any other Bash
command, even a denied one, escalates the run, and a PASS without a successful
exact TESTER_COMMAND call is never accepted.

Evidence for every run is written outside the repository under
$ORCHESTRATION_RUNS_DIR (default ~/.marketradar/orchestration-runs):
run.json (manifest, Module 3 harness shape), events.jsonl, one stream-json
transcript per execution, and mcp-audit/*.jsonl from the MCP servers.

Usage:
    python3 scripts/orchestrate.py plan --task task.json [--model M] [--review]
    python3 scripts/orchestrate.py approve --run-dir DIR --approved-by NAME
    python3 scripts/orchestrate.py run --run-dir DIR [--max-attempts 2]
    python3 scripts/orchestrate.py status --run-dir DIR

task.json: {"task_id": "...", "goal": "...", "acceptance_criteria": [...],
            "context": "..." (optional)}
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import itertools
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

REPO_ROOT = Path(os.environ.get("ORCHESTRATOR_REPO_ROOT", Path(__file__).resolve().parent.parent))
TOOLS_ROOT = Path(__file__).resolve().parent.parent  # harness and audit helpers
MCP_CONFIG = REPO_ROOT / ".mcp.json"
CLAUDE_BIN = os.environ.get("ORCHESTRATOR_CLAUDE_BIN", "claude")
STEP_TIMEOUT_SECONDS = int(os.environ.get("ORCHESTRATOR_STEP_TIMEOUT", "900"))
TESTER_COMMAND = "npm --prefix tracker-api test"
TESTER_COMMAND_RULES = (
    "Command rules for this session (governance D18):\n"
    f"1. Use the Bash tool only to run the tests, with exactly `{TESTER_COMMAND}`, character "
    "for character, or with the run-tests skill, which runs the same command. Run it once.\n"
    "2. Do not add anything to it: no pipes (`|`, for example `| tail`), no redirects "
    "(`>`, `2>&1`), no shell operators (`&&`, `;`, `||`, `&`), no `cd`, and no extra "
    "arguments or flags.\n"
    "3. Do not use Bash for anything else, including repository inspection: no `git`, `ls`, "
    "`cat`, `find`, `head`, `grep` or similar commands. For any inspection you need, use only "
    "the read-only Read, Grep and Glob tools.\n"
    "4. Do not verify file changes or repository scope yourself. The Orchestrator performs "
    "file-scope verification: it measures which files changed and enforces the approved file "
    "list. Rely on the changed-file list it gives you.\n"
    "5. Do not shorten the output. The tool response returns the complete Vitest output, "
    "including the `Test Files` and `Tests` summary lines. Read the results from that response.\n"
    "6. Any other command is denied before it runs and escalates this run to a human, even if "
    "the tests pass. Never retry a denied command, in its original or any modified form, and "
    "never treat a denial as permission to run a different command.\n"
    "7. A denial does not withdraw the authorized test command. If you have not run it yet, run "
    f"`{TESTER_COMMAND}` exactly once, as in rule 1, so your report contains real test "
    "results. The run is still escalated.\n"
    "8. Report every denial accurately: name the denied command and quote the error verbatim "
    "under `### Findings`. If the authorized command itself is denied, errors or cannot run, "
    "do not work around it: report FAIL, quote the error and stop."
)

# Per-role tool grants for headless sessions. MCP grants mirror the server
# allow-lists (eval/test_orchestration.py checks this); everything else is
# denied by `dontAsk`. The agent definition's own `tools:` list still applies.
ROLE_PROFILES: dict[str, dict[str, list[str]]] = {
    "planner": {
        "allowed": ["Read", "Grep", "Glob", "mcp__retrieval__retrieve"],
        "disallowed": ["Edit", "Write", "NotebookEdit", "Bash"],
    },
    "implementer": {
        "allowed": ["Read", "Grep", "Glob", "Edit", "Write",
                    "mcp__retrieval__retrieve", "mcp__storage__write_entry"],
        "disallowed": ["Bash", "NotebookEdit"],
    },
    "tester": {
        "allowed": ["Read", "Grep", "Glob", "mcp__retrieval__retrieve",
                    f"Bash({TESTER_COMMAND})", "Skill(run-tests)"],
        "disallowed": ["Edit", "Write", "NotebookEdit"],
    },
    "project-manager": {
        "allowed": ["mcp__storage__write_entry"],
        "disallowed": ["Read", "Grep", "Glob", "Edit", "Write", "NotebookEdit", "Bash"],
    },
    # Same grants as the CI advisory review (scripts/run-reviewer.py
    # ALLOWED_TOOLS / DISALLOWED_TOOLS); eval/test_orchestration.py checks this.
    "reviewer": {
        "allowed": ["Read", "Grep", "Glob", "mcp__retrieval__retrieve"],
        "disallowed": ["Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch"],
    },
}

# Server-side role (AGENT_ROLE, allow-list key) -> Claude Code agent definition.
# The reviewer's definition was renamed so it does not override the image-level
# `code-reviewer` agent (CLAUDE.md); its runtime role stays `reviewer`.
AGENT_NAMES: dict[str, str] = {"reviewer": "marketradar-code-reviewer"}

REVIEW_EXPECTED_OUTPUT = (
    "Your findings, each with a `Severity: High`, `Severity: Medium` or `Severity: Low` line, "
    "the file and location, what is wrong, why it matters and a short fix; your overall "
    "assessment; and a final `### Verdict` section containing exactly APPROVE or REJECT. "
    "Any High-severity finding means REJECT."
)
HIGH_SEVERITY = re.compile(r"\bseverity\b[\s*_:|-]{0,8}high\b", re.I)


def agent_name(role: str) -> str:
    return AGENT_NAMES.get(role, role)


# --- small helpers ---------------------------------------------------------

def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _runs_root() -> Path:
    default = Path.home() / ".marketradar" / "orchestration-runs"
    return Path(os.environ.get("ORCHESTRATION_RUNS_DIR", default)).expanduser().resolve()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _event(run_dir: Path, event: str, **detail: Any) -> None:
    with open(run_dir / "events.jsonl", "a", encoding="utf-8") as log:
        log.write(json.dumps({"timestamp": _now(), "event": event, **detail}) + "\n")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_transcript(path: Path) -> dict[str, Any]:
    """Reuse the Module 3 harness parser for Claude Code stream-json transcripts."""
    harness = TOOLS_ROOT / "eval" / "module3" / "module-3.3-evaluation-harness"
    parser = _load_module("_parse_agent_execution", harness / "parse_agent_execution.py")
    return parser.parse_execution(path)


def _section(text: str, heading: str) -> str:
    """Body of a `### heading` section, up to the next `###` heading."""
    match = re.search(rf"^#+\s*{re.escape(heading)}\s*$(.*?)(?=^#+\s|\Z)", text, re.M | re.S)
    return match.group(1).strip() if match else ""


# One list item of the `### Files` section ("- `path` ...", "* ...", "1. ..."), optionally
# bolded. Only the path that leads the item is a listed file; paths later in the item
# are explanation (MR-CAP-005: "Its path mirrors `src/services/search-runner.ts`").
_PLAN_FILE_ITEM = re.compile(
    r"^\s*(?:[-*+]|\d+[.)])\s+(?:\*\*)?(?:`([^`\s]+)`|((?:[\w.-]+/)+[\w.-]+\.\w+))", re.M)


def parse_plan_files(plan: str) -> list[str]:
    """Repository-relative paths listed in the Planner's `### Files` section.

    Each list item contributes only the path it starts with. Paths mentioned later
    in an item, or in text outside a list item, are not approved files.
    """
    body = _section(plan, "Files")
    candidates = [quoted or bare for quoted, bare in _PLAN_FILE_ITEM.findall(body)]
    files = []
    for candidate in candidates:
        path = candidate.strip().lstrip("./")
        if "/" in path and not path.startswith("/") and ".." not in path and path not in files:
            files.append(path)
    return files


def parse_test_result(report: str) -> str:
    """PASS or FAIL from the Tester's `### Result` section; FAIL if unclear."""
    match = re.search(r"\b(PASS|FAIL)\b", _section(report, "Result"))
    return match.group(1) if match else "FAIL"


def parse_review_verdict(report: str) -> dict[str, Any]:
    """APPROVE or REJECT from a Code Reviewer report.

    REJECT when the report has any `Severity: High` finding, when its
    `### Verdict` section says REJECT, or when no verdict can be read
    (fail closed, as parse_test_result treats an unclear result as FAIL).
    """
    match = re.search(r"\b(APPROVE|REJECT)\b", _section(report, "Verdict"))
    explicit = match.group(1) if match else None
    high = len(HIGH_SEVERITY.findall(report))
    reasons = []
    if explicit == "REJECT":
        reasons.append("explicit REJECT verdict")
    if high:
        reasons.append(f"{high} High-severity finding(s)")
    if explicit is None:
        reasons.append("no readable APPROVE/REJECT verdict")
    return {"verdict": "REJECT" if reasons else "APPROVE", "explicit_verdict": explicit,
            "high_findings": high, "reasons": reasons}


def tester_command_check(transcript: Path) -> dict[str, Any]:
    """D18 safeguard: the Tester's Bash calls, judged against the exact authorized command.

    `unauthorized` lists every Bash command other than TESTER_COMMAND, whether or
    not the hook denied it. `authorized_successes` counts exact TESTER_COMMAND
    calls with a non-error response, the evidence a PASS must have.
    """
    try:
        calls = _parse_transcript(transcript).get("tool_calls", [])
    except (OSError, ValueError) as error:
        return {"unauthorized": [], "authorized_successes": 0, "parse_error": str(error)}
    bash = [call for call in calls if call.get("tool_name") == "Bash"]
    commands = [(call.get("arguments") or {}).get("command") for call in bash]
    return {
        "unauthorized": [command if isinstance(command, str) else repr(command)
                         for command in commands if command != TESTER_COMMAND],
        "authorized_successes": sum(
            1 for call, command in zip(bash, commands)
            if command == TESTER_COMMAND and call.get("is_error") is False),
        "parse_error": None,
    }


def snapshot(root: Path) -> dict[str, str]:
    """Hash of every tracked and untracked, non-ignored file in the repository."""
    listing = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "-co", "--exclude-standard"],
        check=True, capture_output=True,
    ).stdout.decode("utf-8").split("\0")
    hashes = {}
    for relative in filter(None, listing):
        path = root / relative
        if path.is_file():
            hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def changed_files(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))


# --- child sessions ----------------------------------------------------------

def child_environment(role: str, run_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "AGENT_ROLE": role,
        # Marks an orchestrated session; with AGENT_ROLE=tester it puts the
        # session under the exact-command scope of scripts/enforce_bash_scope.py (D18).
        "ORCHESTRATION_RUN_DIR": str(run_dir),
        "STORAGE_AUDIT_LOG": str(run_dir / "mcp-audit" / "storage-audit-log.jsonl"),
        "RETRIEVAL_AUDIT_LOG": str(run_dir / "mcp-audit" / "retrieval-audit-log.jsonl"),
        "SKILL_AUDIT_LOG": str(run_dir / "hook-audit" / "skill-audit-log.jsonl"),
        "BASH_AUDIT_LOG": str(run_dir / "hook-audit" / "bash-audit-log.jsonl"),
        "APPROVAL_AUDIT_LOG": str(run_dir / "hook-audit" / "approval-audit-log.jsonl"),
    })
    env.setdefault("STORAGE_STORE", str(_runs_root() / "storage-store.db"))
    env.setdefault("RETRIEVAL_REFERENCE_DIR", str(REPO_ROOT / ".memory" / "reference"))
    return env


def child_command(role: str, prompt: str, model: str | None) -> list[str]:
    profile = ROLE_PROFILES[role]
    command = [
        CLAUDE_BIN, "-p", prompt,
        "--agent", agent_name(role),
        "--mcp-config", str(MCP_CONFIG), "--strict-mcp-config",
        "--permission-mode", "dontAsk",
        "--allowedTools", ",".join(profile["allowed"]),
        "--disallowedTools", ",".join(profile["disallowed"]),
        "--no-session-persistence", "--verbose", "--output-format", "stream-json",
    ]
    if model:
        command += ["--model", model]
    return command


def run_role(run_dir: Path, state: dict, role: str, label: str, prompt: str) -> dict[str, Any]:
    """Run one role in its own Claude Code process and record the execution."""
    transcript = run_dir / f"{label}.jsonl"
    (run_dir / f"{label}.prompt.md").write_text(prompt, encoding="utf-8")
    before = snapshot(REPO_ROOT)
    started = _now()
    _event(run_dir, "role_started", role=role, label=label, agent_role_env=role)
    with open(transcript, "w", encoding="utf-8") as out, \
            open(run_dir / f"{label}.stderr.txt", "w", encoding="utf-8") as err:
        try:
            exit_code = subprocess.run(
                child_command(role, prompt, state.get("model")),
                cwd=REPO_ROOT, env=child_environment(role, run_dir),
                stdout=out, stderr=err, timeout=STEP_TIMEOUT_SECONDS,
            ).returncode
        except subprocess.TimeoutExpired:
            exit_code = "timeout"
    modified = changed_files(before, snapshot(REPO_ROOT))
    try:
        parsed = _parse_transcript(transcript)
        parse_error = None
    except (OSError, ValueError) as error:
        parsed, parse_error = {}, str(error)
    execution = {
        "role": role,
        "agent": agent_name(role),
        "label": label,
        "agent_role_env": role,
        "transcript": transcript.name,
        "allowed_tools": ROLE_PROFILES[role]["allowed"],
        "started_at": started,
        "ended_at": _now(),
        "exit_code": exit_code,
        "session_id": parsed.get("session_id"),
        "status": parsed.get("status"),
        "is_error": parsed.get("is_error"),
        "total_cost_usd": parsed.get("total_cost_usd"),
        "available_tools": parsed.get("available_tools"),
        "tool_calls": [
            {"tool": call["tool_name"], "is_error": call["is_error"]}
            for call in parsed.get("tool_calls", [])
        ],
        "permission_denials": parsed.get("permission_denials", []),
        "files_changed": modified,
        "parse_error": parse_error,
    }
    state["executions"].append(execution)
    _event(run_dir, "role_finished", role=role, label=label, exit_code=exit_code,
           status=execution["status"], files_changed=modified)
    execution["final_response"] = parsed.get("final_response") or ""
    return execution


def _succeeded(execution: dict) -> bool:
    return execution["exit_code"] == 0 and execution["status"] == "success" and not execution["is_error"]


# --- prompts (handoff-orchestrator-to-subagent.md template) -----------------

def handoff(role: str, goal: str, criteria: list[str], context: str, files: str,
            previous: str, expected: str) -> str:
    criteria_text = "\n".join(f"- {item}" for item in criteria)
    return (
        f"## Role\n{role}\n\n## Goal\n{goal}\n\n## Acceptance Criteria\n{criteria_text}\n\n"
        f"## Relevant Context\n{context or 'None beyond the repository.'}\n\n"
        f"## Approved Files\n{files}\n\n## Previous Result\n{previous or 'None.'}\n\n"
        f"## Expected Output\n{expected}\n"
    )


# --- state -------------------------------------------------------------------

def load_state(run_dir: Path) -> dict:
    return _read_json(run_dir / "run.json")


def save_state(run_dir: Path, state: dict) -> None:
    state["updated_at"] = _now()
    clean = {**state, "executions": [
        {k: v for k, v in e.items() if k != "final_response"} for e in state["executions"]
    ]}
    _write_json(run_dir / "run.json", clean)


def _finish(run_dir: Path, state: dict, status: str, reason: str) -> int:
    state["status"] = status
    state["status_reason"] = reason
    state["mcp_actions"] = collect_mcp_actions(run_dir)
    save_state(run_dir, state)
    _event(run_dir, f"run_{status}", reason=reason)
    print(f"[orchestrate] {status}: {reason}\n[orchestrate] evidence: {run_dir}")
    return 0 if status in {"complete", "awaiting_approval"} else 1


def collect_mcp_actions(run_dir: Path) -> dict:
    """Server-side audit records, via the CI audit-trail builder's reader."""
    builder = _load_module("_build_audit_trail", TOOLS_ROOT / "scripts" / "build-audit-trail.py")
    return builder.mcp_actions(run_dir)


# --- commands ----------------------------------------------------------------

def cmd_plan(args) -> int:
    task = _read_json(Path(args.task))
    for field in ("task_id", "goal", "acceptance_criteria"):
        if not task.get(field):
            sys.exit(f"task file is missing '{field}'")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", task["task_id"]):
        sys.exit("task_id may contain only letters, digits, '.', '_' and '-'")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = _runs_root() / f"{task['task_id']}-{stamp}"
    for sub in ("", "mcp-audit", "hook-audit"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
    review = bool(getattr(args, "review", False))
    state = {
        "run_id": run_dir.name, "kind": "orchestrated_run", "task": task,
        "task_description": task["goal"], "model": args.model,
        "expected_roles": ["planner", "implementer"] + (["reviewer"] if review else [])
                          + ["tester", "project-manager"],
        "review_required": review,
        "status": "planning", "created_at": _now(), "executions": [],
    }
    save_state(run_dir, state)
    _event(run_dir, "run_created", task_id=task["task_id"])

    prompt = handoff(
        "planner", task["goal"], task["acceptance_criteria"], task.get("context", ""),
        "None. You do not modify files. Name every file the Implementer should create or "
        "modify under `### Files` as a repository-relative path in backticks.",
        "", "The `### Plan`, `### Files`, `### Risks` and `### Acceptance Check` sections from your definition.",
    )
    execution = run_role(run_dir, state, "planner", "01-planner", prompt)
    if not _succeeded(execution):
        return _finish(run_dir, state, "failed", "planner session did not complete successfully")
    if execution["files_changed"]:
        return _finish(run_dir, state, "escalated",
                       f"planner changed files: {execution['files_changed']}")
    plan = execution["final_response"]
    files = parse_plan_files(plan)
    if not files:
        return _finish(run_dir, state, "escalated", "planner returned no file list")
    (run_dir / "plan.md").write_text(plan, encoding="utf-8")
    state["plan"] = {"path": "plan.md", "sha256": _sha256(plan), "files": files}
    return _finish(run_dir, state, "awaiting_approval",
                   f"human approval required: python3 scripts/orchestrate.py approve "
                   f"--run-dir {run_dir} --approved-by <name>")


def cmd_approve(args) -> int:
    run_dir = Path(args.run_dir).resolve()
    state = load_state(run_dir)
    if state["status"] != "awaiting_approval":
        sys.exit(f"run is '{state['status']}', not awaiting approval")
    plan = (run_dir / "plan.md").read_text(encoding="utf-8")
    if _sha256(plan) != state["plan"]["sha256"]:
        sys.exit("plan.md changed after planning; refusing to approve")
    approval = {
        "approved_by": args.approved_by, "approved_at": _now(),
        "plan_sha256": state["plan"]["sha256"], "approved_files": state["plan"]["files"],
        "review_required": bool(state.get("review_required", False)),
    }
    _write_json(run_dir / "approval.json", approval)
    state["status"] = "approved"
    state["approval"] = approval
    save_state(run_dir, state)
    _event(run_dir, "plan_approved", approved_by=args.approved_by, plan_sha256=approval["plan_sha256"],
           review_required=approval["review_required"])
    print(f"[orchestrate] approved by {args.approved_by}; files: {approval['approved_files']}; "
          f"code review stage: {'enabled' if approval['review_required'] else 'not enabled'}")
    return 0


def check_approval(run_dir: Path, state: dict) -> str | None:
    """Return a refusal reason, or None when the approved plan may run."""
    approval_path = run_dir / "approval.json"
    if state.get("status") != "approved" or not approval_path.exists():
        return "no human approval recorded for this plan"
    approval = _read_json(approval_path)
    plan = (run_dir / "plan.md").read_text(encoding="utf-8")
    if approval.get("plan_sha256") != _sha256(plan) or approval.get("plan_sha256") != state["plan"]["sha256"]:
        return "plan changed after approval"
    if approval.get("approved_files") != state["plan"]["files"]:
        return "approved file list does not match the plan"
    # Runs approved before the review stage existed have no flag: both default to False.
    if bool(approval.get("review_required", False)) != bool(state.get("review_required", False)):
        return "review requirement changed after approval"
    return None


def cmd_run(args) -> int:
    run_dir = Path(args.run_dir).resolve()
    state = load_state(run_dir)
    refusal = check_approval(run_dir, state)
    if refusal:
        _event(run_dir, "implementation_refused", reason=refusal)
        print(f"[orchestrate] refused: {refusal}", file=sys.stderr)
        return 2
    task = state["task"]
    plan = (run_dir / "plan.md").read_text(encoding="utf-8")
    approved = state["approval"]["approved_files"]
    state["status"] = "running"
    save_state(run_dir, state)

    review = bool(state.get("review_required", False))
    step_numbers = itertools.count(2)  # without review: 02-implementer-1, 03-tester-1, ...

    def label(name: str) -> str:
        return f"{next(step_numbers):02d}-{name}"

    findings = ""
    last_failure = "tester"
    review_report = None
    for attempt in range(1, args.max_attempts + 1):
        implementer = run_role(run_dir, state, "implementer", label(f"implementer-{attempt}"), handoff(
            "implementer", task["goal"], task["acceptance_criteria"],
            f"Task identifier: {task['task_id']}\n\nApproved plan:\n\n{plan}",
            "\n".join(f"- `{path}`" for path in approved) + "\n\nModify no other file.",
            findings, "The `### Changes Made`, `### Files Modified` and `### Notes` sections from your definition.",
        ))
        if not _succeeded(implementer):
            return _finish(run_dir, state, "failed", f"implementer attempt {attempt} did not complete")
        outside = [path for path in implementer["files_changed"] if path not in approved]
        if outside:
            return _finish(run_dir, state, "escalated",
                           f"implementer changed files outside the approved list: {outside}")

        if review:
            changed = ", ".join(f"`{path}`" for path in implementer["files_changed"]) or "none"
            reviewer = run_role(run_dir, state, "reviewer", label(f"reviewer-{attempt}"), handoff(
                "code reviewer", task["goal"], task["acceptance_criteria"],
                f"Task identifier: {task['task_id']}\n\nApproved plan:\n\n{plan}",
                f"Changed files (measured by the Orchestrator): {changed}\n\n"
                "Review these changes. You do not modify any file.",
                implementer["final_response"], REVIEW_EXPECTED_OUTPUT,
            ))
            if not _succeeded(reviewer):
                return _finish(run_dir, state, "failed", f"reviewer attempt {attempt} did not complete")
            if reviewer["files_changed"]:
                return _finish(run_dir, state, "escalated",
                               f"reviewer modified files: {reviewer['files_changed']}")
            decision = parse_review_verdict(reviewer["final_response"])
            recorded = {key: decision[key] for key in ("verdict", "explicit_verdict", "high_findings")}
            reviewer.update(recorded)
            state["executions"][-1].update(recorded)
            _event(run_dir, "review_result", attempt=attempt, **decision)
            if decision["verdict"] == "REJECT":
                findings = f"Code Reviewer REJECT report (attempt {attempt}):\n\n{reviewer['final_response']}"
                last_failure = "reviewer"
                _event(run_dir, "routed_to_implementer", attempt=attempt, source="reviewer")
                save_state(run_dir, state)
                continue
            review_report = reviewer

        tester = run_role(run_dir, state, "tester", label(f"tester-{attempt}"), handoff(
            "tester", task["goal"], task["acceptance_criteria"],
            TESTER_COMMAND_RULES,
            "Changed files (measured by the Orchestrator): "
            + (", ".join(f"`{path}`" for path in implementer["files_changed"]) or "none"),
            implementer["final_response"],
            "The `### Result` (PASS or FAIL), `### Validation Performed`, `### Findings` and "
            "`### Failed Acceptance Criteria` sections from your definition.",
        ))
        if not _succeeded(tester):
            return _finish(run_dir, state, "failed", f"tester attempt {attempt} did not complete")
        if tester["files_changed"]:
            return _finish(run_dir, state, "escalated",
                           f"tester modified files: {tester['files_changed']}")
        result = parse_test_result(tester["final_response"])
        tester["result"] = result
        state["executions"][-1]["result"] = result
        _event(run_dir, "test_result", attempt=attempt, result=result)
        check = tester_command_check(run_dir / tester["transcript"])
        state["executions"][-1]["command_check"] = check
        _event(run_dir, "tester_command_check", attempt=attempt, **check)
        if check["unauthorized"]:
            return _finish(run_dir, state, "escalated",
                           f"tester attempt {attempt} ran or attempted Bash commands other than "
                           f"`{TESTER_COMMAND}`: {check['unauthorized']}")
        if result == "PASS" and not check["authorized_successes"]:
            return _finish(run_dir, state, "escalated",
                           f"tester attempt {attempt} reported PASS without a successful "
                           f"`{TESTER_COMMAND}` call in its transcript")
        if result == "PASS":
            break
        findings = f"Tester FAIL report (attempt {attempt}):\n\n{tester['final_response']}"
        last_failure = "tester"
        _event(run_dir, "routed_to_implementer", attempt=attempt)
    else:
        if last_failure == "reviewer":
            reason = f"code review still rejecting after {args.max_attempts} implementer attempts"
        else:
            reason = f"tests still failing after {args.max_attempts} implementer attempts"
        return _finish(run_dir, state, "escalated", reason)

    if review:
        review_outcome = (
            f"Code review outcome: APPROVE (Code Reviewer attempt {attempt}; explicit verdict "
            f"{review_report['explicit_verdict']}; {review_report['high_findings']} High-severity "
            f"findings).\n\nCode review report:\n{review_report['final_response']}\n\n"
        )
    else:
        review_outcome = (
            "Code review outcome: NOT_RUN. The CLAUDE.md standard sequence has no review stage, "
            "and review was not requested for this task.\n\n"
        )
    outcome_key = f"task-outcome/{task['task_id']}"
    manager = run_role(run_dir, state, "project-manager", label("project-manager"), (
        f"Task identifier: {task['task_id']}\n\n"
        f"Implementation summary:\n{implementer['final_response']}\n\n"
        f"{review_outcome}"
        f"Test results (Tester attempt {attempt}, PASS):\n{tester['final_response']}\n\n"
        "Requested final status: COMPLETE\n\n"
        f"Record the outcome with key `{outcome_key}`."
    ))
    state["outcome_record"] = verify_outcome_record(run_dir, outcome_key)
    if not _succeeded(manager):
        return _finish(run_dir, state, "complete",
                       "tester PASS; project-manager session did not complete (outcome not verified)")
    return _finish(run_dir, state, "complete", "tester PASS")


def verify_outcome_record(run_dir: Path, key: str) -> dict:
    """Read the Project Manager's record back through the storage server as orchestrator."""
    script = (
        "import asyncio, json, sys\n"
        "from fastmcp import Client\n"
        "from fastmcp.client.transports import PythonStdioTransport\n"
        "async def main():\n"
        "    t = PythonStdioTransport(script_path=sys.argv[1], python_cmd=sys.executable, env=dict(__import__('os').environ))\n"
        "    async with Client(t) as c:\n"
        "        r = await c.call_tool('read_entry', {'key': sys.argv[2]})\n"
        "        print(r.content[0].text)\n"
        "asyncio.run(main())\n"
    )
    env = child_environment("orchestrator", run_dir)
    completed = subprocess.run(
        [sys.executable, "-c", script, str(TOOLS_ROOT / "mcp-servers" / "storage" / "server.py"), key],
        env=env, capture_output=True, text=True, timeout=120,
    )
    if completed.returncode != 0:
        return {"key": key, "verified": False, "error": completed.stderr.strip()[-500:]}
    value = json.loads(completed.stdout.strip().splitlines()[-1]).get("value")
    return {"key": key, "verified": bool(value), "value": value}


def cmd_status(args) -> int:
    print(json.dumps(load_state(Path(args.run_dir).resolve()), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--task", required=True)
    plan.add_argument("--model")
    plan.add_argument("--review", action="store_true",
                      help="add the Code Reviewer stage between Implementer and Tester")
    approve = sub.add_parser("approve")
    approve.add_argument("--run-dir", required=True)
    approve.add_argument("--approved-by", required=True)
    run = sub.add_parser("run")
    run.add_argument("--run-dir", required=True)
    run.add_argument("--max-attempts", type=int, default=2)
    status = sub.add_parser("status")
    status.add_argument("--run-dir", required=True)
    args = parser.parse_args(argv)
    return {"plan": cmd_plan, "approve": cmd_approve, "run": cmd_run, "status": cmd_status}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
