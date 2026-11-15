#!/usr/bin/env python3
"""
Build the combined CI audit trail from CI metadata and downloaded artifacts.

Writes ci-audit-trail-<sha>.json in the current directory (the sha is
sanitized for the filename) and appends a Markdown summary to --summary PATH
(or $GITHUB_STEP_SUMMARY when set). With --comment-file PATH it also writes
the same summary as a PR comment body (hidden marker line first, a link to
the workflow run when GITHUB_SERVER_URL, GITHUB_REPOSITORY and GITHUB_RUN_ID
are set, capped at 60,000 characters).

The trail contains:
- metadata and change classification;
- normalised check statuses (`checks`), with the raw job results kept in
  `results` for compatibility. A check is `not_triggered` when its raw result
  is empty or `skipped`, or `success` while its trigger flag is false (the job
  only printed a skip notice). `failure` and `cancelled` are always reported.
  A check skipped because an upstream job did not succeed gets a `reason`;
- pass/fail counts from the pytest JSON reports;
- rubric scores (without justification text) or the reason none were given;
- a whitelisted summary of the advisory reviewer's review-audit.json (never
  the review text);
- every MCP action recorded in <artifacts>/mcp-audit/*.jsonl, with
  authorization denials listed separately. Actions are never synthesized.

Secret safety: the primary protection is field whitelisting here plus the
upstream redaction in scripts/run-reviewer.py; the CI audit-trail step itself
receives no secrets. As defence in depth, the values of secret-named
environment variables (KEY, TOKEN, SECRET, PASSWORD, CREDENTIAL) present in
this step's own environment are replaced with "[REDACTED]" wherever they occur
in the trail, the summary and the comment body.

Inputs of the wrong shape are treated as absent, so a trail is always written.
Always exits 0 unless the arguments are invalid.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

NOT_TRIGGERED = "not_triggered"
ALWAYS_REPORTED = ("failure", "cancelled")
REDACTED = "[REDACTED]"
SECRET_NAME_PATTERN = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.IGNORECASE)
MIN_SECRET_LENGTH = 8
NO_MCP_LOG_NOTE = "No MCP audit log: the reviewer made no MCP calls or did not run."
ARTIFACT_NAME = "audit-trail"
DENIAL = "authorization_denied"
COMMENT_MARKER = "<!-- marketradar-ci-audit-trail -->"
COMMENT_MAX_CHARS = 60_000
COMMENT_TRUNCATION_NOTE = (
    "\n\n_Comment truncated at 60,000 characters; see the full audit trail artifact._\n"
)

REASON_CLASSIFIER = "skipped: change classification did not succeed"
REASON_POLICY = "skipped: policy_gate did not succeed"
REASON_DETERMINISTIC = "skipped: deterministic evaluation did not succeed"

GATING = {
    "change_classification": True,
    "policy_gate": True,
    "governed_file_gate": True,
    "eval_gate": True,
    "eval_deterministic": True,
    "eval_rubric": False,
    "advisory_review": False,
}
POLICY_DEPENDENTS = {"governed_file_gate", "eval_gate", "eval_deterministic", "eval_rubric", "advisory_review"}

COUNT_KEYS = ("passed", "failed", "error", "skipped", "total")


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (Exception, RecursionError):
        return default


def load_text(path: Path, default=""):
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return default


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    return value if isinstance(value, list) else []


def safe_filename_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value or "") or "unknown"


def trail_filename(sha: str) -> str:
    return f"ci-audit-trail-{safe_filename_part(sha)}.json"


def check_status(raw: str, triggered: bool = True) -> dict:
    value = (raw or "").strip()
    if value in ALWAYS_REPORTED:
        status = value
    elif value in ("", "skipped"):
        status = NOT_TRIGGERED
    elif value == "success" and not triggered:
        status = NOT_TRIGGERED
    else:
        status = value
    return {"status": status, "raw_result": raw}


def add_skip_reasons(checks: dict) -> None:
    """Explain checks that were skipped because an upstream job did not succeed."""
    def raw(name):
        return (checks[name]["raw_result"] or "").strip()

    for name, check in checks.items():
        if name == "change_classification" or raw(name) not in ("", "skipped"):
            continue
        if raw("change_classification") in ALWAYS_REPORTED:
            check["reason"] = REASON_CLASSIFIER
        elif name in POLICY_DEPENDENTS and raw("policy_gate") in ALWAYS_REPORTED:
            check["reason"] = REASON_POLICY
        elif name == "eval_rubric" and raw("eval_deterministic") in ALWAYS_REPORTED:
            check["reason"] = REASON_DETERMINISTIC


def pytest_results(report) -> dict:
    if not isinstance(report, dict) or not report:
        result = {"report_present": False, "exitcode": None}
        result.update({key: None for key in COUNT_KEYS})
        return result
    summary = _dict(report.get("summary"))
    result = {"report_present": True, "exitcode": report.get("exitcode")}
    for key in COUNT_KEYS:
        value = summary.get(key, 0)
        result[key] = value if isinstance(value, int) and not isinstance(value, bool) else 0
    return result


def rubric_results(report, rubric_check_status: str) -> dict:
    if not isinstance(report, dict) or not report:
        if rubric_check_status == "failure":
            return {"status": "failed_no_report"}
        return {"status": NOT_TRIGGERED}
    rubric = report.get("rubric")
    if report.get("rubric_ran") is True and isinstance(rubric, dict):
        scores = []
        for score in _list(rubric.get("scores")):
            if isinstance(score, dict):
                scores.append({
                    "dimension": score.get("dimension"),
                    "score": score.get("score"),
                    "pass_threshold": score.get("pass_threshold"),
                    "passed": score.get("passed"),
                })
        return {
            "status": "scored",
            "scores": scores,
            "total": rubric.get("total"),
            "maximum": rubric.get("maximum"),
            "overall_pass_threshold": rubric.get("overall_pass_threshold"),
            "passed": rubric.get("passed"),
        }
    return {
        "status": "not_scored",
        "reason": report.get("reason"),
        "deterministic_precheck_passed": _dict(report.get("deterministic")).get("passed"),
    }


def reviewer_summary(review_audit) -> dict:
    if not isinstance(review_audit, dict) or not review_audit:
        return {"review_audit_present": False}
    reviewer = _dict(review_audit.get("reviewer"))
    mcp = _dict(review_audit.get("mcp"))
    return {
        "review_audit_present": True,
        "mode": review_audit.get("mode"),
        "mechanism": reviewer.get("mechanism"),
        "ran": reviewer.get("ran"),
        "error": reviewer.get("error"),
        "is_error": reviewer.get("is_error"),
        "num_turns": reviewer.get("num_turns"),
        "files_reviewed": [
            record.get("path") for record in _list(review_audit.get("files"))
            if isinstance(record, dict) and record.get("included") is True
        ],
        "mcp_servers_configured": mcp.get("servers_configured"),
        "mcp_agent_role": mcp.get("agent_role"),
        "mcp_audit_log_present": mcp.get("audit_log_present"),
        "mcp_audit_log_records": mcp.get("audit_log_records"),
    }


def _parse_jsonl_line(raw_line: bytes):
    """Return the record dict, or None if the line cannot be used."""
    try:
        record = json.loads(raw_line.decode("utf-8"))
    except (ValueError, RecursionError):  # UnicodeDecodeError is a ValueError
        return None
    return record if isinstance(record, dict) else None


def mcp_actions(artifact_dir: Path) -> dict:
    try:
        logs = sorted(path for path in (artifact_dir / "mcp-audit").glob("*.jsonl") if path.is_file())
    except OSError:
        logs = []
    actions: list[dict] = []
    denials: list[dict] = []
    unparseable = 0
    for log in logs:
        server = log.name.split("-")[0]
        try:
            data = log.read_bytes()
        except OSError:
            unparseable += 1
            continue
        for raw_line in data.splitlines():
            if not raw_line.strip():
                continue
            record = _parse_jsonl_line(raw_line)
            if record is None:
                unparseable += 1
                continue
            action = {
                "server": server,
                "timestamp": record.get("timestamp"),
                "role": record.get("role"),
                "operation": record.get("operation"),
                "event": record.get("event"),
                "outcome": record.get("outcome"),
            }
            actions.append(action)
            if record.get("event") == DENIAL or record.get("outcome") == DENIAL:
                denials.append({**action, "detail": record.get("detail")})
    result = {
        "mcp_audit_log_present": bool(logs),
        "actions": actions,
        "authorization_denials": denials,
        "unparseable_lines": unparseable,
    }
    if not logs:
        result["note"] = NO_MCP_LOG_NOTE
    return result


def secret_values() -> list[str]:
    """Values of secret-named variables in this step's own environment."""
    values = {
        value for name, value in os.environ.items()
        if SECRET_NAME_PATTERN.search(name) and value and len(value) >= MIN_SECRET_LENGTH
    }
    return sorted(values, key=len, reverse=True)


