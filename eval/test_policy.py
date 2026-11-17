from pathlib import Path
import re


ROOT = Path(".")


def test_governance_policy_exists():
    assert (ROOT / "docs" / "governance-policy.md").exists(), (
        "Missing docs/governance-policy.md"
    )


def test_ci_step_design_exists():
    assert (ROOT / "docs" / "ci-step-design.md").exists(), (
        "Missing docs/ci-step-design.md"
    )


def test_workflow_exists_at_repo_root():
    assert (ROOT / ".github" / "workflows" / "ci.yml").exists(), (
        "Workflow must live at .github/workflows/ci.yml"
    )


def test_workflow_does_not_hardcode_anthropic_key():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    forbidden_patterns = [
        r"sk-ant-[A-Za-z0-9_\-]+",
        r"ANTHROPIC_API_KEY\s*=\s*sk-",
    ]

    for pattern in forbidden_patterns:
        assert not re.search(pattern, workflow), (
            "Do not hardcode Anthropic keys in the workflow. "
            "Use ${{ secrets.ANTHROPIC_API_KEY }}."
        )


def test_policy_gate_is_not_continue_on_error():
    import yaml

    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    job = workflow["jobs"].get("policy-gate")
    assert job is not None, "Missing policy-gate job"
    assert job.get("continue-on-error") is not True, "policy-gate must remain a real gate, not advisory."
    for step in job.get("steps", []):
        assert step.get("continue-on-error") is not True, (
            f"policy-gate step {step.get('name')!r} must not use continue-on-error."
        )


# ---------------------------------------------------------------------------
# Module 4.1 Enforcement Layer 2: MCP server allow-lists.
#
# The expected matrix below is hard-coded from the per-role "MCP server and
# operation access" tables in docs/governance-policy.md. It is deliberately
# not parsed from the policy: changing a grant requires changing the policy,
# the allow-list and this matrix together.
#
# Each test loads a fresh copy of the server module with AGENT_ROLE and all
# file paths pointed at pytest's tmp_path, then calls the tools through an
# in-memory fastmcp Client. Nothing is written inside the repository.
# ---------------------------------------------------------------------------

import asyncio
import importlib.util
import json
import sys
import uuid

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError


SERVERS_DIR = Path(__file__).resolve().parent.parent / "mcp-servers"

RUNTIME_ROLES = [
    "planner",
    "implementer",
    "tester",
    "reviewer",
    "project-manager",
    "orchestrator",
    "dependency-auditor",
]

STORAGE_OPS = [
    "write_entry",
    "read_entry",
    "list_entries",
    "update_entry",
    "delete_entry",
    "audit_read",
]

EXPECTED_STORAGE_GRANTS = {
    "planner": set(),
    "implementer": {"write_entry"},
    "tester": set(),
    "reviewer": set(),
    "project-manager": {"write_entry"},
    "orchestrator": {"read_entry", "list_entries", "audit_read"},
    "dependency-auditor": set(),
}

# None means retrieval is denied.
EXPECTED_RETRIEVE_CEILING = {
    "planner": "internal",
    "implementer": "internal",
    "tester": "internal",
    "reviewer": "internal",
    "project-manager": None,
    "orchestrator": None,
    "dependency-auditor": None,
}

# Agent-definition names and self-declared calling_role values that are not
# runtime roles. None of them may be authorized.
NON_RUNTIME_NAMES = ["marketradar-code-reviewer", "code-reviewer", "project_manager", ""]

STORAGE_ARGS = {
    "write_entry": {"key": "k", "value": "v"},
    "read_entry": {"key": "k"},
    "list_entries": {},
    "update_entry": {"key": "k", "value": "v"},
    "delete_entry": {"key": "k"},
    "audit_read": {},
}

# Existing success events written by mcp-servers/storage/server.py.
# audit_read writes no audit record on success.
STORAGE_SUCCESS_EVENT = {
    "write_entry": "storage_write",
    "read_entry": "storage_read",
    "list_entries": "storage_list",
    "update_entry": "storage_update",
    "delete_entry": "storage_delete",
    "audit_read": None,
}

# Matches all three corpus documents (public, internal, confidential).
CLASSIFICATION_QUERY = "public internal confidential"


def _name_id(name):
    return name or "<empty>"


def _load_server(server, role, monkeypatch, tmp_path, set_store=True):
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setenv("AGENT_ROLE", role)
    monkeypatch.setenv("STORAGE_AUDIT_LOG", str(tmp_path / "storage-audit-log.jsonl"))
    monkeypatch.setenv("RETRIEVAL_AUDIT_LOG", str(tmp_path / "retrieval-audit-log.jsonl"))
    if set_store:
        monkeypatch.setenv("STORAGE_STORE", str(tmp_path / "store.json"))
    else:
        monkeypatch.delenv("STORAGE_STORE", raising=False)
    spec = importlib.util.spec_from_file_location(
        f"_policy_test_{server}_{uuid.uuid4().hex}", SERVERS_DIR / server / "server.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call(module, tool, args):
    async def run():
        async with Client(module.mcp) as client:
            return await client.call_tool(tool, args)

    return asyncio.run(run())


def _audit(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _assert_storage_denied(module, role, op, tmp_path):
    with pytest.raises(ToolError) as excinfo:
        _call(module, op, STORAGE_ARGS[op])
    assert str(excinfo.value).startswith(
        f"authorization_denied: role '{role}' may not call '{op}'"
    )
    records = _audit(tmp_path / "storage-audit-log.jsonl")
    assert len(records) == 1
    assert records[0]["event"] == "authorization_denied"
    assert records[0]["role"] == role
    assert records[0]["operation"] == op


def _assert_retrieve_denied(module, role, tmp_path):
    with pytest.raises(ToolError) as excinfo:
        _call(module, "retrieve", {"query": CLASSIFICATION_QUERY})
    assert str(excinfo.value).startswith(
        f"authorization_denied: role '{role}' may not call 'retrieve': "
    )
    records = _audit(tmp_path / "retrieval-audit-log.jsonl")
    assert len(records) == 1
    assert records[0]["event"] == "authorization_denied"
    assert records[0]["role"] == role
    assert records[0]["operation"] == "retrieve"


@pytest.mark.parametrize("op", STORAGE_OPS)
@pytest.mark.parametrize("role", RUNTIME_ROLES)
def test_storage_authorization_matches_policy(role, op, monkeypatch, tmp_path):
    server = _load_server("storage", role, monkeypatch, tmp_path)
    if op in EXPECTED_STORAGE_GRANTS[role]:
        _call(server, op, STORAGE_ARGS[op])
        records = _audit(tmp_path / "storage-audit-log.jsonl")
        expected_event = STORAGE_SUCCESS_EVENT[op]
        if expected_event is None:
            assert records == []
        else:
            assert len(records) == 1
            assert records[0]["event"] == expected_event
            assert records[0]["role"] == role
            assert records[0]["operation"] == op
            assert records[0]["outcome"] == "success"
    else:
        _assert_storage_denied(server, role, op, tmp_path)


@pytest.mark.parametrize("role", RUNTIME_ROLES)
def test_retrieve_authorization_and_ceiling_match_policy(role, monkeypatch, tmp_path):
    server = _load_server("retrieval", role, monkeypatch, tmp_path)
    ceiling = EXPECTED_RETRIEVE_CEILING[role]
    if ceiling is None:
        _assert_retrieve_denied(server, role, tmp_path)
        return

    result = _call(server, "retrieve", {"query": CLASSIFICATION_QUERY})
    docs = json.loads(result.content[0].text)
    classifications = {doc["classification"] for doc in docs}
    assert "confidential" not in classifications
    assert classifications == {"public", "internal"}

    records = _audit(tmp_path / "retrieval-audit-log.jsonl")
    assert [r["event"] for r in records] == ["classification_withheld", "retrieval_success"]
    for record in records:
        assert record["role"] == role
        assert record["operation"] == "retrieve"
    assert "2 result(s)" in records[1]["detail"]
    assert ceiling in records[1]["detail"]


@pytest.mark.parametrize("op", STORAGE_OPS)
@pytest.mark.parametrize("name", NON_RUNTIME_NAMES, ids=_name_id)
def test_non_runtime_names_denied_storage(name, op, monkeypatch, tmp_path):
    server = _load_server("storage", name, monkeypatch, tmp_path)
    _assert_storage_denied(server, name, op, tmp_path)


@pytest.mark.parametrize("name", NON_RUNTIME_NAMES, ids=_name_id)
def test_non_runtime_names_denied_retrieve(name, monkeypatch, tmp_path):
    server = _load_server("retrieval", name, monkeypatch, tmp_path)
    _assert_retrieve_denied(server, name, tmp_path)


def test_storage_store_defaults_next_to_audit_log(monkeypatch, tmp_path):
    server = _load_server("storage", "implementer", monkeypatch, tmp_path, set_store=False)
    expected_store = tmp_path / "storage-store.json"
    assert server.STORE_PATH == expected_store
    _call(server, "write_entry", {"key": "k", "value": "v"})
    assert expected_store.exists()
    assert json.loads(expected_store.read_text(encoding="utf-8")) == {"k": "v"}


# ---------------------------------------------------------------------------
# Module 4.1 Enforcement Layer 3 and policy observability.
#
# - run-tests skill activation scope: the skill's Activation Scope table, the
#   agent definitions that hold the Skill tool, the hook registration, and the
#   hook's decisions (run as a subprocess with real stdin/stdout).
# - Container permissions: scripts/run-agent.sh launched with a fake docker
#   that records its arguments.
# - Observability: every audit record written on a denial or success carries
#   the same required fields across storage, retrieval and skill enforcement.
#
# These are deterministic tests. They do not start a Claude Code session.
# ---------------------------------------------------------------------------

import datetime
import os
import subprocess

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_HOOK = REPO_ROOT / "scripts" / "enforce_skill_scope.py"
RUN_TESTS_SKILL = REPO_ROOT / ".claude" / "skills" / "run-tests" / "SKILL.md"
AGENTS_DIR = REPO_ROOT / ".claude" / "agents"
PROJECT_SETTINGS = REPO_ROOT / ".claude" / "settings.json"
RUN_AGENT = REPO_ROOT / "scripts" / "run-agent.sh"

# Hard-coded from each role's "Skill activation scope" table for run-tests.
EXPECTED_RUN_TESTS_SCOPE = {
    "tester": "YES",
    "planner": "NO",
    "implementer": "NO",
    "reviewer": "NO",
    "project-manager": "NO",
    "orchestrator": "NO",
    "dependency-auditor": "NO",
}

# Agent definitions allowed to hold the Skill tool.
EXPECTED_SKILL_TOOL_HOLDERS = {"tester"}

# agent_type values the hook may receive; None is the main (orchestrator) session.
HOOK_AGENT_TYPES = [
    None,
    "planner",
    "implementer",
    "tester",
    "marketradar-code-reviewer",
    "project-manager",
    "general-purpose",
    "dependency-auditor",
]

# Hard-coded from each role's "Container permissions" line:
# launch name -> (AGENT_ROLE, workspace mode, /memory mounted)
EXPECTED_CONTAINER = {
    "planner": ("planner", "ro", False),
    "implementer": ("implementer", "rw", True),
    "tester": ("tester", "ro", False),
    "marketradar-code-reviewer": ("reviewer", "ro", False),
    "reviewer": ("reviewer", "ro", False),
    "project-manager": ("project-manager", "ro", False),
    "orchestrator": ("orchestrator", "rw", True),
    "dependency-auditor": ("dependency-auditor", "ro", False),
}

AUDIT_REQUIRED_FIELDS = {
    "timestamp",
    "event",
    "operation",
    "role",
    "outcome",
    "detail",
    "policy_reference",
}


def _frontmatter(path):
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} has no frontmatter"
    block = text.split("---\n", 2)[1]
    fields = {}
    for line in block.splitlines():
        if ":" in line and not line.startswith((" ", "-")):
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    return fields


def _tools(fields):
    return {tool.strip() for tool in fields.get("tools", "").split(",") if tool.strip()}


def _run_hook(payload_text, tmp_path, extra_env=None):
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path / "home"),
        "SKILL_AUDIT_LOG": str(tmp_path / "skill-audit-log.jsonl"),
        "CLAUDE_PROJECT_DIR": str(REPO_ROOT),
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-I", str(SKILL_HOOK)],
        input=payload_text,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _skill_payload(agent_type, skill):
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Skill", "tool_input": {"skill": skill}}
    if agent_type is not None:
        payload["agent_type"] = agent_type
    return json.dumps(payload)


