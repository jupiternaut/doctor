from __future__ import annotations

import sqlite3
from pathlib import Path

from agent_context.evidence_index import build_evidence_index, evidence_index_path_for, search_evidence_index
from agent_context.io import write_jsonl


def test_build_evidence_index_from_manifests_and_packs(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_jsonl(
        out / "manifests" / "chunks.jsonl",
        [
            {
                "chunk_id": "doc:1:chunk:1",
                "doc_id": "doc:1",
                "path": "/Users/example/Downloads/profile.md",
                "relative_path": "profile.md",
                "text": "用户画像 evidence record with recommendation feedback.",
                "score": 0.4,
            }
        ],
    )
    write_jsonl(
        out / "packs" / "task-1" / "sources.jsonl",
        [
            {
                "source_id": "code:recommender",
                "source_group": "git_repositories",
                "provider": "project_code_index",
                "path": "/Users/example/project/src/recommender.py",
                "relative_path": "src/recommender.py",
                "snippet": "def rerank_candidates(feedback): return feedback",
                "score": 0.8,
                "evidence": {
                    "schema_version": "0.1",
                    "evidence_id": "code:recommender",
                    "source_type": "code",
                    "source_group": "git_repositories",
                    "provider": "project_code_index",
                    "path": "/Users/example/project/src/recommender.py",
                    "relative_path": "src/recommender.py",
                    "title": "recommender.py",
                    "text": "rerank_candidates feedback",
                    "summary": "Code evidence for feedback rerank.",
                    "quote": "def rerank_candidates(feedback): return feedback",
                    "location": {},
                    "score": 0.8,
                    "score_parts": {},
                    "retrieval": {"query": "recommendation", "matched_queries": [], "channels": ["project_index"]},
                    "identifiers": {"source_id": "code:recommender"},
                    "entities": [],
                    "edges": [{"type": "implements", "target_id": "concept:rerank", "weight": 0.7}],
                    "embedding_refs": [{"kind": "text", "ref": "derived:text"}],
                    "permissions": {},
                    "provenance": {},
                },
            }
        ],
    )

    result = build_evidence_index(out)

    assert result["status"] == "ok"
    assert result["records_indexed"] == 2
    index_path = evidence_index_path_for(out)
    assert index_path.exists()
    with sqlite3.connect(index_path) as conn:
        edge_count = conn.execute("SELECT count(*) FROM evidence_edges").fetchone()[0]
    assert edge_count >= 7

    search = search_evidence_index(out, "feedback rerank", limit=2)

    assert search["status"] == "ok"
    assert search["records_searched"] == 2
    assert search["sources"][0]["source_id"] == "code:recommender"
    assert search["sources"][0]["evidence"]["source_type"] == "code"


def test_search_evidence_index_reports_missing_index(tmp_path: Path) -> None:
    result = search_evidence_index(tmp_path / "out", "anything", limit=3)

    assert result["status"] == "missing"
    assert result["sources"] == []