def redact(data, secrets: list[str]):
    """Return (redacted copy, number of replacements). Defence in depth only."""
    count = 0

    def scrub_text(text: str) -> str:
        nonlocal count
        for secret in secrets:
            hits = text.count(secret)
            if hits:
                count += hits
                text = text.replace(secret, REDACTED)
        return text

    def walk(value):
        if isinstance(value, str):
            return scrub_text(value)
        if isinstance(value, dict):
            return {scrub_text(k) if isinstance(k, str) else k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(item) for item in value]
        return value

    return walk(data), count


def _display(status) -> str:
    if status == NOT_TRIGGERED:
        return "not triggered"
    return str(status)


def _cell(value) -> str:
    text = str(value).replace("|", "\\|").replace("\n", " ").strip()
    return text or "(empty)"


def build_summary(trail: dict) -> str:
    meta = trail["metadata"]
    sha = str(meta.get("sha") or "")[:7]
    pr = meta.get("pull_request")
    pr_text = f" · PR #{pr}" if pr not in (None, "", "0", 0) else ""
    lines = [f"## CI audit trail: `{_cell(sha)}` ({_cell(meta.get('event'))}){pr_text}", ""]

    checks = trail["checks"]
    with_reason = any(check.get("reason") for check in checks.values())
    if with_reason:
        lines += ["| Check | Kind | Status | Raw result | Reason |", "|---|---|---|---|---|"]
    else:
        lines += ["| Check | Kind | Status | Raw result |", "|---|---|---|---|"]
    for name, check in checks.items():
        kind = "gating" if check["gating"] else "advisory"
        row = f"| {name} | {kind} | {_display(check['status'])} | {_cell(check['raw_result'])} |"
        if with_reason:
            row += f" {_cell(check.get('reason') or '-')} |"
        lines.append(row)
    lines.append("")

    for label, key in (("Policy tests", "policy_results"), ("Deterministic evals", "deterministic_results"),
                       ("Governed-file tests", "governed_results")):
        result = trail[key]
        if result["report_present"]:
            lines.append(
                f"- **{label}:** {result['passed']} passed, {result['failed']} failed, "
                f"{result['error']} errors, {result['skipped']} skipped (exit code {result['exitcode']})"
            )
        else:
            lines.append(f"- **{label}:** no readable report (not triggered or not produced)")
    lines.append("")

    rubric = trail["rubric"]
    if rubric["status"] == "scored":
        lines += [
            f"**Rubric (advisory):** scored, total {rubric.get('total')} / {rubric.get('maximum')} "
            f"(threshold {rubric.get('overall_pass_threshold')}), passed: {rubric.get('passed')}",
            "",
            "| Dimension | Score | Threshold | Passed |",
            "|---|---|---|---|",
        ]
        for score in rubric["scores"]:
            lines.append(
                f"| {_cell(score.get('dimension'))} | {_cell(score.get('score'))} | "
                f"{_cell(score.get('pass_threshold'))} | {_cell(score.get('passed'))} |"
            )
    elif rubric["status"] == "not_scored":
        lines.append(
            f"**Rubric (advisory):** not scored. Reason: {_cell(rubric.get('reason'))} "
            f"(deterministic pre-check passed: {rubric.get('deterministic_precheck_passed')})"
        )
    elif rubric["status"] == "failed_no_report":
        lines.append("**Rubric (advisory):** the rubric step failed and wrote no readable report")
    else:
        lines.append("**Rubric (advisory):** not triggered")
    lines.append("")

    reviewer = trail["reviewer"]
    if reviewer.get("review_audit_present"):
        lines.append(
            f"**Advisory reviewer:** ran: {reviewer.get('ran')}, error: {_cell(reviewer.get('error'))}, "
            f"files reviewed: {len(_list(reviewer.get('files_reviewed')))}"
        )
    else:
        lines.append("**Advisory reviewer:** no readable review audit (not triggered or not produced)")
    lines.append("")

    mcp = trail["mcp_actions"]
    if mcp["mcp_audit_log_present"]:
        lines.append(f"**MCP actions:** {len(mcp['actions'])} recorded")
        lines.append("")
        denials = mcp["authorization_denials"]
        if denials:
            lines.append("Authorization denials:")
            for denial in denials:
                lines.append(
                    f"- role `{_cell(denial.get('role'))}`, operation `{_cell(denial.get('operation'))}`, "
                    f"server `{_cell(denial.get('server'))}`"
                )
        else:
            lines.append("Authorization denials: none")
    else:
        lines.append(f"**MCP actions:** {mcp['note']}")
    lines.append("")

    lines.append(f"Full trail: `{trail_filename(str(meta.get('sha') or ''))}` in the `{ARTIFACT_NAME}` artifact.")
    return "\n".join(lines) + "\n"


