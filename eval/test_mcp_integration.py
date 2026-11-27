"""Capstone Milestone 1: Module 3 backends behind Module 4 governance.

Checks the integration of the Module 3 SQLite store and sqlite-vec/FTS5
retrieval into mcp-servers/, which authorize on AGENT_ROLE. Role-by-operation
authorization is covered exhaustively in eval/test_policy.py; this file covers
what that suite cannot:

- storage persists across real server process restarts (stdio transport);
- denials hold for real server processes, not just in-process calls;
- vector retrieval returns semantic matches from the Module 3 reference corpus;
- keyword fallback runs when no vector match clears the threshold;
- the classification ceiling applies to reference documents.

The vector tests need sqlite-vec and the all-MiniLM-L6-v2 model.
"""

import asyncio
import importlib.util
import json
import os
import sys
import uuid
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import PythonStdioTransport
from fastmcp.exceptions import ToolError

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVERS_DIR = REPO_ROOT / "mcp-servers"
REFERENCE_DIR = REPO_ROOT / ".memory" / "reference"


def _stdio_call(server, role, env, tool, args):
    """Start a fresh server process with AGENT_ROLE=role and call one tool."""
    transport = PythonStdioTransport(
        script_path=str(SERVERS_DIR / server / "server.py"),
        python_cmd=sys.executable,
        env={**os.environ, **env, "AGENT_ROLE": role},
    )

    async def run():
        async with Client(transport) as client:
            result = await client.call_tool(tool, args)
            return json.loads(result.content[0].text)

    return asyncio.run(run())


@pytest.fixture
def storage_env(tmp_path):
    return {
        "STORAGE_AUDIT_LOG": str(tmp_path / "storage-audit-log.jsonl"),
        "STORAGE_STORE": str(tmp_path / "storage-store.db"),
    }


def test_storage_persists_across_process_restarts(storage_env):
    written = _stdio_call(
        "storage", "project-manager", storage_env,
        "write_entry", {"key": "task-outcome-1", "value": "PASS"},
    )
    assert written == {"ok": True, "key": "task-outcome-1"}

    # Each call below is a new server process reading the same SQLite file.
    read = _stdio_call("storage", "orchestrator", storage_env, "read_entry", {"key": "task-outcome-1"})
    assert read == {"key": "task-outcome-1", "value": "PASS"}
    listed = _stdio_call("storage", "orchestrator", storage_env, "list_entries", {})
    assert listed == {"keys": ["task-outcome-1"]}


@pytest.mark.parametrize(
    "role, tool, args",
    [
        ("tester", "read_entry", {"key": "task-outcome-1"}),
        ("planner", "write_entry", {"key": "k", "value": "v"}),
        ("project-manager", "delete_entry", {"key": "task-outcome-1"}),
        ("dependency-auditor", "list_entries", {}),
    ],
)
def test_storage_denies_unauthorized_roles_in_real_process(storage_env, role, tool, args):
    _stdio_call("storage", "project-manager", storage_env,
                "write_entry", {"key": "task-outcome-1", "value": "PASS"})
    with pytest.raises(ToolError, match=f"authorization_denied: role '{role}' may not call '{tool}'"):
        _stdio_call("storage", role, storage_env, tool, args)
    # The stored entry is untouched by the denied call.
    read = _stdio_call("storage", "orchestrator", storage_env, "read_entry", {"key": "task-outcome-1"})
    assert read["value"] == "PASS"


@pytest.mark.parametrize("role", ["project-manager", "orchestrator", "dependency-auditor"])
def test_retrieve_denied_for_unauthorized_roles_in_real_process(tmp_path, role):
    env = {"RETRIEVAL_AUDIT_LOG": str(tmp_path / "retrieval-audit-log.jsonl")}
    with pytest.raises(ToolError, match=f"authorization_denied: role '{role}' may not call 'retrieve'"):
        _stdio_call("retrieval", role, env, "retrieve", {"query": "scrape"})


def _load_retrieval(monkeypatch, tmp_path, role="tester", threshold=None):
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setenv("AGENT_ROLE", role)
    monkeypatch.setenv("RETRIEVAL_AUDIT_LOG", str(tmp_path / "retrieval-audit-log.jsonl"))
    monkeypatch.setenv("RETRIEVAL_REFERENCE_DIR", str(REFERENCE_DIR))
    if threshold is not None:
        monkeypatch.setenv("RETRIEVAL_SIMILARITY_THRESHOLD", str(threshold))
    spec = importlib.util.spec_from_file_location(
        f"_integration_retrieval_{uuid.uuid4().hex}", SERVERS_DIR / "retrieval" / "server.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _retrieve(module, query, top_k=3):
    async def run():
        async with Client(module.mcp) as client:
            result = await client.call_tool("retrieve", {"query": query, "top_k": top_k})
            return json.loads(result.content[0].text)

    return asyncio.run(run())


def _audit(tmp_path):
    path = tmp_path / "retrieval-audit-log.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_vector_retrieval_returns_semantic_match(monkeypatch, tmp_path):
    server = _load_retrieval(monkeypatch, tmp_path)
    # No shared keywords with the lesson title; matched by meaning.
    results = _retrieve(server, "why did scraping return zero listings")
    assert results, "expected at least one vector result"
    top = results[0]
    assert top["retrieval_method"] == "vector"
    assert top["id"].startswith("lesson-empty-scrape-diagnostics")
    assert top["similarity_score"] >= server.SIMILARITY_THRESHOLD
    assert "via vector" in _audit(tmp_path)[-1]["detail"]


def test_keyword_fallback_when_no_vector_match_clears_threshold(monkeypatch, tmp_path):
    # A threshold above 1.0 means no cosine match can qualify.
    server = _load_retrieval(monkeypatch, tmp_path, threshold=1.01)
    results = _retrieve(server, "why did scraping return zero listings")
    assert server._get_index()["model"] is not None, "vector backend must be loaded"
    assert results
    assert {doc["retrieval_method"] for doc in results} == {"keyword"}
    assert all(doc["similarity_score"] is None for doc in results)
    assert "via keyword" in _audit(tmp_path)[-1]["detail"]


def test_reference_documents_respect_classification_ceiling(monkeypatch, tmp_path):
    server = _load_retrieval(monkeypatch, tmp_path)
    # lesson-session-data-security.md is tagged confidential.
    results = _retrieve(server, "storing browser cookies and session state securely", top_k=5)
    assert all(doc["classification"] in {"public", "internal"} for doc in results)
    assert not any(doc["id"].startswith("lesson-session-data-security") for doc in results)
    events = [record["event"] for record in _audit(tmp_path)]
    assert events == ["classification_withheld", "retrieval_success"]
