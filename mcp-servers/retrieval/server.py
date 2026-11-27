#!/usr/bin/env python3
"""Retrieval MCP server with role and classification enforcement.

Authorization, the classification ceiling and auditing are Module 4
(AGENT_ROLE + allow-list.json). Search is the Module 3 implementation
(mcp/retrieval/server.py): all-MiniLM-L6-v2 embeddings in a sqlite-vec
cosine index, with an FTS5/BM25 keyword fallback when no vector match
clears the similarity threshold or the vector backend cannot be loaded.
"""

import datetime
import json
import os
import re
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any

try:
    from fastmcp import FastMCP
    from fastmcp.exceptions import ToolError
except Exception:  # pragma: no cover
    class ToolError(Exception):
        pass
    class FastMCP:
        def __init__(self, name: str):
            self.name = name
        def tool(self, func=None):
            def decorate(fn):
                return fn
            return decorate(func) if func else decorate
        def run(self):
            print(f"FastMCP not installed; {self.name} functions are importable only.")

mcp = FastMCP("retrieval")
CALLING_ROLE = os.environ.get("AGENT_ROLE", "")
ALLOW_LIST_PATH = Path(__file__).parent / "allow-list.json"
ALLOW_LIST = json.loads(ALLOW_LIST_PATH.read_text(encoding="utf-8"))
AUDIT_LOG_PATH = Path(os.environ.get("RETRIEVAL_AUDIT_LOG", "logs/retrieval-audit-log.jsonl"))
AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
CORPUS_PATH = Path(__file__).parent / "corpus.json"
CLASSIFICATION_ORDER = ["public", "internal", "confidential"]


def _record_audit(event: str, operation: str, detail: str) -> dict[str, Any]:
    record = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "event": event,
        "operation": operation,
        "role": CALLING_ROLE,
        "outcome": event,
        "detail": detail,
        "policy_reference": "docs/governance-policy.md",
    }
    with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as log:
        log.write(json.dumps(record) + "\n")
    return record


def _retrieval_ceiling() -> str:
    rule = ALLOW_LIST.get("retrieve", {}).get(CALLING_ROLE)
    if not rule or not rule.get("granted"):
        reason = (rule or {}).get("denial_reason", "role is not granted retrieval")
        _record_audit("authorization_denied", "retrieve", reason)
        raise ToolError(
            f"authorization_denied: role '{CALLING_ROLE}' may not call 'retrieve': "
            f"{reason}. See docs/governance-policy.md."
        )
    return str(rule["classification_ceiling"])


# --- Search backend (ported from Module 3 mcp/retrieval/server.py) ---------

# Optional directory of Module 3 reference markdown (front matter carries
# `classification`). Unset: only corpus.json is indexed.
REFERENCE_DIR = os.environ.get("RETRIEVAL_REFERENCE_DIR", "")
EMBEDDING_MODEL = os.environ.get("RETRIEVAL_EMBEDDING_MODEL", "all-MiniLM-L6-v2")
EMBEDDING_DIM = 384
SIMILARITY_THRESHOLD = float(os.environ.get("RETRIEVAL_SIMILARITY_THRESHOLD", "0.65"))
# Untagged or unknown classifications fail closed to the most sensitive level.
FALLBACK_CLASSIFICATION = CLASSIFICATION_ORDER[-1]

_index_lock = threading.Lock()
_index: dict[str, Any] | None = None


