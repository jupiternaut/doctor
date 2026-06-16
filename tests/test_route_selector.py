from __future__ import annotations

import json
from pathlib import Path

from agent_context.io import write_text
from agent_context.mcp_server import mcp_route_selector_model
from agent_context.resolver import fuse_candidates
from agent_context.route_selector import (
    load_route_selector_model,
    route_selector_boost_parts,
    route_selector_model_path,
    write_route_selector_model,
)


def write_eval_report(out: Path) -> None:
    write_text(
        out / "reports" / "retrieval_eval_20260616000000000000.json",
        json.dumps(
            {
                "retrieval_eval_version": "0.1",
                "cases": [
                    {
                        "query": "recommendation system ranking feedback local project",
                        "query_family": "recommendation_system+project_code",
                        "source": "projects",
                        "expected_sources": ["data/preference_state.json"],
                        "winner_backend": "hash-vector-lite",
                        "backends": {
                            "hash-vector-lite": {"expected_rank": 1, "top1_hit": True},
                            "fastembed-rerank": {"expected_rank": 1, "top1_hit": True},
                            "semantic-fusion": {"expected_rank": 5, "top1_hit": False},
                        },
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


def test_route_selector_model_reads_retrieval_eval_reports(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_eval_report(out)
    write_text(out / "reports" / "retrieval_eval_cases_20260616000000000000.json", '{"cases": [{"ignored": true}]}\n')

    model = load_route_selector_model(out)

    assert model["reports_seen"] == 1
    assert model["cases_seen"] == 1
    assert model["backend_priors"]["hash-vector-lite"] > 0
    assert model["backend_priors"]["semantic-fusion"] < 0
    assert model["source_backend_priors"]["projects"]["semantic-fusion"] < 0
    assert Path(model["model_path"]).exists()


def test_route_selector_model_can_be_compiled_and_loaded_from_file(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_eval_report(out)

    written = write_route_selector_model(out)
    path = route_selector_model_path(out)
    loaded = load_route_selector_model(out)

    assert path.exists()
    assert written["cases_seen"] == 1
    assert json.loads(path.read_text(encoding="utf-8")) == loaded
    assert mcp_route_selector_model(out_root=str(out))["model_path"] == str(path)


def test_route_selector_boost_parts_map_backends_to_candidate_channels(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_eval_report(out)
    model = load_route_selector_model(out)

    project_parts = route_selector_boost_parts(
        model,
        {"source_group": "git_repositories", "retrieval_channels": ["project_code_index"]},
        query_family="recommendation_system+project_code",
    )
    semantic_parts = route_selector_boost_parts(
        model,
        {"source_group": "git_repositories", "retrieval_channels": ["semantic_index"]},
        query_family="recommendation_system+project_code",
    )

    assert project_parts["total"] > 0
    assert semantic_parts["total"] < 0


def test_fuse_candidates_applies_route_selector_prior(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_eval_report(out)
    model = load_route_selector_model(out)
    candidates = [
        {
            "path": "/tmp/semantic.md",
            "score": 0.42,
            "source_group": "git_repositories",
            "matched_queries": ["recommendation system"],
            "retrieval_channels": ["semantic_index"],
        },
        {
            "path": "/tmp/project.md",
            "score": 0.30,
            "source_group": "git_repositories",
            "matched_queries": ["recommendation system"],
            "retrieval_channels": ["project_code_index"],
        },
    ]

    ranked = fuse_candidates(
        candidates,
        2,
        route_selector_model=model,
        query_family="recommendation_system+project_code",
    )

    assert ranked[0]["path"] == "/tmp/project.md"
    assert ranked[0]["resolver_score_parts"]["route_selector"] > 0
    assert ranked[1]["resolver_score_parts"]["route_selector"] < 0
