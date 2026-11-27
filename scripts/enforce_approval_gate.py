#!/usr/bin/env python3
"""PreToolUse hook: only a human may approve an orchestration plan.

Policy (CLAUDE.md "Human Checkpoints"; docs/governance-policy.md): human
approval is required after the Planner phase and before implementation.
`scripts/orchestrate.py approve` records that approval. This hook denies any
Claude Code tool call that would record it instead:

- a Bash command that runs `orchestrate.py ... approve` or names `approval.json`;
- a Write, Edit or NotebookEdit whose target file is named `approval.json`.

A human running the command in their own shell (or with the `!` prefix in a
Claude Code prompt, which is not a tool call) is not affected. Input the hook
cannot read is denied (fail closed). Denials are audited outside the
repository, using the same audit-path rules as scripts/enforce_skill_scope.py.
"""

import datetime
import json
import re
import sys
from pathlib import Path

# The hook runs inside the repository; never write bytecode caches there.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from enforce_skill_scope import write_audit  # noqa: E402

POLICY_REFERENCE = "CLAUDE.md (Human Checkpoints); docs/governance-policy.md"
APPROVAL_FILENAME = "approval.json"
APPROVE_COMMAND = re.compile(r"orchestrate\.py\b.*\bapprove\b", re.DOTALL)
FILE_TOOLS = {"Write", "Edit", "NotebookEdit"}


def decide(payload):
    """Return (decision, reason); decision is "deny" or None (not governed)."""
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    if tool == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str):
            return "deny", "Bash command could not be read; failing closed"
        if APPROVE_COMMAND.search(command) or APPROVAL_FILENAME in command:
            return "deny", "plan approval must be recorded by a human, not by an agent"
        return None, "command does not record approval"
    if tool in FILE_TOOLS:
        target = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        if Path(str(target)).name == APPROVAL_FILENAME:
            return "deny", "agents may not write an orchestration approval record"
        return None, "file is not an approval record"
    return None, "tool not governed by this hook"


def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        decision, reason = decide(payload)
    else:
        payload = {}
        decision, reason = "deny", "unreadable hook input; failing closed"
    if decision is None:
        return 0
    write_audit(
        {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "event": "approval_gate_denied",
            "operation": payload.get("tool_name") or "unknown",
            "role": payload.get("agent_type") or "main session",
            "outcome": "denied",
            "detail": reason,
            "policy_reference": POLICY_REFERENCE,
        },
        env_var="APPROVAL_AUDIT_LOG",
        filename="approval-audit-log.jsonl",
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"approval_gate_denied: {reason}. See {POLICY_REFERENCE}.",
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
