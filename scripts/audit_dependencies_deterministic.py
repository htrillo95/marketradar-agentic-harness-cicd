#!/usr/bin/env python3
"""Deterministic dependency-audit report builder for MarketRadar.

Parses saved `npm audit --json` and `npm outdated --json` output for tracker-api and
tracker-client and writes a JSON result plus an optional Markdown report whose
headings match the "## Output" section of `.claude/agents/dependency-auditor.md`.
No language model, subprocess or network access is involved.

Producing the inputs (run by the caller's shell; the dependency-auditor agent itself
cannot redirect output, because its Bash hook allows only the bare commands):

    npm audit --json --prefix tracker-api      > audit-api.json
    npm audit --json --prefix tracker-client   > audit-client.json
    npm outdated --json --prefix tracker-api   > outdated-api.json
    npm outdated --json --prefix tracker-client > outdated-client.json

Then:

    python scripts/audit_dependencies_deterministic.py \\
        --audit-api audit-api.json --audit-client audit-client.json \\
        --outdated-api outdated-api.json --outdated-client outdated-client.json \\
        --lock-api tracker-api/package-lock.json \\
        --lock-client tracker-client/package-lock.json \\
        --output audit.json --report audit.md

Exit codes: 0 when every input parsed (even if vulnerabilities or outdated packages
are found), 1 when any input is missing, empty, invalid or an npm error object.
npm's own exit codes (non-zero when it finds vulnerabilities or outdated packages)
are irrelevant here; only the saved JSON is inspected.
"""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MANIFESTS = ("tracker-api", "tracker-client")
SEVERITIES = ("info", "low", "moderate", "high", "critical")
COUNT_KEYS = SEVERITIES + ("total",)
APPROVAL = "Requires human approval; implemented by the Implementer."
OUTPUT_HEADINGS = (
    "### Summary",
    "### Vulnerabilities",
    "### Outdated dependencies",
    "### Recommendations",
    "### Commands run",
)


