from __future__ import annotations

from pathlib import Path

from agent_context.grep_route import run_grep_route_probe
from agent_context.io import write_jsonl
from agent_context.resolver import build_resolution_plan, fuse_candidates


def test_grep_route_probe_scores_project_manifest_hits(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_jsonl(
        out / "manifests" / "projects.jsonl",
        [
            {
                "provider": "git_project",
                "path": "/Users/example/recommendation-system",
                "name": "recommendation-system",
                "text": "个人推荐系统 recall ranking feedback rerank architecture.",
            }
        ],
    )

    probe = run_grep_route_probe(
        out,
        "告诉我本地所有项目里如何构建个人推荐系统",
        source_scope="all",
    )

    assert probe["status"] == "ok"
    assert "git_repositories" in probe["provider_scores"]
    assert probe["provider_scores"]["git_repositories"]["hits"] > 0
    assert probe["provider_scores"]["git_repositories"]["score"] >= 0.2


def test_resolver_uses_grep_route_probe_to_activate_workflow_docs(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_jsonl(out / "manifests" / "projects.jsonl", [])
    write_jsonl(out / "manifests" / "sessions.jsonl", [])
    write_jsonl(
        out / "manifests" / "workflows.jsonl",
        [
            {
                "provider": "workflow_doc",
                "path": "/Users/example/workflows/ROUTE_PROBE.md",
                "relative_path": "ROUTE_PROBE.md",
                "workflow_id": "workflow:route-probe",
                "title": "Route Probe",
                "text": "rare-route-token handoff instructions for context routing.",
            }
        ],
    )

    plan = build_resolution_plan(out, "rare-route-token", limit=3)

    assert plan["grep_route_probe"]["provider_scores"]["workflow_docs"]["hits"] > 0
    assert plan["selected_sources"][0] == "workflow_docs"
    assert "grep route probe" in plan["source_reasons"]["workflow_docs"]


def test_grep_route_probe_enters_final_rerank_score(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_jsonl(
        out / "manifests" / "projects.jsonl",
        [
            {
                "provider": "git_project",
                "path": "/Users/example/recommendation-system",
                "name": "recommendation-system",
                "text": "personal recommendation feedback rerank route evidence",
            }
        ],
    )
    probe = run_grep_route_probe(out, "recommendation feedback rerank", source_scope="all")

    sources = fuse_candidates(
        [
            {
                "source_id": "project:recommendation",
                "source_group": "git_repositories",
                "path": "/Users/example/recommendation-system",
                "score": 0.4,
                "matched_queries": ["recommendation feedback rerank"],
                "snippet": "project evidence",
            }
        ],
        limit=1,
        grep_route_probe=probe,
    )

    parts = sources[0]["resolver_score_parts"]
    assert parts["grep_route"] > 0
    assert parts["grep_route_provider_score"] >= 0.2
    assert "grep_route=" in sources[0]["why_selected"]