def _expected_hook_decision(agent_type, skill):
    if agent_type == "tester":
        return "authorized" if skill == "run-tests" else "denied"
    if skill == "run-tests":
        return "denied"
    return None


def _deny_decision(result):
    return json.loads(result.stdout)["hookSpecificOutput"]


def _check_audit_record(record):
    missing = AUDIT_REQUIRED_FIELDS - set(record)
    assert not missing, f"audit record missing {sorted(missing)}: {record}"
    assert record["policy_reference"] == "docs/governance-policy.md"
    datetime.datetime.fromisoformat(record["timestamp"])


def test_run_tests_skill_defines_activation_scope():
    assert RUN_TESTS_SKILL.exists(), "Missing .claude/skills/run-tests/SKILL.md"
    fields = _frontmatter(RUN_TESTS_SKILL)
    assert fields.get("name") == "run-tests"
    assert fields.get("description")
    text = RUN_TESTS_SKILL.read_text(encoding="utf-8")
    assert "## Activation Scope" in text
    scope = {
        match.group(1): match.group(2)
        for match in re.finditer(r"^\|\s*`?([a-z-]+)`?[^|]*\|\s*(YES|NO)\s*\|", text, re.MULTILINE)
    }
    assert scope == EXPECTED_RUN_TESTS_SCOPE


def test_only_tester_holds_skill_tool():
    holders = set()
    for path in sorted(AGENTS_DIR.glob("*.md")):
        fields = _frontmatter(path)
        if "Skill" in _tools(fields):
            holders.add(fields.get("name"))
        assert "run-tests" not in fields.get("skills", ""), f"{path.name} preloads run-tests"
    assert holders == EXPECTED_SKILL_TOOL_HOLDERS


def test_reviewer_definition_has_no_skill_tool():
    fields = _frontmatter(AGENTS_DIR / "marketradar-code-reviewer.md")
    assert fields.get("name") == "marketradar-code-reviewer"
    assert "Skill" not in _tools(fields)


def test_project_settings_register_skill_hook():
    settings = json.loads(PROJECT_SETTINGS.read_text(encoding="utf-8"))
    commands = [
        hook.get("command", "")
        for entry in settings["hooks"]["PreToolUse"]
        if entry.get("matcher") == "Skill"
        for hook in entry.get("hooks", [])
        if hook.get("type") == "command"
    ]
    assert any("scripts/enforce_skill_scope.py" in command for command in commands)


@pytest.mark.parametrize("skill", ["run-tests", "summarize-session"])
@pytest.mark.parametrize("agent_type", HOOK_AGENT_TYPES, ids=lambda value: value or "<main-session>")
def test_skill_hook_enforces_run_tests_scope(agent_type, skill, tmp_path):
    result = _run_hook(_skill_payload(agent_type, skill), tmp_path)
    assert result.returncode == 0, result.stderr
    expected = _expected_hook_decision(agent_type, skill)
    records = _audit(tmp_path / "skill-audit-log.jsonl")
    if expected == "denied":
        decision = _deny_decision(result)
        assert decision["hookEventName"] == "PreToolUse"
        assert decision["permissionDecision"] == "deny"
        assert decision["permissionDecisionReason"].startswith("skill_activation_denied:")
        assert len(records) == 1
        assert records[0]["event"] == "skill_activation_denied"
        assert records[0]["skill"] == skill
        assert records[0]["role"] == (agent_type or "")
    elif expected == "authorized":
        assert result.stdout.strip() == ""
        assert [record["event"] for record in records] == ["skill_activation_authorized"]
    else:
        assert result.stdout.strip() == ""
        assert records == []


@pytest.mark.parametrize("skill_value", ["run-tests", "/run-tests", "run-tests --watch", "project:run-tests"])
def test_skill_hook_recognizes_run_tests_name_forms(skill_value, tmp_path):
    result = _run_hook(_skill_payload("marketradar-code-reviewer", skill_value), tmp_path)
    assert _deny_decision(result)["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "raw",
    ["", "not json", "[]", json.dumps({"tool_name": "Skill", "tool_input": "run-tests"})],
    ids=["empty", "not-json", "list", "string-tool-input"],
)
def test_skill_hook_fails_closed_on_unreadable_input(raw, tmp_path):
    result = _run_hook(raw, tmp_path)
    assert result.returncode == 0, result.stderr
    assert _deny_decision(result)["permissionDecision"] == "deny"


def test_skill_hook_never_writes_audit_inside_repository(tmp_path):
    inside = REPO_ROOT / "logs" / "skill-audit-log.jsonl"
    existed = inside.exists()
    result = _run_hook(
        _skill_payload("implementer", "run-tests"),
        tmp_path,
        {"SKILL_AUDIT_LOG": str(inside)},
    )
    assert _deny_decision(result)["permissionDecision"] == "deny"
    assert inside.exists() == existed
    fallback = tmp_path / "home" / ".local" / "state" / "marketradar" / "skill-audit-log.jsonl"
    written = fallback if fallback.exists() else Path("/logs/skill-audit-log.jsonl")
    records = _audit(written)
    assert records and records[-1]["event"] == "skill_activation_denied"


