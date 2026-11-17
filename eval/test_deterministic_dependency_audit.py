"""Tests for the deterministic dependency-audit report builder."""

import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from audit_dependencies_deterministic import (  # noqa: E402
    build_report,
    load_npm_json,
    parse_audit,
    parse_outdated,
    render_markdown,
)

SCRIPT = "scripts/audit_dependencies_deterministic.py"
FIXTURES_REL = "eval/fixtures/dependency-audit"
FIXTURES = REPO / FIXTURES_REL
AGENT = REPO / ".claude" / "agents" / "dependency-auditor.md"


def fixture(name):
    data, error = load_npm_json(FIXTURES / name)
    assert error is None, error
    return data


def entry(name, audit, outdated, audit_input="audit.json", outdated_input="outdated.json"):
    return {
        "name": name,
        "audit_input": audit_input,
        "audit": audit,
        "outdated_input": outdated_input,
        "outdated": outdated,
        "lock_input": None,
        "lock_error": None,
    }


def error_audit(message):
    return {"status": "error", "error": message, "counts": None, "rows": [], "warnings": []}


def summary_section(markdown):
    return markdown.split("### Summary", 1)[1].split("### Vulnerabilities", 1)[0]


def reverse_keys(value):
    """Return a copy with every dict's key order reversed (lists keep their order)."""
    if isinstance(value, dict):
        return {key: reverse_keys(value[key]) for key in reversed(list(value))}
    if isinstance(value, list):
        return [reverse_keys(item) for item in value]
    return value


def output_headings():
    text = AGENT.read_text(encoding="utf-8")
    section = text.split("## Output", 1)[1].split("\n## ", 1)[0]
    return [line.strip() for line in section.splitlines() if line.startswith("### ")]


# --- parse_audit -----------------------------------------------------------------


def test_v7_counts_come_from_metadata_and_rows_are_sorted():
    result = parse_audit(fixture("audit-v7-vulns.json"), manifest="tracker-api")
    assert result["status"] == "ok"
    assert result["error"] is None
    assert result["counts"] == {
        "info": 0,
        "low": 0,
        "moderate": 2,
        "high": 1,
        "critical": 0,
        "total": 3,
    }
    assert [row["package"] for row in result["rows"]] == ["body-parser", "lodash", "qs"]
    assert all(row["manifest"] == "tracker-api" for row in result["rows"])
    assert result["warnings"] == []


def test_clean_audit_is_ok_with_zero_counts():
    result = parse_audit(fixture("audit-v7-clean.json"))
    assert result["status"] == "ok"
    assert result["counts"]["total"] == 0
    assert result["rows"] == []


def test_advisory_fixed_and_installed_formatting_with_lockfile():
    lock = fixture("package-lock-api.json")
    rows = {row["package"]: row for row in parse_audit(fixture("audit-v7-vulns.json"), lock)["rows"]}

    lodash = rows["lodash"]
    assert lodash["advisory"] == (
        "Prototype Pollution in lodash (https://github.com/advisories/GHSA-p6mc-m468-83gw)"
    )
    assert lodash["fixed"] == "lodash@4.17.21 (semver-major)"
    assert lodash["installed"] == "4.17.20"
    assert lodash["severity"] == "high"

    body_parser = rows["body-parser"]
    assert body_parser["advisory"] == "via qs"
    assert body_parser["fixed"] == "available"
    # Not present in the lockfile, so falls back to the vulnerable range.
    assert body_parser["installed"] == "unknown (range <=1.20.1)"

    qs = rows["qs"]
    assert qs["fixed"] == "none"
    assert qs["installed"] == "6.9.0"


def test_installed_falls_back_to_range_without_lockfile():
    rows = {row["package"]: row for row in parse_audit(fixture("audit-v7-vulns.json"))["rows"]}
    assert rows["lodash"]["installed"] == "unknown (range <4.17.21)"
    assert rows["qs"]["installed"] == "unknown (range <6.10.3)"


def test_multiple_via_entries_are_joined():
    data = fixture("audit-v7-vulns.json")
    data["vulnerabilities"]["body-parser"]["via"] = [
        "qs",
        {"title": "Body parser DoS", "url": "https://example.invalid/adv"},
    ]
    rows = {row["package"]: row for row in parse_audit(data)["rows"]}
    assert rows["body-parser"]["advisory"] == "Body parser DoS (https://example.invalid/adv); via qs"


def test_fix_available_missing_is_none():
    data = fixture("audit-v7-vulns.json")
    del data["vulnerabilities"]["lodash"]["fixAvailable"]
    rows = {row["package"]: row for row in parse_audit(data)["rows"]}
    assert rows["lodash"]["fixed"] == "none"


def test_total_mismatch_adds_warning():
    data = fixture("audit-v7-vulns.json")
    data["metadata"]["vulnerabilities"]["total"] = 5
    result = parse_audit(data)
    assert result["status"] == "ok"
    assert result["counts"]["total"] == 5
    assert len(result["warnings"]) == 1
    assert "(5)" in result["warnings"][0] and "(3)" in result["warnings"][0]