def load_npm_json(path: str | Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return (data, None) on success or (None, error) on failure. Never raises."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, f"file not found: {path}"
    except UnicodeDecodeError:
        return None, f"invalid JSON: {path}: not UTF-8 text"
    except OSError as exc:
        return None, f"cannot read {path}: {exc.strerror or exc.__class__.__name__}"
    if text.strip() == "":
        return None, f"empty file: {path}"
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {path}: {exc.msg} (line {exc.lineno}, column {exc.colno})"
    if not isinstance(data, dict):
        return None, f"top-level JSON is not an object: {path}"
    return data, None


def _audit_error(message: str) -> Dict[str, Any]:
    return {"status": "error", "error": message, "counts": None, "rows": [], "warnings": []}


def _npm_error_message(error: Dict[str, Any]) -> str:
    code = error.get("code", "unknown")
    summary = str(error.get("summary") or "").strip()
    return f"npm error {code}: {summary}" if summary else f"npm error {code}"


def _format_advisory(via: Any) -> str:
    items = via if isinstance(via, list) else [via]
    parts: List[str] = []
    for item in items:
        if isinstance(item, dict):
            title = item.get("title") or "untitled advisory"
            url = item.get("url")
            parts.append(f"{title} ({url})" if url else str(title))
        elif isinstance(item, str):
            parts.append(f"via {item}")
    return "; ".join(sorted(parts)) if parts else "unknown"


def _format_fixed(fix: Any) -> str:
    if isinstance(fix, dict):
        text = f"{fix.get('name', '?')}@{fix.get('version', '?')}"
        if fix.get("isSemVerMajor"):
            text += " (semver-major)"
        return text
    if fix is True:
        return "available"
    return "none"


def _installed_version(name: str, entry: Dict[str, Any], lock: Optional[Dict[str, Any]]) -> str:
    if isinstance(lock, dict):
        packages = lock.get("packages")
        if isinstance(packages, dict):
            node = packages.get(f"node_modules/{name}")
            if isinstance(node, dict) and isinstance(node.get("version"), str):
                return node["version"]
    return f"unknown (range {entry.get('range', 'unknown')})"


def parse_audit(
    data: Dict[str, Any], lock: Optional[Dict[str, Any]] = None, manifest: str = ""
) -> Dict[str, Any]:
    """Parse npm v7+ `npm audit --json` output. Counts are None on error, never 0."""
    if not isinstance(data, dict):
        return _audit_error("audit output is not a JSON object")
    if isinstance(data.get("error"), dict):
        return _audit_error(_npm_error_message(data["error"]))
    if "advisories" in data and "vulnerabilities" not in data:
        return _audit_error("unsupported audit format (npm v6)")
    vulns = data.get("vulnerabilities")
    if not isinstance(vulns, dict):
        return _audit_error("missing or invalid 'vulnerabilities' map")
    metadata = data.get("metadata")
    meta_counts = metadata.get("vulnerabilities") if isinstance(metadata, dict) else None
    if not isinstance(meta_counts, dict):
        return _audit_error("missing or invalid 'metadata.vulnerabilities' counts")
    counts: Dict[str, int] = {}
    for key in COUNT_KEYS:
        value = meta_counts.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            return _audit_error(f"missing or invalid count 'metadata.vulnerabilities.{key}'")
        counts[key] = value

    rows: List[Dict[str, str]] = []
    for name, entry in vulns.items():
        if not isinstance(entry, dict):
            return _audit_error(f"invalid vulnerability entry: {name}")
        rows.append(
            {
                "package": name,
                "installed": _installed_version(name, entry, lock),
                "severity": str(entry.get("severity", "unknown")),
                "advisory": _format_advisory(entry.get("via", [])),
                "fixed": _format_fixed(entry.get("fixAvailable", False)),
                "manifest": manifest,
            }
        )
    rows.sort(key=lambda row: (row["package"], row["advisory"]))

    warnings: List[str] = []
    if counts["total"] != len(vulns):
        warnings.append(
            f"metadata.vulnerabilities.total ({counts['total']}) does not match "
            f"the number of vulnerabilities entries ({len(vulns)})"
        )
    return {"status": "ok", "error": None, "counts": counts, "rows": rows, "warnings": warnings}


def _outdated_error(message: str) -> Dict[str, Any]:
    return {"status": "error", "error": message, "count": None, "rows": []}


def parse_outdated(data: Dict[str, Any], manifest: str = "") -> Dict[str, Any]:
    """Parse `npm outdated --json` output. `{}` means nothing is outdated."""
    if not isinstance(data, dict):
        return _outdated_error("outdated output is not a JSON object")
    if isinstance(data.get("error"), dict):
        return _outdated_error(_npm_error_message(data["error"]))
    rows: List[Dict[str, str]] = []
    for name, entry in data.items():
        if not isinstance(entry, dict):
            return _outdated_error(f"invalid outdated entry: {name}")
        rows.append(
            {
                "package": name,
                "current": str(entry.get("current", "not installed")),
                "wanted": str(entry.get("wanted", "unknown")),
                "latest": str(entry.get("latest", "unknown")),
                "manifest": manifest,
            }
        )
    rows.sort(key=lambda row: row["package"])
    return {"status": "ok", "error": None, "count": len(rows), "rows": rows}


def audit_manifest(
    name: str, audit_path: str, outdated_path: str, lock_path: Optional[str] = None
) -> Dict[str, Any]:
    """Load and parse the inputs for one manifest. Never raises on bad inputs."""
    lock, lock_error = (None, None)
    if lock_path is not None:
        lock, lock_error = load_npm_json(lock_path)

    audit_data, audit_error = load_npm_json(audit_path)
    audit = _audit_error(audit_error) if audit_error else parse_audit(audit_data, lock, name)

    outdated_data, outdated_error = load_npm_json(outdated_path)
    outdated = (
        _outdated_error(outdated_error) if outdated_error else parse_outdated(outdated_data, name)
    )
    return {
        "name": name,
        "audit_input": audit_path,
        "audit": audit,
        "outdated_input": outdated_path,
        "outdated": outdated,
        "lock_input": lock_path,
        "lock_error": lock_error,
    }


def _vuln_recommendation(row: Dict[str, str]) -> str:
    prefix = f"[{row['manifest']}] {row['package']} ({row['severity']}):"
    if row["fixed"] == "none":
        action = "no fix available; review the advisory and consider mitigation or replacement."
    elif row["fixed"] == "available":
        action = "a fix is available; upgrade to a patched version."
    else:
        action = f"upgrade to {row['fixed']}."
    return f"{prefix} {action} {APPROVAL}"


def _outdated_recommendation(row: Dict[str, str]) -> str:
    return (
        f"[{row['manifest']}] {row['package']}: upgrade from {row['current']} to "
        f"{row['wanted']} (wanted) or {row['latest']} (latest). {APPROVAL}"
    )


def build_report(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Combine per-manifest results (from audit_manifest) into one report dict."""
    order = {name: index for index, name in enumerate(MANIFESTS)}
    entries = sorted(entries, key=lambda e: (order.get(e["name"], len(order)), e["name"]))

    manifests: List[Dict[str, Any]] = []
    vulnerabilities: List[Dict[str, str]] = []
    outdated: List[Dict[str, str]] = []
    audit_commands: List[Dict[str, str]] = []
    outdated_commands: List[Dict[str, str]] = []
    failed = False

    for entry in entries:
        name, audit, out = entry["name"], entry["audit"], entry["outdated"]
        lock_status = "not provided"
        if entry.get("lock_input") is not None:
            lock_status = f"error: {entry['lock_error']}" if entry.get("lock_error") else "loaded"
        if audit["status"] != "ok" or out["status"] != "ok" or entry.get("lock_error"):
            failed = True
        manifests.append(
            {
                "name": name,
                "audit": {
                    "input": entry["audit_input"],
                    "status": audit["status"],
                    "error": audit["error"],
                    "counts": audit["counts"],
                    "warnings": audit["warnings"],
                },
                "outdated": {
                    "input": entry["outdated_input"],
                    "status": out["status"],
                    "error": out["error"],
                    "count": out["count"],
                },
                "lockfile": {"input": entry.get("lock_input"), "status": lock_status},
            }
        )
        vulnerabilities.extend(audit["rows"])
        outdated.extend(out["rows"])
        audit_commands.append(
            {
                "command": f"npm audit --json --prefix {name}",
                "input": entry["audit_input"],
                "result": "parsed" if audit["status"] == "ok" else f"error: {audit['error']}",
            }
        )
        outdated_commands.append(
            {
                "command": f"npm outdated --json --prefix {name}",
                "input": entry["outdated_input"],
                "result": "parsed" if out["status"] == "ok" else f"error: {out['error']}",
            }
        )

    recommendations = [_vuln_recommendation(row) for row in vulnerabilities]
    recommendations += [_outdated_recommendation(row) for row in outdated]
    return {
        "status": "error" if failed else "ok",
        "manifests": manifests,
        "vulnerabilities": vulnerabilities,
        "outdated": outdated,
        "recommendations": recommendations,
        "commands": audit_commands + outdated_commands,
    }


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(headers: List[str], keys: List[str], rows: List[Dict[str, Any]]) -> List[str]:
    if not rows:
        return ["None."]
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for row in rows:
        lines.append("| " + " | ".join(_cell(row[key]) for key in keys) + " |")
    return lines


def render_markdown(report: Dict[str, Any]) -> str:
    """Render the report using the dependency-auditor Output headings."""
    lines: List[str] = ["# Dependency audit report", "", f"Status: {report['status']}", ""]

    lines += [OUTPUT_HEADINGS[0], ""]
    for manifest in report["manifests"]:
        audit, out = manifest["audit"], manifest["outdated"]
        if audit["status"] == "ok":
            counts = audit["counts"]
            detail = ", ".join(f"{sev} {counts[sev]}" for sev in reversed(SEVERITIES))
            vuln_text = f"{counts['total']} ({detail})"
        else:
            vuln_text = f"error: {audit['error']}"
        out_text = str(out["count"]) if out["status"] == "ok" else f"error: {out['error']}"
        lines.append(f"- {manifest['name']}: vulnerable packages: {vuln_text}; outdated packages: {out_text}")
        if manifest["lockfile"]["input"] is not None:
            lines.append(
                f"  - lockfile {manifest['lockfile']['input']}: {manifest['lockfile']['status']}"
            )
        for warning in audit["warnings"]:
            lines.append(f"  - warning: {warning}")
    lines.append("")

    lines += [OUTPUT_HEADINGS[1], ""]
    lines += _table(
        ["Package", "Installed", "Severity", "Advisory", "Fixed", "Manifest"],
        ["package", "installed", "severity", "advisory", "fixed", "manifest"],
        report["vulnerabilities"],
    )
    lines.append("")

    lines += [OUTPUT_HEADINGS[2], ""]
    lines += _table(
        ["Package", "Current", "Wanted", "Latest", "Manifest"],
        ["package", "current", "wanted", "latest", "manifest"],
        report["outdated"],
    )
    lines.append("")

    lines += [OUTPUT_HEADINGS[3], ""]
    if report["recommendations"]:
        lines += [f"- {item}" for item in report["recommendations"]]
    else:
        lines.append("None.")
    lines.append("")

    lines += [OUTPUT_HEADINGS[4], ""]
    lines += _table(["Command", "Input", "Result"], ["command", "input", "result"], report["commands"])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--audit-api", required=True, help="saved npm audit --json for tracker-api")
    parser.add_argument("--audit-client", required=True, help="saved npm audit --json for tracker-client")
    parser.add_argument("--outdated-api", required=True, help="saved npm outdated --json for tracker-api")
    parser.add_argument(
        "--outdated-client", required=True, help="saved npm outdated --json for tracker-client"
    )
    parser.add_argument("--lock-api", default=None, help="tracker-api package-lock.json")
    parser.add_argument("--lock-client", default=None, help="tracker-client package-lock.json")
    parser.add_argument("--output", required=True, help="path to write the JSON result")
    parser.add_argument("--report", default=None, help="path to write the Markdown report")
    args = parser.parse_args()

    report = build_report(
        [
            audit_manifest("tracker-api", args.audit_api, args.outdated_api, args.lock_api),
            audit_manifest("tracker-client", args.audit_client, args.outdated_client, args.lock_client),
        ]
    )

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    if args.report:
        md = Path(args.report)
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(render_markdown(report), encoding="utf-8")

    return 1 if report["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
