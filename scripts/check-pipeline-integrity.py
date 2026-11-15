#!/usr/bin/env python3
"""Assert the pipeline's safety invariants hold in the workflow file.

Job-level checks only (a step-level continue-on-error, such as the advisory
rubric step in eval-gate, is allowed). Checks 1 and 2 are fail-closed
allowlists: any value not explicitly allowed fails.
1. Every gating job exists, and its job-level `continue-on-error` is either
   absent or the YAML boolean `false`. Strings ("false", "true", expressions
   such as "${{ true }}"), numbers and null all fail.
2. The audit-trail job exists and its `if` is a string that normalizes to
   exactly `always()`: surrounding whitespace is stripped, an outer `${{ }}`
   wrapper is removed, then all whitespace is removed. Any other condition,
   including compound ones such as `always() && ...`, fails.
3. The change-type-check classifier exists.

With --output PATH a JSON report is written (parent directories are created):
{"passed": bool, "workflow": str, "checks": [{"name", "passed", "detail"}], "errors": [str]}

A missing workflow file, invalid YAML, or a top-level or `jobs` value that is
not a mapping is reported as a failure. Exits 0 when every invariant holds,
1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

GATING_JOBS = {"policy-gate", "eval-gate", "pipeline-integrity"}
DEFAULT_WORKFLOW = ".github/workflows/ci.yml"


def load_jobs(path: Path):
    """Return (jobs mapping, None) or (None, error message)."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, f"workflow file not found: {path}"
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"workflow file could not be read: {type(exc).__name__}"
    try:
        workflow = yaml.safe_load(text)
    except yaml.YAMLError:
        return None, "workflow file is not valid YAML"
    except (Exception, RecursionError) as exc:
        return None, f"workflow file could not be parsed: {type(exc).__name__}"
    if not isinstance(workflow, dict):
        return None, "workflow top level is not a mapping"
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        return None, "workflow 'jobs' is not a mapping"
    return jobs, None


def describe_value(value) -> str:
    """Deterministic, JSON-style rendering of a YAML value for error messages."""
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError, RecursionError):
        return repr(value)


def is_always_condition(value) -> bool:
    """True only if `value` is a string that normalizes to exactly `always()`.

    Strict allowlist: a logically always-true condition such as
    `always() || false` is still rejected, because accepting compound
    expressions would require evaluating them.
    """
    if not isinstance(value, str):
        return False
    text = value.strip()
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2].strip()
    return "".join(text.split()) == "always()"


def check_jobs(jobs: dict) -> tuple[list[dict], list[str]]:
    checks: list[dict] = []
    errors: list[str] = []

    def record(name: str, passed: bool, detail: str, error: str | None = None) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})
        if error:
            errors.append(error)

    # 1. No gating job may disable itself with continue-on-error. Only an
    #    absent key or the boolean False is allowed (`is False`, since 0 == False).
    for name in sorted(GATING_JOBS):
        job = jobs.get(name)
        if name not in jobs:
            message = f"required gating job '{name}' is missing"
            record(f"gating_job_present:{name}", False, message, message)
            record(f"gating_job_not_continue_on_error:{name}", False, "not checked: job is missing")
            continue
        record(f"gating_job_present:{name}", True, f"job '{name}' exists")
        if not isinstance(job, dict):
            message = f"gating job '{name}' is not a mapping"
            record(f"gating_job_not_continue_on_error:{name}", False, message, message)
        elif "continue-on-error" not in job or job["continue-on-error"] is False:
            record(
                f"gating_job_not_continue_on_error:{name}", True, "job-level continue-on-error is absent or false"
            )
        elif job["continue-on-error"] is True:
            message = f"gating job '{name}' has continue-on-error: true, which disables the gate"
            record(f"gating_job_not_continue_on_error:{name}", False, message, message)
        else:
            value = describe_value(job["continue-on-error"])
            message = f"gating job '{name}' has continue-on-error: {value}; only false or absent is allowed"
            record(f"gating_job_not_continue_on_error:{name}", False, message, message)

    # 2. The audit trail job must exist and always run. Its `if` must be
    #    exactly always() (see is_always_condition); a missing key fails.
    audit = jobs.get("audit-trail")
    if "audit-trail" not in jobs:
        message = "audit-trail job is missing"
        record("audit_trail_present", False, message, message)
        record("audit_trail_if_always", False, "not checked: audit-trail job is missing")
    else:
        record("audit_trail_present", True, "job 'audit-trail' exists")
        condition = audit.get("if") if isinstance(audit, dict) else None
        if not is_always_condition(condition):
            message = "audit-trail job must keep 'if: always()'"
            record("audit_trail_if_always", False, message, message)
        else:
            record("audit_trail_if_always", True, "audit-trail 'if' is exactly always()")

    # 3. The change classifier must still exist.
    if "change-type-check" not in jobs:
        message = "change-type-check classifier is missing"
        record("change_type_check_present", False, message, message)
    else:
        record("change_type_check_present", True, "job 'change-type-check' exists")

    return checks, errors


def write_report(output: str, report: dict) -> bool:
    try:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return True
    except Exception as exc:
        print(f"Pipeline integrity report could not be written: {type(exc).__name__}", file=sys.stderr)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    parser.add_argument("--output", default=None, help="optional path to a JSON report")
    args = parser.parse_args()

    workflow_path = Path(args.workflow)
    jobs, load_error = load_jobs(workflow_path)
    if load_error:
        checks = [{"name": "workflow_loadable", "passed": False, "detail": load_error}]
        errors = [load_error]
    else:
        checks, errors = check_jobs(jobs)

    report = {
        "passed": not errors,
        "workflow": str(workflow_path),
        "checks": checks,
        "errors": errors,
    }
    report_written = write_report(args.output, report) if args.output else True

    if errors:
        print("Pipeline integrity check FAILED:")
        for problem in errors:
            print(f"  - {problem}")
        return 1

    if not report_written:
        return 1

    print("Pipeline integrity check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