def build_trail(args) -> dict:
    artifact_dir = Path(args.artifacts)

    policy_report = load_json(artifact_dir / "policy-report.json", {})
    governed_report = load_json(artifact_dir / "governed-file-report.json", {})
    review_audit = load_json(artifact_dir / "review-audit.json", {})
    review_output = load_text(artifact_dir / "review-output.md", "")
    deterministic_report = load_json(artifact_dir / "deterministic-report.json", {})
    rubric_report = load_json(artifact_dir / "rubric-report.json", {})

    requires_governed = args.requires_governed_check == "true"
    agent_affecting = args.agent_affecting == "true"
    is_pr = args.event == "pull_request"

    checks = {
        "change_classification": check_status(args.classifier_result),
        "policy_gate": check_status(args.policy_result),
        "governed_file_gate": check_status(args.governed_result, requires_governed),
        "eval_gate": check_status(args.eval_result, agent_affecting),
        "eval_deterministic": check_status(args.deterministic_outcome, agent_affecting),
        "eval_rubric": check_status(args.rubric_outcome, agent_affecting),
        "advisory_review": check_status(args.review_result, is_pr),
    }
    for name, check in checks.items():
        check["gating"] = GATING[name]
    add_skip_reasons(checks)

    return {
        "metadata": {
            "sha": args.sha,
            "event": args.event,
            "pull_request": args.pr,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "change_classification": {
            "changed_files": args.changed_files.split(),
            "touches_policy": args.touches_policy == "true",
            "requires_governed_check": requires_governed,
            "agent_affecting": agent_affecting,
        },
        "checks": checks,
        "results": {
            "policy_gate": args.policy_result,
            "governed_file_gate": args.governed_result,
            "advisory_review": args.review_result,
            "eval_gate": args.eval_result,
            "eval_deterministic_step": args.deterministic_outcome,
            "eval_rubric_step_advisory": args.rubric_outcome,
        },
        "reports_present": {
            "policy_report": bool(policy_report),
            "governed_file_report": bool(governed_report),
            "review_audit": bool(review_audit),
            "review_output": bool(review_output),
            "deterministic_report": bool(deterministic_report),
            "rubric_report": bool(rubric_report),
        },
        "policy_results": pytest_results(policy_report),
        "deterministic_results": pytest_results(deterministic_report),
        "governed_results": pytest_results(governed_report),
        "rubric": rubric_results(rubric_report, checks["eval_rubric"]["status"]),
        "reviewer": reviewer_summary(review_audit),
        "mcp_actions": mcp_actions(artifact_dir),
    }


def run_link_line() -> str | None:
    server = os.environ.get("GITHUB_SERVER_URL", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
    if not (server and repo and run_id):
        return None
    return (
        f"Full audit trail: artifact `{ARTIFACT_NAME}` on "
        f"[workflow run]({server.rstrip('/')}/{repo}/actions/runs/{run_id})"
    )


def build_comment(body: str) -> str:
    head = COMMENT_MARKER + "\n"
    link = run_link_line()
    tail = f"\n{link}\n" if link else ""
    if len(head) + len(body) + len(tail) <= COMMENT_MAX_CHARS:
        return head + body + tail
    available = COMMENT_MAX_CHARS - len(head) - len(COMMENT_TRUNCATION_NOTE) - len(tail)
    return head + body[:max(available, 0)] + COMMENT_TRUNCATION_NOTE + tail


def write_comment(path: str, body: str, secrets: list[str]) -> None:
    """Write the PR comment body. Never raises: the comment is best effort."""
    try:
        comment, _ = redact(build_comment(body), secrets)
        if len(comment) > COMMENT_MAX_CHARS:  # redaction can lengthen short secrets
            comment = comment[:COMMENT_MAX_CHARS]
        Path(path).write_text(comment, encoding="utf-8")
    except Exception as exc:
        print(f"PR comment body could not be written: {type(exc).__name__}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sha", required=True)
    parser.add_argument("--event", required=True)
    parser.add_argument("--pr", required=True)
    parser.add_argument("--changed-files", default="")
    parser.add_argument("--touches-policy", default="false")
    parser.add_argument("--requires-governed-check", default="false")
    parser.add_argument("--classifier-result", default="unknown")
    parser.add_argument("--policy-result", default="unknown")
    parser.add_argument("--governed-result", default="unknown")
    parser.add_argument("--review-result", default="unknown")
    parser.add_argument("--agent-affecting", default="false")
    parser.add_argument("--eval-result", default="unknown")
    parser.add_argument("--deterministic-outcome", default="unknown")
    parser.add_argument("--rubric-outcome", default="unknown")
    parser.add_argument("--artifacts", default="ci-artifacts")
    parser.add_argument("--summary", default=None)
    parser.add_argument("--comment-file", default=None)
    args = parser.parse_args()

    secrets = secret_values()
    out = Path(trail_filename(args.sha))
    trail = None
    try:
        trail, count = redact(build_trail(args), secrets)
        trail["redactions"] = count
        out.write_text(json.dumps(trail, indent=2), encoding="utf-8")
        print(f"Wrote {out}")
    except (Exception, RecursionError) as exc:
        # Exception text is not printed or recorded: it could echo artifact content.
        error_type = type(exc).__name__
        print(f"Audit trail could not be built: {error_type}", file=sys.stderr)
        fallback, count = redact({
            "metadata": {
                "sha": args.sha,
                "event": args.event,
                "pull_request": args.pr,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            "error": f"audit trail could not be built: {error_type}",
        }, secrets)
        fallback["redactions"] = count
        trail = None
        try:
            out.write_text(json.dumps(fallback, indent=2), encoding="utf-8")
            print(f"Wrote {out}")
        except Exception:
            pass
        if args.comment_file:
            write_comment(
                args.comment_file,
                "## CI audit trail\n\n"
                f"The audit trail could not be fully built ({error_type}). "
                "See the workflow run logs.\n",
                secrets,
            )
        return 0

    summary = None
    try:
        summary, _ = redact(build_summary(trail), secrets)
    except Exception as exc:
        print(f"Summary could not be built: {type(exc).__name__}", file=sys.stderr)

    summary_path = args.summary or os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path and summary is not None:
        try:
            with open(summary_path, "a", encoding="utf-8") as handle:
                handle.write(summary)
        except Exception as exc:
            print(f"Job summary could not be written: {type(exc).__name__}", file=sys.stderr)

    if args.comment_file:
        if summary is None:
            summary = "## CI audit trail\n\nThe audit summary could not be built. See the workflow run logs.\n"
        write_comment(args.comment_file, summary, secrets)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