# --- error inputs: counts must be None and Summary must show "error" -------------


def _assert_audit_error_rendered(audit):
    assert audit["status"] == "error"
    assert audit["counts"] is None
    assert audit["rows"] == []
    report = build_report(
        [
            entry("tracker-api", audit, parse_outdated({})),
            entry("tracker-client", parse_audit(fixture("audit-v7-clean.json")), parse_outdated({})),
        ]
    )
    assert report["status"] == "error"
    summary = summary_section(render_markdown(report))
    api_line = next(line for line in summary.splitlines() if line.startswith("- tracker-api:"))
    assert "vulnerable packages: error:" in api_line
    assert "vulnerable packages: 0" not in api_line
    return api_line


def test_audit_error_object_is_error():
    audit = parse_audit(fixture("audit-error.json"))
    assert "ENOLOCK" in audit["error"]
    assert "requires an existing lockfile" in audit["error"]
    _assert_audit_error_rendered(audit)


def test_empty_file_is_error(tmp_path):
    data, error = load_npm_json(FIXTURES / "empty.json")
    assert data is None and "empty file" in error
    whitespace = tmp_path / "blank.json"
    whitespace.write_text("  \n\t\n", encoding="utf-8")
    data, error = load_npm_json(whitespace)
    assert data is None and "empty file" in error
    _assert_audit_error_rendered(error_audit(error))


def test_invalid_json_is_error():
    data, error = load_npm_json(FIXTURES / "invalid.json")
    assert data is None and error.startswith("invalid JSON")
    _assert_audit_error_rendered(error_audit(error))


def test_non_object_top_level_is_error(tmp_path):
    path = tmp_path / "list.json"
    path.write_text("[1, 2]", encoding="utf-8")
    data, error = load_npm_json(path)
    assert data is None and "not an object" in error


def test_missing_file_is_error(tmp_path):
    data, error = load_npm_json(tmp_path / "nope.json")
    assert data is None and "file not found" in error


def test_v6_shape_is_unsupported():
    audit = parse_audit(fixture("audit-v6-legacy.json"))
    assert audit["error"] == "unsupported audit format (npm v6)"
    _assert_audit_error_rendered(audit)


@pytest.mark.parametrize("bad_metadata", [None, {}, {"vulnerabilities": "lots"}, "x"])
def test_missing_metadata_counts_is_error(bad_metadata):
    data = fixture("audit-v7-vulns.json")
    if bad_metadata is None:
        del data["metadata"]
    else:
        data["metadata"] = bad_metadata
    audit = parse_audit(data)
    _assert_audit_error_rendered(audit)


def test_missing_single_count_is_error():
    data = fixture("audit-v7-vulns.json")
    del data["metadata"]["vulnerabilities"]["critical"]
    audit = parse_audit(data)
    _assert_audit_error_rendered(audit)


# --- parse_outdated --------------------------------------------------------------


def test_outdated_empty_is_ok_zero():
    result = parse_outdated(fixture("outdated-empty.json"))
    assert result == {"status": "ok", "error": None, "count": 0, "rows": []}


def test_outdated_error_is_error():
    result = parse_outdated(fixture("outdated-error.json"))
    assert result["status"] == "error"
    assert result["count"] is None
    assert "ENOTFOUND" in result["error"]
    report = build_report(
        [
            entry("tracker-api", parse_audit(fixture("audit-v7-clean.json")), result),
            entry("tracker-client", parse_audit(fixture("audit-v7-clean.json")), parse_outdated({})),
        ]
    )
    summary = summary_section(render_markdown(report))
    api_line = next(line for line in summary.splitlines() if line.startswith("- tracker-api:"))
    assert "outdated packages: error:" in api_line


def test_outdated_rows_sorted_and_missing_current():
    result = parse_outdated(fixture("outdated-some.json"), manifest="tracker-client")
    assert result["status"] == "ok"
    assert result["count"] == 2
    assert [row["package"] for row in result["rows"]] == ["react", "vite"]
    vite = result["rows"][1]
    assert vite == {
        "package": "vite",
        "current": "not installed",
        "wanted": "8.0.1",
        "latest": "8.1.0",
        "manifest": "tracker-client",
    }


# --- report ----------------------------------------------------------------------


def _full_report(audit_data, outdated_data, lock=None):
    return build_report(
        [
            entry(
                "tracker-client",
                parse_audit(fixture("audit-v7-clean.json"), manifest="tracker-client"),
                parse_outdated(outdated_data, manifest="tracker-client"),
            ),
            entry(
                "tracker-api",
                parse_audit(audit_data, lock, manifest="tracker-api"),
                parse_outdated({}, manifest="tracker-api"),
            ),
        ]
    )


def test_shuffled_input_gives_identical_output():
    audit = fixture("audit-v7-vulns.json")
    outdated = fixture("outdated-some.json")
    lock = fixture("package-lock-api.json")
    first = _full_report(audit, outdated, lock)
    second = _full_report(reverse_keys(copy.deepcopy(audit)), reverse_keys(outdated), reverse_keys(lock))
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert render_markdown(first) == render_markdown(second)