def _parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Read a leading ---...--- block of `key: value` lines."""
    metadata: dict[str, str] = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].strip().splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    metadata[key.strip()] = value.strip()
            return metadata, text[end + 4 :].strip()
    return metadata, text.strip()


def _chunk_paragraphs(body: str, min_words: int = 50, max_words: int = 300) -> list[str]:
    """Split on blank lines, merge small pieces and split over-large ones."""
    merged: list[str] = []
    buffer = ""
    for piece in (p.strip() for p in re.split(r"\n\s*\n", body)):
        if not piece:
            continue
        candidate = f"{buffer} {piece}".strip() if buffer else piece
        if len(candidate.split()) < min_words:
            buffer = candidate
        else:
            merged.append(candidate)
            buffer = ""
    if buffer:
        merged.append(buffer)
    chunks: list[str] = []
    for piece in merged:
        words = piece.split()
        for i in range(0, len(words), max_words):
            chunks.append(" ".join(words[i : i + max_words]))
    return [chunk for chunk in chunks if chunk.strip()]


def _load_documents() -> list[dict[str, Any]]:
    """corpus.json documents plus chunked reference markdown, if configured."""
    docs = list(json.loads(CORPUS_PATH.read_text(encoding="utf-8")))
    if REFERENCE_DIR:
        reference = Path(REFERENCE_DIR)
        if not reference.is_dir():
            raise FileNotFoundError(f"RETRIEVAL_REFERENCE_DIR not found: {REFERENCE_DIR}")
        for path in sorted(reference.glob("*.md")):
            metadata, body = _parse_front_matter(path.read_text(encoding="utf-8"))
            classification = metadata.get("classification")
            if classification not in CLASSIFICATION_ORDER:
                classification = FALLBACK_CLASSIFICATION
            for index, chunk in enumerate(_chunk_paragraphs(body)):
                docs.append(
                    {
                        "id": f"{path.stem}#{index}",
                        "title": path.name,
                        "classification": classification,
                        "text": chunk,
                    }
                )
    for doc in docs:
        if doc.get("classification") not in CLASSIFICATION_ORDER:
            doc["classification"] = FALLBACK_CLASSIFICATION
    return docs


def _load_vector_backend(conn: sqlite3.Connection) -> Any:
    """Load sqlite-vec and the embedding model; None if either is unavailable."""
    try:
        import sqlite_vec
        from sentence_transformers import SentenceTransformer

        conn.enable_load_extension(True)
        try:
            sqlite_vec.load(conn)
        finally:
            conn.enable_load_extension(False)
        return SentenceTransformer(EMBEDDING_MODEL)
    except Exception as exc:  # degrade to keyword search, loudly
        print(f"WARNING: vector retrieval unavailable ({exc}); using keyword search only.",
              file=sys.stderr, flush=True)
        return None


def _embed(model: Any, text: str) -> bytes:
    import sqlite_vec

    vector = model.encode(text, normalize_embeddings=True).tolist()
    return sqlite_vec.serialize_float32(vector)


def _get_index() -> dict[str, Any]:
    """Build the in-memory vector and keyword indexes once per process."""
    global _index
    with _index_lock:
        if _index is not None:
            return _index
        docs = _load_documents()
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.execute("CREATE VIRTUAL TABLE fts_docs USING fts5(body)")
        model = _load_vector_backend(conn)
        if model is not None:
            conn.execute(
                f"CREATE VIRTUAL TABLE vec_docs USING vec0("
                f"embedding float[{EMBEDDING_DIM}] distance_metric=cosine)"
            )
        for rowid, doc in enumerate(docs, start=1):
            body = f"{doc.get('title', '')} {doc.get('text', '')}"
            conn.execute("INSERT INTO fts_docs (rowid, body) VALUES (?, ?)", (rowid, body))
            if model is not None:
                conn.execute(
                    "INSERT INTO vec_docs (rowid, embedding) VALUES (?, ?)",
                    (rowid, _embed(model, body)),
                )
        conn.commit()
        _index = {"conn": conn, "docs": docs, "model": model}
        return _index


def _fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 OR-query of quoted tokens."""
    tokens = set(re.findall(r"[A-Za-z0-9_]+", text)) | set(re.findall(r"[A-Za-z0-9]+", text))
    ordered = sorted(tokens, key=lambda token: (-len(token), token.lower()))
    return " OR ".join(f'"{token}"' for token in ordered)


def _keyword_search(index: dict[str, Any], query: str, top_k: int) -> list[dict[str, Any]]:
    expression = _fts_query(query)
    if not expression:
        return []
    rows = index["conn"].execute(
        "SELECT rowid FROM fts_docs WHERE fts_docs MATCH ? ORDER BY bm25(fts_docs) LIMIT ?",
        (expression, top_k),
    ).fetchall()
    return [
        {**index["docs"][rowid - 1], "similarity_score": None, "retrieval_method": "keyword"}
        for (rowid,) in rows
    ]


def _vector_search(query: str, top_k: int) -> list[dict[str, Any]]:
    """Vector search above SIMILARITY_THRESHOLD; keyword fallback otherwise."""
    index = _get_index()
    model = index["model"]
    if model is not None:
        rows = index["conn"].execute(
            "SELECT rowid, distance FROM vec_docs WHERE embedding MATCH ? AND k = ? "
            "ORDER BY distance",
            (_embed(model, query), top_k),
        ).fetchall()
        results = []
        for rowid, distance in rows:
            score = round(1.0 - float(distance), 3)
            if score >= SIMILARITY_THRESHOLD:
                results.append(
                    {**index["docs"][rowid - 1], "similarity_score": score,
                     "retrieval_method": "vector"}
                )
        if results:
            return results
    return _keyword_search(index, query, top_k)


@mcp.tool
def retrieve(query: str, top_k: int = 5) -> list:
    ceiling = _retrieval_ceiling()
    max_level = CLASSIFICATION_ORDER.index(ceiling)
    candidates = _vector_search(query, top_k)
    visible = []
    withheld = 0
    for doc in candidates:
        if CLASSIFICATION_ORDER.index(doc["classification"]) <= max_level:
            visible.append(doc)
        else:
            withheld += 1
    if withheld:
        _record_audit(
            "classification_withheld",
            "retrieve",
            f"{withheld} result(s) above the '{ceiling}' ceiling were withheld",
        )
    _record_audit(
        "retrieval_success",
        "retrieve",
        f"{len(visible)} result(s) returned at the '{ceiling}' ceiling"
        f" via {candidates[0]['retrieval_method'] if candidates else 'no match'}",
    )
    return visible


if __name__ == "__main__":
    mcp.run()