def _launch(launch_name, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake_docker = bin_dir / "docker"
    fake_docker.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$FAKE_DOCKER_ARGS"\n',
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    args_file = tmp_path / "docker-args.txt"
    env = {
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "FAKE_DOCKER_ARGS": str(args_file),
        "HOME": str(tmp_path),
    }
    result = subprocess.run(
        ["bash", str(RUN_AGENT), launch_name, "true"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    args = args_file.read_text(encoding="utf-8").splitlines() if args_file.exists() else None
    return result, args


@pytest.mark.parametrize("launch_name", sorted(EXPECTED_CONTAINER))
def test_run_agent_container_permissions_match_policy(launch_name, tmp_path):
    role, mode, memory = EXPECTED_CONTAINER[launch_name]
    result, args = _launch(launch_name, tmp_path)
    assert result.returncode == 0, result.stderr
    assert args is not None
    assert f"AGENT_ROLE={role}" in args
    assert f"{REPO_ROOT}:/workspace:{mode}" in args
    assert f"{REPO_ROOT}/.memory/knowledge:/workspace/.memory/knowledge:ro" in args
    assert f"{REPO_ROOT}/.memory/reference:/workspace/.memory/reference:ro" in args
    assert "agent-logs:/logs" in args
    assert ("agent-memory:/memory" in args) is memory
    assert "-p" not in args and "--publish" not in args
    assert not any(
        arg.startswith(("SLACK_", "ANTHROPIC_API_KEY")) or "docker.sock" in arg for arg in args
    )


@pytest.mark.parametrize("launch_name", ["code-reviewer", "unknown-role"])
def test_run_agent_rejects_roles_without_policy_entry(launch_name, tmp_path):
    result, args = _launch(launch_name, tmp_path)
    assert result.returncode != 0
    assert args is None
    assert "Launching" not in result.stdout


def test_storage_audit_records_are_complete(monkeypatch, tmp_path):
    server = _load_server("storage", "implementer", monkeypatch, tmp_path)
    _call(server, "write_entry", {"key": "k", "value": "v"})
    with pytest.raises(ToolError):
        _call(server, "delete_entry", {"key": "k"})
    records = _audit(tmp_path / "storage-audit-log.jsonl")
    assert [record["event"] for record in records] == ["storage_write", "authorization_denied"]
    for record in records:
        _check_audit_record(record)


def test_retrieval_denial_audit_record_is_complete(monkeypatch, tmp_path):
    server = _load_server("retrieval", "orchestrator", monkeypatch, tmp_path)
    with pytest.raises(ToolError):
        _call(server, "retrieve", {"query": CLASSIFICATION_QUERY})
    records = _audit(tmp_path / "retrieval-audit-log.jsonl")
    assert [record["event"] for record in records] == ["authorization_denied"]
    _check_audit_record(records[0])


def test_retrieval_withholding_audit_records_are_complete(monkeypatch, tmp_path):
    server = _load_server("retrieval", "implementer", monkeypatch, tmp_path)
    _call(server, "retrieve", {"query": CLASSIFICATION_QUERY})
    records = _audit(tmp_path / "retrieval-audit-log.jsonl")
    assert [record["event"] for record in records] == ["classification_withheld", "retrieval_success"]
    for record in records:
        _check_audit_record(record)


def test_skill_denial_audit_record_is_complete(tmp_path):
    _run_hook(_skill_payload("marketradar-code-reviewer", "run-tests"), tmp_path)
    records = _audit(tmp_path / "skill-audit-log.jsonl")
    assert len(records) == 1
    _check_audit_record(records[0])
    assert records[0]["skill"] == "run-tests"


# ---------------------------------------------------------------------------
# Policy observability: parse the per-role tables in docs/governance-policy.md
# and compare them with the enforcement artifacts, so a grant changed in only
# one place fails. Each comparison reports overgrants (granted by an artifact
# but not by the policy) and missing grants (granted by the policy but absent
# from the artifact). The hard-coded matrices above are also checked against
# the parsed tables.
# ---------------------------------------------------------------------------

POLICY_PATH = REPO_ROOT / "docs" / "governance-policy.md"
POLICY_ROLES = [
    "planner",
    "implementer",
    "tester",
    "marketradar-code-reviewer",
    "project-manager",
    "orchestrator",
    "dependency-auditor",
]


def _policy_subsection(body, title):
    match = re.search(rf"^### {re.escape(title)}[ \t]*$(.*?)(?=^### |\Z)", body, re.M | re.S)
    assert match, f"policy role section is missing '### {title}'"
    return match.group(1)


def _policy_table_rows(section):
    rows = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|") or set(line) <= set("|-: "):
            continue
        rows.append([cell.strip() for cell in line.strip("|").split("|")])
    return rows


def _parse_policy_roles():
    text = POLICY_PATH.read_text(encoding="utf-8")
    parts = re.split(r"^## Role: (\S+)[ \t]*$", text, flags=re.M)
    roles = {}
    for name, body in zip(parts[1::2], parts[2::2]):
        body = re.split(r"^## ", body, flags=re.M)[0]
        runtime = re.search(r"^\*\*Module 4 runtime role:\*\* `([^`]+)`", body, re.M)
        ceiling = re.search(r"^\*\*Maximum level:\*\*\s*(\w+)", body, re.M)
        container = re.search(r"^\*\*Container permissions:\*\*(.*)$", body, re.M)
        assert runtime and ceiling and container, f"policy role '{name}' is missing a required field"
        mcp = {
            row[0]: row[2].upper().startswith("YES")
            for row in _policy_table_rows(_policy_subsection(body, "MCP server and operation access"))
            if len(row) >= 3 and row[1] in ("storage", "retrieval")
        }
        skills = {
            row[0]: row[1].upper().startswith("YES")
            for row in _policy_table_rows(_policy_subsection(body, "Skill activation scope"))
            if len(row) >= 2 and row[1].upper().startswith(("YES", "NO"))
        }
        line = container.group(1)
        mode = "rw" if "workspace read-write" in line else "ro" if "workspace read-only" in line else None
        memory = True if "memory mounted" in line else False if "memory omitted" in line else None
        roles[name] = {
            "runtime": runtime.group(1),
            "storage": {op: mcp.get(op) for op in STORAGE_OPS},
            "retrieve": mcp.get("retrieve"),
            "ceiling": ceiling.group(1),
            "skills": skills,
            "container": (mode, memory),
        }
    return roles


def _grant_diff(label, artifact, policy):
    overgrants = sorted(set(artifact) - set(policy))
    missing = sorted(set(policy) - set(artifact))
    return f"{label}: overgrants {overgrants}, missing grants {missing}"


@pytest.fixture(scope="module")
def policy_roles():
    return _parse_policy_roles()


def test_policy_tables_parse_for_every_role(policy_roles):
    assert sorted(policy_roles) == sorted(POLICY_ROLES)
    for name, role in policy_roles.items():
        assert None not in role["storage"].values(), f"{name}: storage table incomplete"
        assert role["retrieve"] is not None, f"{name}: retrieve row missing"
        assert "run-tests" in role["skills"], f"{name}: run-tests row missing"
        assert None not in role["container"], f"{name}: container permissions not parsed"


def test_hard_coded_matrices_match_policy_tables(policy_roles):
    by_runtime = {role["runtime"]: role for role in policy_roles.values()}
    assert EXPECTED_STORAGE_GRANTS == {
        runtime: {op for op, granted in role["storage"].items() if granted}
        for runtime, role in by_runtime.items()
    }
    assert EXPECTED_RETRIEVE_CEILING == {
        runtime: (role["ceiling"] if role["retrieve"] else None)
        for runtime, role in by_runtime.items()
    }
    assert EXPECTED_RUN_TESTS_SCOPE == {
        runtime: ("YES" if role["skills"]["run-tests"] else "NO")
        for runtime, role in by_runtime.items()
    }
    for name, role in policy_roles.items():
        assert EXPECTED_CONTAINER[name] == (role["runtime"], *role["container"])


@pytest.mark.parametrize("op", STORAGE_OPS)
def test_storage_allow_list_matches_policy_tables(op, policy_roles):
    allow_list = json.loads((SERVERS_DIR / "storage" / "allow-list.json").read_text(encoding="utf-8"))
    assert set(allow_list) - {"explicit_denials"} == set(STORAGE_OPS)
    granted = {role["runtime"] for role in policy_roles.values() if role["storage"][op]}
    actual = set(allow_list[op])
    assert actual == granted, _grant_diff(op, actual, granted)


def test_retrieval_allow_list_matches_policy_tables(policy_roles):
    rules = json.loads((SERVERS_DIR / "retrieval" / "allow-list.json").read_text(encoding="utf-8"))["retrieve"]
    granted = {role["runtime"]: role["ceiling"] for role in policy_roles.values() if role["retrieve"]}
    actual = {name: rule.get("classification_ceiling") for name, rule in rules.items() if rule.get("granted")}
    assert set(actual) == set(granted), _grant_diff("retrieve", actual, granted)
    assert actual == granted, f"retrieve ceilings differ: allow-list {actual}, policy {granted}"
    known = {role["runtime"] for role in policy_roles.values()}
    assert set(rules) <= known, f"retrieval allow-list names roles without a policy entry: {sorted(set(rules) - known)}"
    assert set(rules) == known, f"roles not explicitly represented in the retrieval allow-list: {sorted(known - set(rules))}"


def test_run_tests_scope_matches_policy_tables(policy_roles):
    text = RUN_TESTS_SKILL.read_text(encoding="utf-8")
    skill_scope = {
        match.group(1)
        for match in re.finditer(r"^\|\s*`?([a-z-]+)`?[^|]*\|\s*YES\s*\|", text, re.MULTILINE)
    }
    granted = {role["runtime"] for role in policy_roles.values() if role["skills"]["run-tests"]}
    assert skill_scope == granted, _grant_diff("run-tests Activation Scope", skill_scope, granted)
    holders = set()
    for path in sorted(AGENTS_DIR.glob("*.md")):
        fields = _frontmatter(path)
        if "Skill" in _tools(fields):
            holders.add(fields.get("name"))
    expected_holders = {name for name, role in policy_roles.items() if role["skills"]["run-tests"]}
    assert holders == expected_holders, _grant_diff("Skill tool holders", holders, expected_holders)


@pytest.mark.parametrize("role_name", POLICY_ROLES)
def test_skill_hook_matches_policy_tables(role_name, policy_roles, tmp_path):
    agent_type = None if role_name == "orchestrator" else role_name
    result = _run_hook(_skill_payload(agent_type, "run-tests"), tmp_path)
    assert result.returncode == 0, result.stderr
    authorized = result.stdout.strip() == ""
    granted = policy_roles[role_name]["skills"]["run-tests"]
    assert authorized == granted, (
        f"hook {'authorizes' if authorized else 'denies'} run-tests for {role_name}; "
        f"policy says {'YES' if granted else 'NO'}"
    )


@pytest.mark.parametrize("role_name", POLICY_ROLES)
def test_container_permissions_match_policy_tables(role_name, policy_roles, tmp_path):
    role = policy_roles[role_name]
    mode, memory = role["container"]
    result, args = _launch(role_name, tmp_path)
    assert result.returncode == 0, result.stderr
    assert args is not None
    assert f"AGENT_ROLE={role['runtime']}" in args
    assert f"{REPO_ROOT}:/workspace:{mode}" in args
    assert ("agent-memory:/memory" in args) is memory


def test_launcher_accepts_only_policy_roles(policy_roles):
    script = RUN_AGENT.read_text(encoding="utf-8")
    accepted = set()
    for match in re.finditer(r"^\s{2}([a-z|-]+)\)[ \t]*$", script, re.M):
        accepted |= set(match.group(1).split("|"))
    known = set(policy_roles) | {role["runtime"] for role in policy_roles.values()}
    assert accepted <= known, f"run-agent.sh launches roles without a policy entry: {sorted(accepted - known)}"
    missing = set(policy_roles) - accepted
    assert not missing, f"policy roles that run-agent.sh cannot launch: {sorted(missing)}"


# ---------------------------------------------------------------------------
# Module 4.1 Exercise 1: dependency-auditor enforcement.
# ---------------------------------------------------------------------------

BASH_HOOK = REPO_ROOT / "scripts" / "enforce_bash_scope.py"
AUDITOR_DEFINITION = AGENTS_DIR / "dependency-auditor.md"

AUDITOR_ALLOWED_COMMANDS = [
    "npm audit --json --prefix tracker-api",
    "npm audit --json --prefix tracker-client",
    "npm outdated --json --prefix tracker-api",
    "npm outdated --json --prefix tracker-client",
    "npm --prefix tracker-api audit --json",
    "npm --prefix tracker-client outdated --json",
    "npm  audit   --json --prefix   tracker-api",
]

AUDITOR_DENIED_COMMANDS = [
    "npm install --prefix tracker-api",
    "npm ci --prefix tracker-client",
    "npm update --prefix tracker-api",
    "npm uninstall vite --prefix tracker-client",
    "npm audit fix --prefix tracker-api",
    "npm audit fix --force --json --prefix tracker-api",
    "npm test --prefix tracker-api",
    "npm --prefix tracker-api test",
    "npm audit --json",
    "npm audit --json --prefix ..",
    "npm audit --json --prefix tracker-api > audit.json",
    "npm audit --json --prefix tracker-api; rm -rf tracker-api",
    "npm audit --json --prefix tracker-api && npm install",
    "npm audit --json --prefix tracker-api\ntouch /workspace/should-fail.txt",
    "touch /workspace/should-fail.txt",
    "git commit -am x",
    "",
]


def _run_bash_hook(payload_text, tmp_path):
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path / "home"),
        "BASH_AUDIT_LOG": str(tmp_path / "bash-audit-log.jsonl"),
        "CLAUDE_PROJECT_DIR": str(REPO_ROOT),
    }
    return subprocess.run(
        [sys.executable, "-I", str(BASH_HOOK)],
        input=payload_text,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _bash_payload(agent_type, command):
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}
    if agent_type is not None:
        payload["agent_type"] = agent_type
    return json.dumps(payload)


def test_dependency_auditor_definition_matches_policy(policy_roles):
    fields = _frontmatter(AUDITOR_DEFINITION)
    assert fields.get("name") == "dependency-auditor"
    assert _tools(fields) == {"Read", "Grep", "Glob", "Bash"}
    assert "skills" not in fields
    assert "dependency-auditor" in policy_roles


def test_project_settings_register_bash_hook():
    settings = json.loads(PROJECT_SETTINGS.read_text(encoding="utf-8"))
    commands = [
        hook.get("command", "")
        for entry in settings["hooks"]["PreToolUse"]
        if entry.get("matcher") == "Bash"
        for hook in entry.get("hooks", [])
        if hook.get("type") == "command"
    ]
    assert any("scripts/enforce_bash_scope.py" in command for command in commands)


@pytest.mark.parametrize("command", AUDITOR_ALLOWED_COMMANDS)
def test_bash_hook_allows_auditor_audit_commands(command, tmp_path):
    result = _run_bash_hook(_bash_payload("dependency-auditor", command), tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""
    records = _audit(tmp_path / "bash-audit-log.jsonl")
    assert [record["event"] for record in records] == ["bash_command_authorized"]
    _check_audit_record(records[0])


@pytest.mark.parametrize("command", AUDITOR_DENIED_COMMANDS)
def test_bash_hook_denies_other_auditor_commands(command, tmp_path):
    result = _run_bash_hook(_bash_payload("dependency-auditor", command), tmp_path)
    assert result.returncode == 0, result.stderr
    decision = json.loads(result.stdout)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert decision["permissionDecisionReason"].startswith("bash_command_denied:")
    records = _audit(tmp_path / "bash-audit-log.jsonl")
    assert [record["event"] for record in records] == ["bash_command_denied"]
    assert records[0]["role"] == "dependency-auditor"
    _check_audit_record(records[0])


@pytest.mark.parametrize("agent_type", [None, "implementer", "tester", "marketradar-code-reviewer"])
def test_bash_hook_does_not_govern_other_roles(agent_type, tmp_path):
    result = _run_bash_hook(_bash_payload(agent_type, "npm install --prefix tracker-api"), tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""
    assert _audit(tmp_path / "bash-audit-log.jsonl") == []


@pytest.mark.parametrize("raw", ["", "not json", "[]"], ids=["empty", "not-json", "list"])
def test_bash_hook_fails_closed_on_unreadable_input(raw, tmp_path):
    result = _run_bash_hook(raw, tmp_path)
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_storage_explicit_denials_match_policy_tables(policy_roles):
    allow_list = json.loads((SERVERS_DIR / "storage" / "allow-list.json").read_text(encoding="utf-8"))
    denials = allow_list.get("explicit_denials", {})
    assert "dependency-auditor" in denials
    by_runtime = {role["runtime"]: role for role in policy_roles.values()}
    for runtime, ops in denials.items():
        assert runtime in by_runtime, f"explicit denial for a role without a policy entry: {runtime}"
        policy_denied = {op for op, granted in by_runtime[runtime]["storage"].items() if not granted}
        assert set(ops) == policy_denied, _grant_diff(f"{runtime} explicit denials", ops, policy_denied)
        for op in ops:
            assert runtime not in allow_list[op], f"{runtime} is both granted and denied {op}"


# ---------------------------------------------------------------------------
# Module 4.2: Evaluation Harness gating job (eval-gate).
# ---------------------------------------------------------------------------

import yaml

WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
DETERMINISTIC_EVAL_FILES = [
    "eval/test_deterministic.py",
    "eval/test_deterministic_router.py",
    "eval/test_deterministic_step.py",
    "eval/test_deterministic_dependency_audit.py",
]


def _workflow():
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _step(job, step_id):
    return next(step for step in job["steps"] if step.get("id") == step_id)


def _agent_affecting_pattern():
    classify = next(
        step for step in _workflow()["jobs"]["change-type-check"]["steps"] if step.get("id") == "classify"
    )
    match = re.search(r"grep -qE '([^']+)'; then\s*\n\s*echo \"agent-affecting=true\"", classify["run"])
    assert match, "change-type-check has no agent-affecting classification"
    return re.compile(match.group(1))


def test_eval_gate_reports_on_every_run_after_policy_gate():
    jobs = _workflow()["jobs"]
    job = jobs["eval-gate"]
    assert set(job["needs"]) == {"change-type-check", "policy-gate"}
    assert "if" not in job, "eval-gate must have no job-level condition so it reports on every run"
    assert job.get("continue-on-error") is not True
    assert "agent-affecting" in jobs["change-type-check"]["outputs"]


def test_eval_gate_deterministic_step_is_gating_and_runs_in_docker():
    step = _step(_workflow()["jobs"]["eval-gate"], "deterministic")
    assert step.get("continue-on-error") is not True
    assert "docker run" in step["run"] and "agentic_engineer_4" in step["run"]
    assert ":/workspace:ro" in step["run"]
    for path in DETERMINISTIC_EVAL_FILES:
        assert path in step["run"]
    assert "env" not in step


def test_eval_gate_rubric_step_is_advisory_and_runs_in_docker():
    step = _step(_workflow()["jobs"]["eval-gate"], "rubric")
    assert step.get("continue-on-error") is True
    assert "docker run" in step["run"] and "agentic_engineer_4" in step["run"]
    assert "--judge openrouter" in step["run"]
    assert "steps.deterministic.outcome == 'success'" in step["if"]


def test_openrouter_key_is_scoped_to_the_rubric_step_only():
    workflow = _workflow()
    assert "OPENROUTER_API_KEY" not in json.dumps(workflow.get("env", {}))
    holders = []
    for job_name, job in workflow["jobs"].items():
        assert "OPENROUTER_API_KEY" not in json.dumps(job.get("env", {})), f"{job_name} exposes the key job-wide"
        for step in job.get("steps", []):
            if "OPENROUTER_API_KEY" in json.dumps(step.get("env", {})):
                holders.append((job_name, step.get("id")))
    assert holders == [("eval-gate", "rubric")]
    rubric_env = _step(workflow["jobs"]["eval-gate"], "rubric")["env"]
    assert rubric_env["OPENROUTER_API_KEY"] == "${{ secrets.OPENROUTER_API_KEY }}"


def test_eval_reports_upload_always_and_ignore_missing():
    upload = next(
        step for step in _workflow()["jobs"]["eval-gate"]["steps"]
        if str(step.get("uses", "")).startswith("actions/upload-artifact")
    )
    assert upload["if"] == "always()"
    assert upload["with"]["if-no-files-found"] == "ignore"
    assert "deterministic-report.json" in upload["with"]["path"]
    assert "rubric-report.json" in upload["with"]["path"]


@pytest.mark.parametrize(
    "path, expected",
    [
        ("docs/ci-step-design.md", False),
        ("README.md", False),
        ("docs/module3/holdout-task-set.md", False),
        ("tracker-client/src/App.tsx", False),
        (".claude/agents/tester.md", True),
        (".claude/settings.json", True),
        ("scripts/enforce_bash_scope.py", True),
        ("mcp-servers/storage/allow-list.json", True),
        ("eval/test_policy.py", True),
        ("docs/governance-policy.md", True),
        ("CLAUDE.md", True),
        ("Dockerfile", True),
        (".github/workflows/ci.yml", True),
    ],
)
def test_change_classifier_separates_agent_affecting_from_docs_only(path, expected):
    assert bool(_agent_affecting_pattern().match(path)) is expected


def test_audit_trail_records_eval_gate():
    job = _workflow()["jobs"]["audit-trail"]
    assert "eval-gate" in job["needs"]
    assert job["if"] == "always()"
    command = next(step["run"] for step in job["steps"] if "build-audit-trail.py" in str(step.get("run", "")))
    assert "--eval-result" in command and "needs.eval-gate.result" in command


# ---------------------------------------------------------------------------
# Module 4.2: advisory code review (advisory-review job and
# scripts/run-reviewer.py). The script tests never start the claude CLI:
# subprocess.run is replaced by a fake that records the command.
# ---------------------------------------------------------------------------

RUN_REVIEWER = REPO_ROOT / "scripts" / "run-reviewer.py"
REVIEW_STEP_NAME = "Run advisory agentic review"
COMMENT_STEP_NAME = "Post review comment"
AUDIT_COMMENT_STEP_NAME = "Post audit summary comment"
DUMMY_KEY = "sk-test-dummy"


def _named_step(job, name):
    return next(step for step in job["steps"] if step.get("name") == name)


def _review_job():
    return _workflow()["jobs"]["advisory-review"]


def test_advisory_review_job_is_advisory_and_scoped():
    job = _review_job()
    assert job.get("continue-on-error") is True
    assert "pull_request" in job["if"]
    assert job["permissions"] == {"contents": "read", "pull-requests": "write"}


def test_only_comment_posting_jobs_have_permissions():
    workflow = _workflow()
    assert "permissions" not in workflow, "no workflow-level permissions block expected"
    holders = [name for name, job in workflow["jobs"].items() if "permissions" in job]
    assert holders == ["advisory-review", "audit-trail"]
    for name in holders:
        assert workflow["jobs"][name]["permissions"] == {"contents": "read", "pull-requests": "write"}, name


def test_advisory_review_checkout_does_not_persist_credentials():
    checkout = next(
        step for step in _review_job()["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout")
    )
    assert checkout["with"]["persist-credentials"] is False


def test_advisory_review_step_mounts_and_passes_changed_files_by_name():
    step = _named_step(_review_job(), REVIEW_STEP_NAME)
    script = step["run"]
    assert "docker run" in script
    assert '"$GITHUB_WORKSPACE":/workspace:ro' in script
    assert '"$GITHUB_WORKSPACE/ci-out":/out' in script
    assert re.search(r"-e CHANGED_FILES\s*\\?\s*$", script, re.M), "CHANGED_FILES must be passed by name"
    assert "CHANGED_FILES=" not in script
    assert step["env"]["CHANGED_FILES"] == "${{ needs.change-type-check.outputs.changed-files }}"
    assert "${{" not in script, "expressions must not be interpolated into the run script"


def test_anthropic_key_and_github_token_are_step_scoped():
    workflow = _workflow()
    assert "ANTHROPIC_API_KEY" not in json.dumps(workflow.get("env", {}))
    key_holders, token_holders = [], []
    for job_name, job in workflow["jobs"].items():
        job_level = json.dumps({k: v for k, v in job.items() if k != "steps"})
        assert "ANTHROPIC_API_KEY" not in job_level, f"{job_name} exposes ANTHROPIC_API_KEY job-wide"
        assert "GH_TOKEN" not in job_level and "github.token" not in job_level
        for step in job.get("steps", []):
            if "ANTHROPIC_API_KEY" in json.dumps(step):
                key_holders.append((job_name, step.get("name")))
            if "GH_TOKEN" in json.dumps(step) or "github.token" in json.dumps(step):
                token_holders.append((job_name, step.get("name")))
    assert key_holders == [("advisory-review", REVIEW_STEP_NAME)]
    assert token_holders == [
        ("advisory-review", COMMENT_STEP_NAME),
        ("audit-trail", AUDIT_COMMENT_STEP_NAME),
    ]
    review = _named_step(workflow["jobs"]["advisory-review"], REVIEW_STEP_NAME)
    assert review["env"]["ANTHROPIC_API_KEY"] == "${{ secrets.ANTHROPIC_API_KEY }}"
    for job_name, step_name in token_holders:
        comment = _named_step(workflow["jobs"][job_name], step_name)
        assert comment["env"]["GH_TOKEN"] == "${{ github.token }}"
        assert "GH_TOKEN" not in comment["run"] and "github.token" not in comment["run"]


def test_advisory_review_comment_step_posts_review_body():
    step = _named_step(_review_job(), COMMENT_STEP_NAME)
    assert "gh pr comment" in step["run"]
    assert "--body-file ci-out/review-output.md" in step["run"]
    assert step.get("continue-on-error") is True
    assert "always()" in step["if"]


def test_advisory_review_upload_always_and_ignore_missing():
    upload = next(
        step for step in _review_job()["steps"]
        if str(step.get("uses", "")).startswith("actions/upload-artifact")
    )
    assert upload["if"] == "always()"
    assert upload["with"]["if-no-files-found"] == "ignore"
    assert upload["with"]["path"] == "ci-artifacts/"


def _load_reviewer(monkeypatch, tmp_path, key):
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    work = tmp_path / "work"
    work.mkdir()
    (work / "sample.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    monkeypatch.chdir(work)
    monkeypatch.setenv("CHANGED_FILES", "sample.py")
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    if key is None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    else:
        monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    spec = importlib.util.spec_from_file_location(f"_run_reviewer_{uuid.uuid4().hex}", RUN_REVIEWER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = tmp_path / "out"
    monkeypatch.setattr(module, "OUT_DIR", out)
    return module, out


def _fake_claude(calls, returncode=0, stdout="", write_audit=False, configs=None):
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        # The script deletes the config directory after the run, so read it now.
        config_text = Path(cmd[cmd.index("--mcp-config") + 1]).read_text(encoding="utf-8")
        if configs is not None:
            configs.append(config_text)
        if write_audit:
            config = json.loads(config_text)
            log = Path(config["mcpServers"]["retrieval"]["env"]["RETRIEVAL_AUDIT_LOG"])
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"event": "retrieval_success", "role": "reviewer"}) + "\n")
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="boom " + DUMMY_KEY)

    return fake_run


def _reviewer_outputs(out):
    review = (out / "review-output.md").read_text(encoding="utf-8")
    audit_text = (out / "review-audit.json").read_text(encoding="utf-8")
    assert DUMMY_KEY not in review and DUMMY_KEY not in audit_text
    return review, json.loads(audit_text)


SUBAGENT_STDOUT = json.dumps({"result": "## Advisory Code Review\n- none", "is_error": False, "num_turns": 2})


def test_reviewer_without_key_does_not_invoke_claude(monkeypatch, tmp_path):
    module, out = _load_reviewer(monkeypatch, tmp_path, None)

    def forbidden(*args, **kwargs):
        raise AssertionError("claude must not be invoked without ANTHROPIC_API_KEY")

    monkeypatch.setattr(module.subprocess, "run", forbidden)
    assert module.main() == 0
    review, audit = _reviewer_outputs(out)
    assert "ANTHROPIC_API_KEY" in review
    assert audit["reviewer"]["ran"] is False
    assert audit["mcp"]["audit_log_present"] is False
    assert audit["mcp"]["servers_configured"] == []


def test_reviewer_runs_subagent_with_only_retrieval_mcp(monkeypatch, tmp_path):
    module, out = _load_reviewer(monkeypatch, tmp_path, DUMMY_KEY)
    calls, configs = [], []
    monkeypatch.setattr(module.subprocess, "run", _fake_claude(calls, stdout=SUBAGENT_STDOUT, configs=configs))
    assert module.main() == 0
    review, audit = _reviewer_outputs(out)
    assert review.startswith(module.REVIEW_HEADER)
    assert "marketradar-code-reviewer" in review.splitlines()[0]
    assert "## Advisory Code Review\n- none" in review

    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[0] == "claude" and "-p" in cmd
    assert cmd[cmd.index("--agent") + 1] == "marketradar-code-reviewer"
    assert "--strict-mcp-config" in cmd
    assert DUMMY_KEY not in json.dumps(cmd)
    config_path = Path(cmd[cmd.index("--mcp-config") + 1])
    assert out not in config_path.parents
    assert len(configs) == 1
    config = json.loads(configs[0])
    assert list(config["mcpServers"]) == ["retrieval"]
    assert config["mcpServers"]["retrieval"]["env"]["AGENT_ROLE"] == "reviewer"
    assert DUMMY_KEY not in configs[0]

    assert audit["reviewer"]["ran"] is True
    assert audit["reviewer"]["mechanism"] == "claude -p --agent marketradar-code-reviewer"
    assert audit["reviewer"]["num_turns"] == 2
    assert audit["mcp"]["servers_configured"] == ["retrieval"]
    assert audit["mcp"]["agent_role"] == "reviewer"
    assert audit["mcp"]["audit_log_present"] is False
    assert audit["mcp"]["audit_log"] is None
    assert audit["mcp"]["audit_log_records"] == 0


def test_reviewer_failure_still_exits_zero(monkeypatch, tmp_path):
    module, out = _load_reviewer(monkeypatch, tmp_path, DUMMY_KEY)
    calls = []
    monkeypatch.setattr(module.subprocess, "run", _fake_claude(calls, returncode=1))
    assert module.main() == 0
    review, audit = _reviewer_outputs(out)
    assert len(calls) == 1
    assert audit["reviewer"]["ran"] is False
    assert audit["reviewer"]["exit_code"] == 1
    assert "did not complete" in review


def test_reviewer_reports_mcp_audit_log_from_disk(monkeypatch, tmp_path):
    module, out = _load_reviewer(monkeypatch, tmp_path, DUMMY_KEY)
    calls = []
    monkeypatch.setattr(
        module.subprocess, "run", _fake_claude(calls, stdout=SUBAGENT_STDOUT, write_audit=True)
    )
    assert module.main() == 0
    _, audit = _reviewer_outputs(out)
    assert audit["mcp"]["audit_log_present"] is True
    assert audit["mcp"]["audit_log_records"] == 1
    assert audit["mcp"]["audit_log"] == "mcp-audit/retrieval-audit-log.jsonl"
    assert (out / "mcp-audit" / "retrieval-audit-log.jsonl").is_file()


@pytest.mark.parametrize(
    "payload, reason",
    [
        ({"result": "API Error: overloaded " + DUMMY_KEY, "is_error": True, "num_turns": 1}, "is_error"),
        ({"result": "partial", "is_error": False, "subtype": "error_max_turns", "num_turns": 20}, "error_max_turns"),
        ({"result": "", "is_error": False, "subtype": "success", "num_turns": 1}, "empty_result"),
    ],
    ids=["is-error", "error-max-turns", "empty-result"],
)
def test_reviewer_error_result_is_not_presented_as_review(payload, reason, monkeypatch, tmp_path):
    module, out = _load_reviewer(monkeypatch, tmp_path, DUMMY_KEY)
    calls = []
    monkeypatch.setattr(module.subprocess, "run", _fake_claude(calls, stdout=json.dumps(payload)))
    assert module.main() == 0
    review, audit = _reviewer_outputs(out)
    assert review.startswith(module.NOT_RUN_HEADER)
    assert "Generated by" not in review
    assert "did not produce a review" in review
    assert audit["reviewer"]["ran"] is False
    assert audit["reviewer"]["error"] == f"claude_reported_error: {reason}"
    assert audit["reviewer"]["is_error"] == payload["is_error"]
    assert audit["reviewer"]["num_turns"] == payload["num_turns"]
    assert audit["mcp"]["servers_configured"] == ["retrieval"]


def test_reviewer_missing_claude_reports_no_mcp_server(monkeypatch, tmp_path):
    module, out = _load_reviewer(monkeypatch, tmp_path, DUMMY_KEY)
    calls = []

    def missing(cmd, **kwargs):
        calls.append(cmd)
        raise FileNotFoundError("claude")

    monkeypatch.setattr(module.subprocess, "run", missing)
    assert module.main() == 0
    review, audit = _reviewer_outputs(out)
    assert len(calls) == 1
    assert review.startswith(module.NOT_RUN_HEADER)
    assert audit["reviewer"]["ran"] is False
    assert audit["reviewer"]["error"] == "FileNotFoundError"
    assert audit["mcp"]["servers_configured"] == []
    assert audit["mcp"]["audit_log_present"] is False
    assert not Path(calls[0][calls[0].index("--mcp-config") + 1]).parent.exists()


@pytest.mark.parametrize("returncode", [0, 1])
def test_reviewer_removes_temporary_mcp_config(returncode, monkeypatch, tmp_path):
    module, out = _load_reviewer(monkeypatch, tmp_path, DUMMY_KEY)
    calls = []
    monkeypatch.setattr(
        module.subprocess, "run", _fake_claude(calls, returncode=returncode, stdout=SUBAGENT_STDOUT)
    )
    assert module.main() == 0
    config_dir = Path(calls[0][calls[0].index("--mcp-config") + 1]).parent
    assert not config_dir.exists()


# Reviewer definition used by the advisory review must match the policy
# (Bash removed, D4) and the retrieval server's real parameters.

REVIEWER_DEFINITION = AGENTS_DIR / "marketradar-code-reviewer.md"
EXPECTED_REVIEWER_TOOLS = {"Read", "Grep", "Glob", "mcp__retrieval__retrieve"}


def _reviewer_body():
    return REVIEWER_DEFINITION.read_text(encoding="utf-8").split("---\n", 2)[2]


def _reviewer_script_tool_lists(monkeypatch):
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    spec = importlib.util.spec_from_file_location(f"_run_reviewer_{uuid.uuid4().hex}", RUN_REVIEWER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def split(value):
        return {tool.strip() for tool in value.split(",") if tool.strip()}

    return split(module.ALLOWED_TOOLS), split(module.DISALLOWED_TOOLS)


def test_reviewer_definition_tools_are_read_only():
    tools = _tools(_frontmatter(REVIEWER_DEFINITION))
    assert tools == EXPECTED_REVIEWER_TOOLS
    assert not tools & {"Bash", "Edit", "Write", "Skill"}


def test_reviewer_definition_uses_retrieval_server_parameters():
    body = _reviewer_body()
    assert "query" in body and "top_k" in body
    for stale in ("calling_role", "project_id", "classification_ceiling:"):
        assert stale not in body, f"reviewer definition still passes Module 3 argument {stale!r}"


def test_retrieval_tool_parameters_are_query_and_top_k(monkeypatch, tmp_path):
    import inspect

    server = _load_server("retrieval", "reviewer", monkeypatch, tmp_path)
    # With fastmcp installed, @mcp.tool wraps the function; without it the
    # fallback decorator returns the function itself.
    function = getattr(server.retrieve, "fn", server.retrieve)
    assert set(inspect.signature(function).parameters) == {"query", "top_k"}

    async def listed():
        async with Client(server.mcp) as client:
            return await client.list_tools()

    tool = next(tool for tool in asyncio.run(listed()) if tool.name == "retrieve")
    # Prefer the current attribute name; fall back to the deprecated one only if absent.
    schema = getattr(tool, "input_schema", None) or tool.inputSchema
    assert set(schema["properties"]) == {"query", "top_k"}


def test_run_reviewer_tool_lists_match_reviewer_definition(monkeypatch):
    allowed, disallowed = _reviewer_script_tool_lists(monkeypatch)
    tools = _tools(_frontmatter(REVIEWER_DEFINITION))
    assert allowed, "run-reviewer.py ALLOWED_TOOLS is empty"
    assert allowed <= tools, f"allowed in CI but not in the definition: {sorted(allowed - tools)}"
    assert not disallowed & tools, f"disallowed in CI but granted by the definition: {sorted(disallowed & tools)}"


# ---------------------------------------------------------------------------
# Module 4.2: audit trail (scripts/build-audit-trail.py and the audit-trail
# job). The script runs as a subprocess in tmp_path with fixture artifacts;
# nothing is written inside the repository.
# ---------------------------------------------------------------------------

BUILD_AUDIT_TRAIL = REPO_ROOT / "scripts" / "build-audit-trail.py"
AUDIT_SHA = "abc1234def5678"
SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.IGNORECASE)
RUN_LINK_ENV = ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID")
PR_ARGS = {
    "--sha": AUDIT_SHA,
    "--event": "pull_request",
    "--pr": "42",
    "--changed-files": "scripts/x.py eval/test_policy.py",
    "--touches-policy": "false",
    "--requires-governed-check": "true",
    "--classifier-result": "success",
    "--integrity-result": "success",
    "--policy-result": "success",
    "--governed-result": "success",
    "--review-result": "success",
    "--agent-affecting": "true",
    "--eval-result": "success",
    "--deterministic-outcome": "success",
    "--rubric-outcome": "success",
}
RUBRIC_SCORED = {
    "deterministic": {"passed": True},
    "rubric_ran": True,
    "rubric": {
        "scores": [
            {"dimension": "correctness", "score": 4, "justification": "JUSTIFICATION-TEXT",
             "pass_threshold": 3, "passed": True},
            {"dimension": "scope", "score": 2, "justification": "JUSTIFICATION-TEXT",
             "pass_threshold": 3, "passed": False},
        ],
        "total": 6,
        "maximum": 10,
        "overall_pass_threshold": 6,
        "passed": True,
    },
}
REVIEW_AUDIT = {
    "timestamp": "2026-11-15T00:00:00+00:00",
    "mode": "advisory",
    "changed_files": ["scripts/x.py", "logo.png"],
    "files": [
        {"path": "scripts/x.py", "included": True, "reason": "included", "chars": 10},
        {"path": "logo.png", "included": False, "reason": "skipped_non_text_extension", "chars": 0},
    ],
    "anthropic_key_present": True,
    "output": "/out/review-output.md",
    "exit_behavior": "always_zero_advisory",
    "reviewer": {
        "mechanism": "claude -p --agent marketradar-code-reviewer", "ran": True, "exit_code": 0,
        "is_error": False, "num_turns": 3, "total_cost_usd": 0.01, "subtype": "success", "error": None,
    },
    "mcp": {
        "servers_configured": ["retrieval"], "agent_role": "reviewer",
        "audit_log": "mcp-audit/retrieval-audit-log.jsonl", "audit_log_present": True, "audit_log_records": 2,
    },
}
MCP_SUCCESS = {
    "timestamp": "2026-11-15T00:00:01+00:00", "event": "retrieval_success", "operation": "retrieve",
    "role": "reviewer", "outcome": "retrieval_success", "detail": "2 result(s) returned at the 'internal' ceiling",
    "policy_reference": "docs/governance-policy.md",
}
MCP_DENIAL = {
    "timestamp": "2026-11-15T00:00:02+00:00", "event": "authorization_denied", "operation": "retrieve",
    "role": "project-manager", "outcome": "authorization_denied", "detail": "role is not granted retrieval",
    "policy_reference": "docs/governance-policy.md",
}


def _pytest_report(passed, failed):
    return {
        "exitcode": 1 if failed else 0,
        "summary": {"passed": passed, "failed": failed, "total": passed + failed},
        "tests": [{} for _ in range(passed + failed)],
    }


def _write_artifacts(tmp_path, rubric=RUBRIC_SCORED, review_audit=REVIEW_AUDIT, mcp_records=None):
    artifacts = tmp_path / "ci-artifacts"
    artifacts.mkdir()
    (artifacts / "policy-report.json").write_text(json.dumps(_pytest_report(200, 0)), encoding="utf-8")
    (artifacts / "deterministic-report.json").write_text(json.dumps(_pytest_report(14, 1)), encoding="utf-8")
    (artifacts / "governed-file-report.json").write_text(json.dumps(_pytest_report(5, 0)), encoding="utf-8")
    if rubric is not None:
        (artifacts / "rubric-report.json").write_text(json.dumps(rubric), encoding="utf-8")
    (artifacts / "review-audit.json").write_text(json.dumps(review_audit), encoding="utf-8")
    (artifacts / "review-output.md").write_text("REVIEW-BODY-TEXT\n", encoding="utf-8")
    if mcp_records is not None:
        log_dir = artifacts / "mcp-audit"
        log_dir.mkdir()
        lines = [json.dumps(record) for record in mcp_records] + ["not json"]
        (log_dir / "retrieval-audit-log.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return artifacts


def _run_audit_trail(tmp_path, overrides=None, extra_env=None):
    args = dict(PR_ARGS)
    args.update(overrides or {})
    args["--artifacts"] = str(tmp_path / "ci-artifacts")
    summary = tmp_path / "summary.md"
    args["--summary"] = str(summary)
    env = {
        name: value for name, value in os.environ.items()
        if name not in RUN_LINK_ENV and name != "GITHUB_STEP_SUMMARY" and not SECRET_NAME.search(name)
    }
    env.update(extra_env or {})
    command = [sys.executable, "-I", str(BUILD_AUDIT_TRAIL)]
    for name, value in args.items():
        if value is not None:
            command += [name, value]
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    safe_sha = re.sub(r"[^A-Za-z0-9._-]", "_", args["--sha"])
    trail_path = tmp_path / f"ci-audit-trail-{safe_sha}.json"
    trail_text = trail_path.read_text(encoding="utf-8") if trail_path.exists() else ""
    summary_text = summary.read_text(encoding="utf-8") if summary.exists() else ""
    return result, trail_text, summary_text


def _summary_check_rows(summary_text):
    """Map check name -> (kind, status, raw result, reason or None) from the summary table."""
    rows = {}
    pattern = r"^\| (\w+) \| (gating|advisory) \| ([^|]+?) \| ([^|]+?) \|(?: ([^|]+?) \|)?$"
    for match in re.finditer(pattern, summary_text, re.M):
        rows[match.group(1)] = match.group(2, 3, 4, 5)
    return rows


def test_audit_trail_full_pull_request_run(tmp_path):
    _write_artifacts(tmp_path, mcp_records=[MCP_SUCCESS, MCP_DENIAL])
    result, trail_text, summary = _run_audit_trail(tmp_path)
    assert result.returncode == 0, result.stderr
    trail = json.loads(trail_text)

    meta = trail["metadata"]
    assert (meta["sha"], meta["event"], meta["pull_request"]) == (AUDIT_SHA, "pull_request", "42")
    datetime.datetime.fromisoformat(meta["timestamp"])
    assert trail["change_classification"]["changed_files"] == ["scripts/x.py", "eval/test_policy.py"]

    checks = trail["checks"]
    assert set(checks) == {
        "change_classification", "pipeline_integrity", "policy_gate", "governed_file_gate", "eval_gate",
        "eval_deterministic", "eval_rubric", "advisory_review",
    }
    for check in checks.values():
        assert check["status"] == "success" and check["raw_result"] == "success"
        assert "reason" not in check
    assert {name for name, check in checks.items() if check["gating"]} == {
        "change_classification", "pipeline_integrity", "policy_gate", "governed_file_gate", "eval_gate",
        "eval_deterministic",
    }
    assert "subtype" not in trail["reviewer"]
    assert trail["results"]["policy_gate"] == "success"

    assert trail["policy_results"] == {
        "report_present": True, "exitcode": 0, "passed": 200, "failed": 0, "error": 0, "skipped": 0, "total": 200,
    }
    assert trail["deterministic_results"]["passed"] == 14
    assert trail["deterministic_results"]["failed"] == 1
    assert trail["governed_results"]["total"] == 5

    rubric = trail["rubric"]
    assert rubric["status"] == "scored"
    assert rubric["scores"][0] == {"dimension": "correctness", "score": 4, "pass_threshold": 3, "passed": True}
    assert (rubric["total"], rubric["maximum"], rubric["overall_pass_threshold"], rubric["passed"]) == (6, 10, 6, True)
    assert "JUSTIFICATION-TEXT" not in trail_text

    mcp = trail["mcp_actions"]
    assert mcp["mcp_audit_log_present"] is True
    assert "note" not in mcp
    assert mcp["unparseable_lines"] == 1
    assert [(a["server"], a["role"], a["operation"], a["outcome"]) for a in mcp["actions"]] == [
        ("retrieval", "reviewer", "retrieve", "retrieval_success"),
        ("retrieval", "project-manager", "retrieve", "authorization_denied"),
    ]
    assert all("detail" not in action for action in mcp["actions"])
    assert len(mcp["authorization_denials"]) == 1
    denial = mcp["authorization_denials"][0]
    assert denial["role"] == "project-manager" and denial["detail"] == "role is not granted retrieval"

    reviewer = trail["reviewer"]
    assert reviewer["ran"] is True
    assert reviewer["mechanism"] == "claude -p --agent marketradar-code-reviewer"
    assert reviewer["files_reviewed"] == ["scripts/x.py"]
    assert reviewer["mcp_audit_log_records"] == 2
    assert "review_audit" not in trail
    assert "REVIEW-BODY-TEXT" not in trail_text and "REVIEW-BODY-TEXT" not in summary
    assert trail["redactions"] == 0

    assert "abc1234" in summary and "PR #42" in summary
    assert "project-manager" in summary and "audit-trail" in summary
    assert "JUSTIFICATION-TEXT" not in summary


def test_audit_trail_push_docs_only_run_is_not_triggered(tmp_path):
    overrides = {
        "--event": "push",
        "--pr": "0",
        "--changed-files": "docs/ci-step-design.md",
        "--requires-governed-check": "false",
        "--agent-affecting": "false",
        "--review-result": "skipped",
        "--eval-result": "success",
        "--deterministic-outcome": "",
        "--rubric-outcome": "skipped",
    }
    result, trail_text, summary = _run_audit_trail(tmp_path, overrides)
    assert result.returncode == 0, result.stderr
    trail = json.loads(trail_text)
    checks = trail["checks"]
    assert checks["policy_gate"]["status"] == "success"
    for name in ("governed_file_gate", "eval_gate", "eval_deterministic", "eval_rubric", "advisory_review"):
        assert checks[name]["status"] == "not_triggered", name
    assert checks["eval_deterministic"]["raw_result"] == ""
    assert trail["rubric"] == {"status": "not_triggered"}
    assert trail["policy_results"]["report_present"] is False
    assert trail["mcp_actions"]["mcp_audit_log_present"] is False
    assert trail["mcp_actions"]["actions"] == []
    assert trail["mcp_actions"]["note"] == "No MCP audit log: the reviewer made no MCP calls or did not run."
    assert "not triggered" in summary
    assert "failure" not in summary.lower()


def test_audit_trail_rubric_not_scored(tmp_path):
    rubric = {
        "deterministic": {"passed": False},
        "rubric_ran": False,
        "rubric": None,
        "reason": "deterministic pre-check failed: execution_health",
    }
    _write_artifacts(tmp_path, rubric=rubric)
    result, trail_text, summary = _run_audit_trail(tmp_path, {"--rubric-outcome": "failure"})
    assert result.returncode == 0, result.stderr
    trail = json.loads(trail_text)
    assert trail["rubric"] == {
        "status": "not_scored",
        "reason": "deterministic pre-check failed: execution_health",
        "deterministic_precheck_passed": False,
    }
    assert trail["checks"]["eval_rubric"]["status"] == "failure"
    assert trail["checks"]["eval_rubric"]["gating"] is False
    assert "execution_health" in summary


def test_audit_trail_redacts_secret_values(tmp_path):
    secret = "supersecretvalue123"
    review_audit = json.loads(json.dumps(REVIEW_AUDIT))
    review_audit["unexpected_field"] = f"leaked {secret}"
    review_audit["reviewer"]["error"] = f"exit_code_1: {secret}"
    denial = dict(MCP_DENIAL, detail=f"denied for {secret}")
    _write_artifacts(tmp_path, review_audit=review_audit, mcp_records=[denial])
    result, trail_text, summary = _run_audit_trail(tmp_path, extra_env={"FAKE_API_TOKEN": secret})
    assert result.returncode == 0, result.stderr
    assert secret not in trail_text
    assert secret not in summary
    assert secret not in result.stdout and secret not in result.stderr
    trail = json.loads(trail_text)
    assert trail["redactions"] >= 1
    assert trail["reviewer"]["error"] == "exit_code_1: [REDACTED]"
    assert trail["mcp_actions"]["authorization_denials"][0]["detail"] == "denied for [REDACTED]"
    assert "FAKE_API_TOKEN" not in trail_text and "unexpected_field" not in trail_text


@pytest.mark.parametrize("scenario", ["pull-request", "push-docs-only"])
def test_audit_trail_summary_matches_check_statuses(scenario, tmp_path):
    if scenario == "pull-request":
        _write_artifacts(tmp_path, mcp_records=[MCP_SUCCESS])
        overrides = {"--deterministic-outcome": "failure", "--eval-result": "failure"}
    else:
        overrides = {
            "--event": "push", "--pr": "0", "--requires-governed-check": "false",
            "--agent-affecting": "false", "--review-result": "skipped",
            "--deterministic-outcome": "skipped", "--rubric-outcome": "skipped",
        }
    result, trail_text, summary = _run_audit_trail(tmp_path, overrides)
    assert result.returncode == 0, result.stderr
    checks = json.loads(trail_text)["checks"]
    rows = _summary_check_rows(summary)
    assert set(rows) == set(checks)
    for name, check in checks.items():
        kind, shown, raw, reason = rows[name]
        assert kind == ("gating" if check["gating"] else "advisory")
        expected = "not triggered" if check["status"] == "not_triggered" else check["status"]
        assert shown == expected, name
        assert raw == (check["raw_result"] or "(empty)"), name
        assert reason is None, name


def test_audit_trail_step_passes_changed_files_via_env():
    job = _workflow()["jobs"]["audit-trail"]
    step = next(step for step in job["steps"] if "build-audit-trail.py" in str(step.get("run", "")))
    assert step["env"]["CHANGED_FILES"] == "${{ needs.change-type-check.outputs.changed-files }}"
    assert '--changed-files "$CHANGED_FILES"' in step["run"]
    assert "outputs.changed-files" not in step["run"]
    assert '--classifier-result "${{ needs.change-type-check.result }}"' in step["run"]


def test_audit_trail_reports_failures_even_when_not_triggered(tmp_path):
    overrides = {
        "--event": "push", "--pr": "0",
        "--requires-governed-check": "false", "--agent-affecting": "false",
        "--governed-result": "failure", "--eval-result": "failure",
        "--deterministic-outcome": "cancelled", "--rubric-outcome": "failure",
        "--review-result": "failure",
    }
    result, trail_text, summary = _run_audit_trail(tmp_path, overrides)
    assert result.returncode == 0, result.stderr
    checks = json.loads(trail_text)["checks"]
    assert checks["governed_file_gate"]["status"] == "failure"
    assert checks["eval_gate"]["status"] == "failure"
    assert checks["eval_deterministic"]["status"] == "cancelled"
    assert checks["eval_rubric"]["status"] == "failure"
    assert checks["advisory_review"]["status"] == "failure"
    rows = _summary_check_rows(summary)
    assert rows["governed_file_gate"][1] == "failure"


def test_audit_trail_explains_skips_after_classifier_failure(tmp_path):
    overrides = {
        "--classifier-result": "failure", "--policy-result": "skipped", "--governed-result": "skipped",
        "--eval-result": "skipped", "--deterministic-outcome": "", "--rubric-outcome": "",
        "--review-result": "skipped", "--requires-governed-check": "", "--agent-affecting": "",
    }
    result, trail_text, summary = _run_audit_trail(tmp_path, overrides)
    assert result.returncode == 0, result.stderr
    checks = json.loads(trail_text)["checks"]
    assert checks["change_classification"] == {"status": "failure", "raw_result": "failure", "gating": True}
    # pipeline-integrity has no upstream job, so the classifier failure does not skip it.
    assert checks["pipeline_integrity"] == {"status": "success", "raw_result": "success", "gating": True}
    for name, check in checks.items():
        if name in ("change_classification", "pipeline_integrity"):
            continue
        assert check["status"] == "not_triggered", name
        assert check["reason"] == "skipped: change classification did not succeed", name
    rows = _summary_check_rows(summary)
    assert rows["policy_gate"][3] == "skipped: change classification did not succeed"


def test_audit_trail_explains_skips_after_policy_failure(tmp_path):
    overrides = {
        "--policy-result": "failure", "--governed-result": "skipped", "--eval-result": "skipped",
        "--deterministic-outcome": "", "--rubric-outcome": "", "--review-result": "skipped",
    }
    result, trail_text, _ = _run_audit_trail(tmp_path, overrides)
    assert result.returncode == 0, result.stderr
    checks = json.loads(trail_text)["checks"]
    assert checks["policy_gate"]["status"] == "failure"
    assert "reason" not in checks["change_classification"]
    for name in ("governed_file_gate", "eval_gate", "eval_deterministic", "eval_rubric", "advisory_review"):
        assert checks[name]["status"] == "not_triggered", name
        assert checks[name]["reason"] == "skipped: policy_gate did not succeed", name


def test_audit_trail_explains_rubric_skip_after_deterministic_failure(tmp_path):
    overrides = {"--eval-result": "failure", "--deterministic-outcome": "failure", "--rubric-outcome": "skipped"}
    result, trail_text, _ = _run_audit_trail(tmp_path, overrides)
    assert result.returncode == 0, result.stderr
    trail = json.loads(trail_text)
    rubric_check = trail["checks"]["eval_rubric"]
    assert rubric_check["status"] == "not_triggered"
    assert rubric_check["reason"] == "skipped: deterministic evaluation did not succeed"
    assert trail["rubric"] == {"status": "not_triggered"}


def test_audit_trail_rubric_failed_without_report(tmp_path):
    result, trail_text, summary = _run_audit_trail(tmp_path, {"--rubric-outcome": "failure"})
    assert result.returncode == 0, result.stderr
    trail = json.loads(trail_text)
    assert trail["checks"]["eval_rubric"]["status"] == "failure"
    assert trail["rubric"] == {"status": "failed_no_report"}
    assert "Rubric (advisory):** not triggered" not in summary


def test_audit_trail_tolerates_wrong_shapes_and_bad_jsonl(tmp_path):
    artifacts = tmp_path / "ci-artifacts"
    (artifacts / "mcp-audit").mkdir(parents=True)
    (artifacts / "policy-report.json").write_text(json.dumps({"exitcode": 0, "summary": ["x"]}), encoding="utf-8")
    (artifacts / "deterministic-report.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    (artifacts / "governed-file-report.json").write_text("{not json", encoding="utf-8")
    (artifacts / "rubric-report.json").write_text(
        json.dumps({"rubric_ran": True, "deterministic": "yes",
                    "rubric": {"scores": "not-a-list", "total": 1, "maximum": 5}}),
        encoding="utf-8",
    )
    (artifacts / "review-audit.json").write_text(
        json.dumps({"mode": "advisory", "reviewer": "not-a-dict", "mcp": [1], "files": {"a": 1}}),
        encoding="utf-8",
    )
    log = (
        json.dumps(MCP_SUCCESS).encode("utf-8") + b"\n"
        + b"\xff\xfe invalid utf-8\n"
        + b"[" * 100000 + b"\n"
        + b"[1, 2]\n"
        + json.dumps(MCP_DENIAL).encode("utf-8") + b"\n"
    )
    (artifacts / "mcp-audit" / "retrieval-audit-log.jsonl").write_bytes(log)

    sha = "feature/branch:abc"
    result, trail_text, summary = _run_audit_trail(tmp_path, {"--sha": sha})
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "ci-audit-trail-feature_branch_abc.json").is_file()
    assert not (tmp_path / "ci-audit-trail-feature").exists()
    trail = json.loads(trail_text)
    assert trail["metadata"]["sha"] == sha
    assert trail["policy_results"]["passed"] == 0 and trail["policy_results"]["report_present"] is True
    assert trail["deterministic_results"]["report_present"] is False
    assert trail["governed_results"]["report_present"] is False
    assert trail["rubric"]["status"] == "scored" and trail["rubric"]["scores"] == []
    reviewer = trail["reviewer"]
    assert reviewer["ran"] is None and reviewer["files_reviewed"] == []
    assert reviewer["mcp_servers_configured"] is None
    mcp = trail["mcp_actions"]
    assert mcp["unparseable_lines"] == 3
    assert [action["event"] for action in mcp["actions"]] == ["retrieval_success", "authorization_denied"]
    assert len(mcp["authorization_denials"]) == 1
    assert "ci-audit-trail-feature_branch_abc.json" in summary


# Module 4.2: the audit-trail job posts the audit summary as a PR comment.

AUDIT_JOB_NEEDS = {
    "change-type-check", "pipeline-integrity", "policy-gate", "eval-gate", "governed-file-gate", "advisory-review",
}
COMMENT_MARKER_LINE = "<!-- marketradar-ci-audit-trail -->"
RUN_LINK = {
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_REPOSITORY": "octo/marketradar",
    "GITHUB_RUN_ID": "123456",
}


def _audit_job():
    return _workflow()["jobs"]["audit-trail"]


def _audit_build_step(job):
    return next(step for step in job["steps"] if "build-audit-trail.py" in str(step.get("run", "")))


def _audit_upload_index(job):
    return next(
        index for index, step in enumerate(job["steps"])
        if str(step.get("uses", "")).startswith("actions/upload-artifact")
    )


def test_audit_trail_job_keeps_needs_and_always():
    job = _audit_job()
    assert set(job["needs"]) == AUDIT_JOB_NEEDS
    assert job["if"] == "always()"


def test_audit_trail_checkout_does_not_persist_credentials():
    checkout = next(
        step for step in _audit_job()["steps"] if str(step.get("uses", "")).startswith("actions/checkout")
    )
    assert checkout["with"]["persist-credentials"] is False


def test_audit_trail_build_step_writes_comment_file():
    assert "--comment-file audit-pr-comment.md" in _audit_build_step(_audit_job())["run"]


def test_audit_trail_upload_always_and_error_if_missing():
    job = _audit_job()
    upload = job["steps"][_audit_upload_index(job)]
    assert upload["if"] == "always()"
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["path"] == "ci-audit-trail-*.json"
    assert upload["with"]["name"] == "audit-trail"


def test_audit_trail_comment_step_posts_after_upload():
    job = _audit_job()
    names = [step.get("name") for step in job["steps"]]
    comment_index = names.index(AUDIT_COMMENT_STEP_NAME)
    assert comment_index > _audit_upload_index(job)
    step = job["steps"][comment_index]
    assert step.get("continue-on-error") is True
    for fragment in ("always()", "pull_request", "hashFiles('audit-pr-comment.md')"):
        assert fragment in step["if"], fragment
    assert "gh pr comment" in step["run"]
    assert "--body-file audit-pr-comment.md" in step["run"]
    for index, other in enumerate(job["steps"]):
        if index != comment_index:
            text = json.dumps(other)
            assert "GH_TOKEN" not in text and "github.token" not in text, other.get("name")


def _comment_rows(text):
    return _summary_check_rows(text)


def test_audit_comment_file_matches_summary_and_links_run(tmp_path):
    _write_artifacts(tmp_path, mcp_records=[MCP_SUCCESS, MCP_DENIAL])
    comment = tmp_path / "audit-pr-comment.md"
    result, _, summary = _run_audit_trail(
        tmp_path, {"--comment-file": str(comment), "--deterministic-outcome": "failure"}, extra_env=RUN_LINK
    )
    assert result.returncode == 0, result.stderr
    body = comment.read_text(encoding="utf-8")
    assert body.splitlines()[0] == COMMENT_MARKER_LINE
    rows = _comment_rows(body)
    assert rows and rows == _summary_check_rows(summary)
    assert rows["eval_deterministic"][1] == "failure"
    assert (
        "Full audit trail: artifact `audit-trail` on "
        "[workflow run](https://github.com/octo/marketradar/actions/runs/123456)"
    ) in body
    assert len(body) <= 60_000


def test_audit_comment_file_omits_run_link_without_run_env(tmp_path):
    comment = tmp_path / "audit-pr-comment.md"
    partial = {"GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "octo/marketradar"}
    result, _, _ = _run_audit_trail(tmp_path, {"--comment-file": str(comment)}, extra_env=partial)
    assert result.returncode == 0, result.stderr
    body = comment.read_text(encoding="utf-8")
    assert body.startswith(COMMENT_MARKER_LINE + "\n")
    assert "workflow run" not in body and "/actions/runs/" not in body


def test_audit_comment_file_redacts_secret(tmp_path):
    secret = "supersecretvalue123"
    review_audit = json.loads(json.dumps(REVIEW_AUDIT))
    review_audit["unexpected_field"] = f"leaked {secret}"
    review_audit["reviewer"]["error"] = f"exit_code_1: {secret}"
    denial = dict(MCP_DENIAL, detail=f"denied for {secret}", role=f"role-{secret}")
    _write_artifacts(tmp_path, review_audit=review_audit, mcp_records=[denial])
    comment = tmp_path / "audit-pr-comment.md"
    result, trail_text, summary = _run_audit_trail(
        tmp_path, {"--comment-file": str(comment)}, extra_env={"FAKE_API_TOKEN": secret}
    )
    assert result.returncode == 0, result.stderr
    body = comment.read_text(encoding="utf-8")
    assert secret not in body and secret not in summary and secret not in trail_text
    assert "[REDACTED]" in body


def test_audit_comment_unwritable_path_still_writes_trail(tmp_path):
    comment = tmp_path / "missing-dir" / "audit-pr-comment.md"
    result, trail_text, summary = _run_audit_trail(tmp_path, {"--comment-file": str(comment)})
    assert result.returncode == 0, result.stderr
    assert trail_text and json.loads(trail_text)["metadata"]["sha"] == AUDIT_SHA
    assert summary
    assert not comment.exists()


def test_audit_comment_is_capped(tmp_path):
    denials = [dict(MCP_DENIAL, role=f"project-manager-{index:05d}") for index in range(2000)]
    _write_artifacts(tmp_path, mcp_records=denials)
    comment = tmp_path / "audit-pr-comment.md"
    result, trail_text, summary = _run_audit_trail(
        tmp_path, {"--comment-file": str(comment)}, extra_env=RUN_LINK
    )
    assert result.returncode == 0, result.stderr
    assert len(summary) > 60_000, "fixture must exceed the cap"
    body = comment.read_text(encoding="utf-8")
    assert len(body) <= 60_000
    assert body.startswith(COMMENT_MARKER_LINE + "\n")
    assert "Comment truncated at 60,000 characters" in body
    assert "/actions/runs/123456" in body
    assert len(json.loads(trail_text)["mcp_actions"]["authorization_denials"]) == 2000


# ---------------------------------------------------------------------------
# Module 4.2 Exercise 1: Pipeline Integrity job (pipeline-integrity and
# scripts/check-pipeline-integrity.py). The script runs as a subprocess with
# --workflow and --output under tmp_path; nothing is written in the repository.
# ---------------------------------------------------------------------------

CHECK_PIPELINE_INTEGRITY = REPO_ROOT / "scripts" / "check-pipeline-integrity.py"
INTEGRITY_SCRIPT_PATH = "scripts/check-pipeline-integrity.py"
INTEGRITY_REPORT_PATH = "ci-artifacts/pipeline-integrity-report.json"


def _integrity_job():
    return _workflow()["jobs"]["pipeline-integrity"]


def _integrity_run_step(job):
    return next(step for step in job["steps"] if INTEGRITY_SCRIPT_PATH in str(step.get("run", "")))


def test_pipeline_integrity_job_exists_and_is_a_gate():
    job = _integrity_job()
    assert job["name"] == "Pipeline Integrity Check"
    assert "continue-on-error" not in job, "pipeline-integrity must not use job-level continue-on-error"
    for step in job["steps"]:
        assert "continue-on-error" not in step, f"pipeline-integrity step {step.get('name')!r} uses continue-on-error"


def test_pipeline_integrity_job_is_unprivileged_and_independent():
    job = _integrity_job()
    assert "permissions" not in job
    assert "needs" not in job
    assert "env" not in job
    assert "secrets." not in json.dumps(job)
    for step in job["steps"]:
        assert "env" not in step, step.get("name")


def test_pipeline_integrity_checkout_does_not_persist_credentials():
    checkout = next(
        step for step in _integrity_job()["steps"] if str(step.get("uses", "")).startswith("actions/checkout")
    )
    assert checkout["with"]["persist-credentials"] is False


def test_pipeline_integrity_job_runs_script_with_report_output():
    command = _integrity_run_step(_integrity_job())["run"]
    assert f"python {INTEGRITY_SCRIPT_PATH}" in command
    assert f"--output {INTEGRITY_REPORT_PATH}" in command


def test_pipeline_integrity_report_upload_always_and_ignore_missing():
    upload = next(
        step for step in _integrity_job()["steps"]
        if str(step.get("uses", "")).startswith("actions/upload-artifact")
    )
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == INTEGRITY_REPORT_PATH
    assert upload["with"]["if-no-files-found"] == "ignore"


def test_audit_trail_records_pipeline_integrity_job():
    job = _audit_job()
    assert "pipeline-integrity" in job["needs"]
    assert '--integrity-result "${{ needs.pipeline-integrity.result }}"' in _audit_build_step(job)["run"]


def _run_integrity(tmp_path, workflow_path):
    report_path = tmp_path / "out" / "pipeline-integrity-report.json"
    result = subprocess.run(
        [sys.executable, str(CHECK_PIPELINE_INTEGRITY), "--workflow", str(workflow_path), "--output", str(report_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
    return result, report


def _write_workflow(tmp_path, workflow):
    path = tmp_path / "ci.yml"
    path.write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")
    return path


def test_pipeline_integrity_script_passes_on_repository_workflow(tmp_path):
    result, report = _run_integrity(tmp_path, WORKFLOW_PATH)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Pipeline integrity check passed" in result.stdout
    assert report is not None
    assert report["passed"] is True
    assert report["errors"] == []
    assert report["workflow"] == str(WORKFLOW_PATH)
    assert report["checks"] and all(check["passed"] is True for check in report["checks"]), report["checks"]
    assert {check["name"] for check in report["checks"]} == {
        "gating_job_present:eval-gate",
        "gating_job_present:pipeline-integrity",
        "gating_job_present:policy-gate",
        "gating_job_not_continue_on_error:eval-gate",
        "gating_job_not_continue_on_error:pipeline-integrity",
        "gating_job_not_continue_on_error:policy-gate",
        "audit_trail_present",
        "audit_trail_if_always",
        "change_type_check_present",
    }


def _inject_integrity_fault(jobs, fault):
    if fault == "policy-gate-continue-on-error":
        jobs["policy-gate"]["continue-on-error"] = True
    elif fault == "eval-gate-continue-on-error":
        jobs["eval-gate"]["continue-on-error"] = True
    elif fault == "audit-trail-if-removed":
        del jobs["audit-trail"]["if"]
    elif fault == "change-type-check-removed":
        del jobs["change-type-check"]
    elif fault == "pipeline-integrity-removed":
        del jobs["pipeline-integrity"]
    else:
        raise AssertionError(f"unknown fault {fault}")


INTEGRITY_FAULTS = {
    "policy-gate-continue-on-error": (
        "gating_job_not_continue_on_error:policy-gate",
        "gating job 'policy-gate' has continue-on-error: true, which disables the gate",
    ),
    "eval-gate-continue-on-error": (
        "gating_job_not_continue_on_error:eval-gate",
        "gating job 'eval-gate' has continue-on-error: true, which disables the gate",
    ),
    "audit-trail-if-removed": ("audit_trail_if_always", "audit-trail job must keep 'if: always()'"),
    "change-type-check-removed": ("change_type_check_present", "change-type-check classifier is missing"),
    "pipeline-integrity-removed": (
        "gating_job_present:pipeline-integrity",
        "required gating job 'pipeline-integrity' is missing",
    ),
}


@pytest.mark.parametrize("fault", sorted(INTEGRITY_FAULTS))
def test_pipeline_integrity_script_detects_injected_fault(fault, tmp_path):
    workflow = _workflow()
    _inject_integrity_fault(workflow["jobs"], fault)
    result, report = _run_integrity(tmp_path, _write_workflow(tmp_path, workflow))
    check_name, expected_error = INTEGRITY_FAULTS[fault]
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Pipeline integrity check FAILED:" in result.stdout
    assert expected_error in result.stdout
    assert "Traceback" not in result.stderr
    assert report is not None
    assert report["passed"] is False
    assert report["errors"] == [expected_error]
    failed = {check["name"] for check in report["checks"] if not check["passed"]}
    assert check_name in failed


# Fail-closed allowlists: job-level continue-on-error on a gating job must be
# absent or boolean false, and the audit-trail `if` must be exactly always().
# Mutated workflows are written with yaml.safe_dump, which quotes strings such
# as "true", "false" and "yes" so they reload as strings, not booleans.

CONTINUE_ON_ERROR_BYPASSES = [
    "true",
    "True",
    "false",
    "${{ true }}",
    "${{ github.event_name == 'push' }}",
    1,
    0,
    "yes",
    None,
    ["x"],
    {"a": 1},
]
GATING_JOB_NAMES = ["policy-gate", "eval-gate", "pipeline-integrity"]
AUDIT_IF_BYPASSES = [
    "!always()",
    "${{ !always() }}",
    "always() && false",
    "always() && github.event_name == 'push'",
    "${{ always() && failure() }}",
    "success()",
    "always() || false",
    "${{ always() }} && ${{ false }}",
    True,
    ["always()"],
    {"always()": 1},
]
AUDIT_IF_ACCEPTED = ["always()", " always() ", "${{ always() }}", "${{always()}}"]
AUDIT_IF_ERROR = "audit-trail job must keep 'if: always()'"


def _run_integrity_on_mutated_workflow(tmp_path, mutate):
    workflow = _workflow()
    mutate(workflow["jobs"])
    path = _write_workflow(tmp_path, workflow)
    reloaded = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]
    result, report = _run_integrity(tmp_path, path)
    return result, report, reloaded


@pytest.mark.parametrize("value", CONTINUE_ON_ERROR_BYPASSES, ids=repr)
@pytest.mark.parametrize("job_name", GATING_JOB_NAMES)
def test_pipeline_integrity_rejects_non_false_continue_on_error(job_name, value, tmp_path):
    def mutate(jobs):
        jobs[job_name]["continue-on-error"] = value

    result, report, reloaded = _run_integrity_on_mutated_workflow(tmp_path, mutate)
    # The written workflow must carry the value with its original type.
    assert reloaded[job_name]["continue-on-error"] == value
    assert type(reloaded[job_name]["continue-on-error"]) is type(value)
    expected_error = (
        f"gating job '{job_name}' has continue-on-error: {json.dumps(value)}; only false or absent is allowed"
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Pipeline integrity check FAILED:" in result.stdout
    assert "Traceback" not in result.stderr
    assert report is not None
    assert report["passed"] is False
    assert report["errors"] == [expected_error]
    assert any(job_name in error for error in report["errors"])
    failed = {check["name"] for check in report["checks"] if not check["passed"]}
    assert f"gating_job_not_continue_on_error:{job_name}" in failed


# Boolean True has its own exact message; it is kept out of
# CONTINUE_ON_ERROR_BYPASSES so the json.dumps-based expectation above holds.
@pytest.mark.parametrize("job_name", GATING_JOB_NAMES)
def test_pipeline_integrity_rejects_boolean_true_continue_on_error(job_name, tmp_path):
    def mutate(jobs):
        jobs[job_name]["continue-on-error"] = True

    result, report, reloaded = _run_integrity_on_mutated_workflow(tmp_path, mutate)
    assert reloaded[job_name]["continue-on-error"] is True
    expected_error = f"gating job '{job_name}' has continue-on-error: true, which disables the gate"
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Pipeline integrity check FAILED:" in result.stdout
    assert expected_error in result.stdout
    assert "Traceback" not in result.stderr
    assert report is not None
    assert report["passed"] is False
    assert report["errors"] == [expected_error]
    failed = {check["name"] for check in report["checks"] if not check["passed"]}
    assert f"gating_job_not_continue_on_error:{job_name}" in failed


def test_pipeline_integrity_accepts_continue_on_error_false(tmp_path):
    def mutate(jobs):
        jobs["policy-gate"]["continue-on-error"] = False

    result, report, reloaded = _run_integrity_on_mutated_workflow(tmp_path, mutate)
    assert reloaded["policy-gate"]["continue-on-error"] is False
    assert result.returncode == 0, result.stdout + result.stderr
    assert report is not None
    assert report["passed"] is True
    assert report["errors"] == []


@pytest.mark.parametrize("value", AUDIT_IF_BYPASSES, ids=repr)
def test_pipeline_integrity_rejects_audit_trail_if_bypass(value, tmp_path):
    def mutate(jobs):
        jobs["audit-trail"]["if"] = value

    result, report, reloaded = _run_integrity_on_mutated_workflow(tmp_path, mutate)
    assert reloaded["audit-trail"]["if"] == value
    assert type(reloaded["audit-trail"]["if"]) is type(value)
    assert result.returncode == 1, result.stdout + result.stderr
    assert AUDIT_IF_ERROR in result.stdout
    assert "Traceback" not in result.stderr
    assert report is not None
    assert report["passed"] is False
    assert report["errors"] == [AUDIT_IF_ERROR]
    failed = {check["name"] for check in report["checks"] if not check["passed"]}
    assert "audit_trail_if_always" in failed


@pytest.mark.parametrize("value", AUDIT_IF_ACCEPTED, ids=repr)
def test_pipeline_integrity_accepts_audit_trail_if_always_forms(value, tmp_path):
    def mutate(jobs):
        jobs["audit-trail"]["if"] = value

    result, report, reloaded = _run_integrity_on_mutated_workflow(tmp_path, mutate)
    assert reloaded["audit-trail"]["if"] == value
    assert result.returncode == 0, result.stdout + result.stderr
    assert report is not None
    assert report["passed"] is True
    assert report["errors"] == []


@pytest.mark.parametrize(
    "case, content",
    [
        ("missing-file", None),
        ("invalid-yaml", "jobs: [unclosed\n  policy-gate: {\n"),
        ("top-level-not-mapping", "- just\n- a list\n"),
        ("jobs-not-mapping", "name: CI\njobs:\n  - policy-gate\n"),
    ],
)
def test_pipeline_integrity_script_fails_cleanly_on_unreadable_workflow(case, content, tmp_path):
    workflow_path = tmp_path / "ci.yml"
    if content is not None:
        workflow_path.write_text(content, encoding="utf-8")
    result, report = _run_integrity(tmp_path, workflow_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Pipeline integrity check FAILED:" in result.stdout
    assert "Traceback" not in result.stderr
    assert report is not None
    assert report["passed"] is False
    assert report["errors"]
    assert report["checks"] and all(check["passed"] is False for check in report["checks"])


def _run_integrity_twice(tmp_path, workflow_path):
    runs = []
    for index in (1, 2):
        report_path = tmp_path / f"out-{index}" / "pipeline-integrity-report.json"
        result = subprocess.run(
            [sys.executable, str(CHECK_PIPELINE_INTEGRITY), "--workflow", str(workflow_path), "--output", str(report_path)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert "Traceback" not in result.stderr
        assert report_path.exists(), result.stdout + result.stderr
        runs.append((result, report_path.read_bytes()))
    return runs


def test_pipeline_integrity_output_is_deterministic_on_failure(tmp_path):
    workflow = _workflow()
    workflow["jobs"]["policy-gate"]["continue-on-error"] = "true"
    path = _write_workflow(tmp_path, workflow)
    (first, first_report), (second, second_report) = _run_integrity_twice(tmp_path, path)
    assert first.returncode == second.returncode == 1, first.stdout + first.stderr
    assert first.stdout == second.stdout
    assert first_report == second_report


def test_pipeline_integrity_output_is_deterministic_on_repository_workflow(tmp_path):
    (first, first_report), (second, second_report) = _run_integrity_twice(tmp_path, WORKFLOW_PATH)
    assert first.returncode == second.returncode == 0, first.stdout + first.stderr
    assert first.stdout == second.stdout
    assert first_report == second_report


# The audit trail records the pipeline-integrity job result and report.

INTEGRITY_ERROR = "gating job 'policy-gate' has continue-on-error: true, which disables the gate"


def _write_integrity_report(tmp_path, report):
    artifacts = tmp_path / "ci-artifacts"
    artifacts.mkdir(exist_ok=True)
    (artifacts / "pipeline-integrity-report.json").write_text(json.dumps(report), encoding="utf-8")


@pytest.mark.parametrize("integrity", ["success", "failure"])
def test_audit_trail_records_pipeline_integrity_result(integrity, tmp_path):
    result, trail_text, summary = _run_audit_trail(tmp_path, {"--integrity-result": integrity})
    assert result.returncode == 0, result.stderr
    trail = json.loads(trail_text)
    assert trail["checks"]["pipeline_integrity"] == {"status": integrity, "raw_result": integrity, "gating": True}
    assert trail["results"]["pipeline_integrity"] == integrity
    assert _summary_check_rows(summary)["pipeline_integrity"] == ("gating", integrity, integrity, None)


def test_audit_trail_reflects_failed_pipeline_integrity_report(tmp_path):
    _write_artifacts(tmp_path)
    _write_integrity_report(tmp_path, {
        "passed": False,
        "workflow": ".github/workflows/ci.yml",
        "checks": [{"name": "gating_job_not_continue_on_error:policy-gate", "passed": False, "detail": "x"}],
        "errors": [INTEGRITY_ERROR, 7, {"not": "a string"}],
    })
    result, trail_text, summary = _run_audit_trail(tmp_path, {"--integrity-result": "failure"})
    assert result.returncode == 0, result.stderr
    trail = json.loads(trail_text)
    assert trail["pipeline_integrity"] == {"report_present": True, "passed": False, "errors": [INTEGRITY_ERROR]}
    assert trail["reports_present"]["pipeline_integrity_report"] is True
    assert "**Pipeline integrity:** failed (1 problem)" in summary
    assert INTEGRITY_ERROR in summary


def test_audit_trail_pluralizes_multiple_pipeline_integrity_problems(tmp_path):
    second_error = "required job 'audit-trail' is missing"
    _write_integrity_report(tmp_path, {
        "passed": False, "workflow": "ci.yml", "checks": [], "errors": [INTEGRITY_ERROR, second_error],
    })
    result, trail_text, summary = _run_audit_trail(tmp_path, {"--integrity-result": "failure"})
    assert result.returncode == 0, result.stderr
    assert "**Pipeline integrity:** failed (2 problems)" in summary
    assert INTEGRITY_ERROR in summary
    assert second_error in summary


def test_audit_trail_reflects_passed_pipeline_integrity_report(tmp_path):
    _write_integrity_report(tmp_path, {"passed": True, "workflow": "ci.yml", "checks": [], "errors": []})
    result, trail_text, summary = _run_audit_trail(tmp_path)
    assert result.returncode == 0, result.stderr
    trail = json.loads(trail_text)
    assert trail["pipeline_integrity"] == {"report_present": True, "passed": True, "errors": []}
    assert trail["reports_present"]["pipeline_integrity_report"] is True
    assert "**Pipeline integrity:** passed" in summary


@pytest.mark.parametrize(
    "report",
    [None, {"passed": "yes", "errors": [INTEGRITY_ERROR]}, [1, 2]],
    ids=["missing", "non-bool-passed", "list"],
)
def test_audit_trail_without_usable_pipeline_integrity_report(report, tmp_path):
    if report is not None:
        _write_integrity_report(tmp_path, report)
    result, trail_text, summary = _run_audit_trail(tmp_path)
    assert result.returncode == 0, result.stderr
    trail = json.loads(trail_text)
    assert trail["pipeline_integrity"] == {"report_present": False, "passed": None, "errors": []}
    assert trail["reports_present"]["pipeline_integrity_report"] is False
    assert "**Pipeline integrity:** no readable report" in summary
    assert INTEGRITY_ERROR not in summary


def test_audit_trail_gives_pipeline_integrity_no_skip_reason_after_classifier_failure(tmp_path):
    overrides = {
        "--classifier-result": "failure", "--integrity-result": "skipped", "--policy-result": "skipped",
        "--governed-result": "skipped", "--eval-result": "skipped", "--deterministic-outcome": "",
        "--rubric-outcome": "", "--review-result": "skipped", "--requires-governed-check": "",
        "--agent-affecting": "",
    }
    result, trail_text, _ = _run_audit_trail(tmp_path, overrides)
    assert result.returncode == 0, result.stderr
    checks = json.loads(trail_text)["checks"]
    assert checks["pipeline_integrity"] == {"status": "not_triggered", "raw_result": "skipped", "gating": True}
    assert checks["policy_gate"]["reason"] == "skipped: change classification did not succeed"