def test_report_orders_manifests_api_then_client():
    report = _full_report(fixture("audit-v7-vulns.json"), fixture("outdated-some.json"))
    assert [m["name"] for m in report["manifests"]] == ["tracker-api", "tracker-client"]
    assert [c["command"] for c in report["commands"]] == [
        "npm audit --json --prefix tracker-api",
        "npm audit --json --prefix tracker-client",
        "npm outdated --json --prefix tracker-api",
        "npm outdated --json --prefix tracker-client",
    ]
    assert all(c["result"] == "parsed" for c in report["commands"])


def test_recommendations_one_per_finding_and_require_approval():
    report = _full_report(fixture("audit-v7-vulns.json"), fixture("outdated-some.json"))
    recs = report["recommendations"]
    assert len(recs) == 3 + 2
    for rec in recs:
        assert "human approval" in rec
        assert "Implementer" in rec
    assert any("lodash@4.17.21 (semver-major)" in rec for rec in recs)
    assert any("qs" in rec and "no fix available" in rec for rec in recs)
    assert any("vite" in rec and "8.0.1" in rec and "8.1.0" in rec for rec in recs)


def test_markdown_contains_all_agent_output_headings():
    headings = output_headings()
    assert headings == [
        "### Summary",
        "### Vulnerabilities",
        "### Outdated dependencies",
        "### Recommendations",
        "### Commands run",
    ]
    markdown = render_markdown(
        _full_report(fixture("audit-v7-vulns.json"), fixture("outdated-some.json"))
    )
    lines = markdown.splitlines()
    for heading in headings:
        assert heading in lines


# --- CLI -------------------------------------------------------------------------


def run_cli(tmp_path, *, audit_api="audit-v7-vulns.json", audit_client="audit-v7-clean.json",
            outdated_api="outdated-empty.json", outdated_client="outdated-some.json",
            lock_api="package-lock-api.json", tag="run"):
    output = tmp_path / tag / "result.json"
    report = tmp_path / tag / "report.md"
    args = [
        sys.executable,
        SCRIPT,
        "--audit-api", f"{FIXTURES_REL}/{audit_api}",
        "--audit-client", f"{FIXTURES_REL}/{audit_client}",
        "--outdated-api", f"{FIXTURES_REL}/{outdated_api}",
        "--outdated-client", f"{FIXTURES_REL}/{outdated_client}",
        "--output", str(output),
        "--report", str(report),
    ]
    if lock_api:
        args += ["--lock-api", f"{FIXTURES_REL}/{lock_api}"]
    proc = subprocess.run(args, cwd=REPO, capture_output=True, text=True)
    return proc, output, report


def test_cli_exit_zero_when_vulnerabilities_found(tmp_path):
    proc, output, report = run_cli(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert output.exists() and report.exists()
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "ok"
    assert len(result["vulnerabilities"]) == 3
    # Paths are kept as given, not resolved to absolute paths.
    assert result["commands"][0]["input"] == f"{FIXTURES_REL}/audit-v7-vulns.json"


@pytest.mark.parametrize(
    "override",
    [
        {"audit_api": "audit-error.json"},
        {"audit_client": "audit-v6-legacy.json"},
        {"audit_api": "empty.json"},
        {"audit_client": "invalid.json"},
        {"outdated_api": "outdated-error.json"},
        {"outdated_client": "invalid.json"},
        {"lock_api": "invalid.json"},
    ],
)
def test_cli_exit_one_on_bad_input(tmp_path, override):
    proc, output, report = run_cli(tmp_path, **override)
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "error"
    assert "error" in report.read_text(encoding="utf-8")


def test_cli_missing_input_path_exit_one_without_traceback(tmp_path):
    proc, output, report = run_cli(tmp_path, audit_api="does-not-exist.json")
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
    assert "Traceback" not in proc.stdout
    result = json.loads(output.read_text(encoding="utf-8"))
    assert "file not found" in result["manifests"][0]["audit"]["error"]
    assert result["manifests"][0]["audit"]["counts"] is None


def test_cli_two_runs_are_byte_identical(tmp_path):
    _, out_a, md_a = run_cli(tmp_path, tag="a")
    _, out_b, md_b = run_cli(tmp_path, tag="b")
    assert out_a.read_bytes() == out_b.read_bytes()
    assert md_a.read_bytes() == md_b.read_bytes()
    assert out_a.read_bytes().endswith(b"}\n")


# --- guard -----------------------------------------------------------------------


def test_script_has_no_subprocess_or_network_usage():
    source = (REPO / SCRIPT).read_text(encoding="utf-8")
    assert "import subprocess" not in source
    assert "socket" not in source
    assert "urllib" not in source
    assert not re.search(r"^\s*(import|from)\s+(http|requests|httpx)\b", source, re.MULTILINE)
