from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_context.feedback_model import (
    feedback_boost,
    feedback_boost_parts,
    feedback_model_path,
    load_feedback_model,
    query_family_for_text,
)
from agent_context.io import write_jsonl
from agent_context.resolver import fuse_candidates


@pytest.fixture(autouse=True)
def isolate_panel_feedback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")


def test_feedback_model_uses_source_project_and_group_keys(tmp_path: Path) -> None:
    out = tmp_path / "out"
    source = {
        "path": "/projects/ranker/src/rank.py",
        "relative_path": "src/rank.py",
        "source_id": "source-1",
        "source_chunk_id": "chunk-1",
        "doc_id": "doc-1",
        "project_id": "project-1",
        "project_path": "/projects/ranker",
        "project_name": "ranker",
        "source_group": "git_repositories",
    }
    write_jsonl(out / "feedback" / "mcp_feedback.jsonl", [{"selected_source": source, "rating": 5}])

    model = load_feedback_model(out)
    scores = model["source_scores"]

    assert {
        "path",
        "relative_path",
        "source_id",
        "source_chunk_id",
        "doc_id",
        "project_id",
        "project_path",
        "project_name",
        "source_group",
    } <= set(model["source_key_fields"])
    for key in (
        "/projects/ranker/src/rank.py",
        "path:/projects/ranker/src/rank.py",
        "source_id:source-1",
        "source_chunk_id:chunk-1",
        "doc_id:doc-1",
        "project_id:project-1",
        "project_path:/projects/ranker",
        "project_name:ranker",
        "source_group:git_repositories",
    ):
        assert scores[key] > 0

    assert feedback_boost(model, {"project_id": "project-1", "source_group": "git_repositories"}) > 0


def test_feedback_model_supports_negative_ratings(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_jsonl(
        out / "feedback" / "mcp_feedback.jsonl",
        [
            {"selected_source": {"path": "/repo/good.py"}, "rating": 5},
            {"selected_source": {"path": "/repo/bad.py"}, "rating": "negative"},
            {"selected_source": {"path": "/repo/one_star.py"}, "rating": 1},
        ],
    )

    model = load_feedback_model(out)

    assert feedback_boost(model, {"path": "/repo/good.py"}) > 0
    assert feedback_boost(model, {"path": "/repo/bad.py"}) < 0
    assert feedback_boost(model, {"path": "/repo/one_star.py"}) < 0


def test_arena_pairwise_feedback_boosts_winners_and_penalizes_losers(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_jsonl(
        out / "feedback" / "arena_feedback.jsonl",
        [
            {
                "goal": "告诉我本地项目里如何构建个人推荐系统",
                "winner": "candidate-1",
                "winner_route": "route-a",
                "candidates": [
                    {
                        "candidate_id": "candidate-1",
                        "route": "route-a",
                        "selected": True,
                        "source_keys": ["/repo/good.py", "project_name:good"],
                    },
                    {
                        "candidate_id": "candidate-2",
                        "route": "route-b",
                        "selected": False,
                        "source_keys": ["/repo/bad.py", "project_name:bad"],
                    },
                    {
                        "candidate_id": "candidate-3",
                        "route": "route-c",
                        "selected": False,
                        "source_keys": ["/repo/weak.py"],
                    },
                ],
            }
        ],
    )

    model = load_feedback_model(out)
    scores = model["source_scores"]
    stats = model["pairwise_stats"]
    family = query_family_for_text("告诉我本地项目里如何构建个人推荐系统")

    assert stats["comparisons"] == 2
    assert stats["route_wins"]["route-a"] == 2
    assert stats["route_losses"]["route-b"] == 1
    assert family in model["query_family_source_scores"]
    assert model["pairwise_elo"]["source_ratings"]["/repo/good.py"] > 1000
    assert model["pairwise_elo"]["source_ratings"]["/repo/bad.py"] < 1000
    assert model["query_family_pairwise_elo"][family]["source_priors"]["/repo/good.py"] > 0
    assert model["pairwise_bradley_terry"]["source"]["abilities"]["/repo/good.py"] > 0
    assert model["pairwise_bradley_terry"]["source"]["abilities"]["/repo/bad.py"] < 0
    assert model["pairwise_bradley_terry"]["source"]["source_priors"]["/repo/good.py"] > 0
    assert model["query_family_pairwise_bradley_terry"][family]["source"]["source_priors"]["/repo/good.py"] > 0
    assert scores["/repo/good.py"] > 0
    assert scores["/repo/bad.py"] < 0
    assert feedback_boost(model, {"path": "/repo/good.py"}) > 0
    assert feedback_boost(model, {"path": "/repo/bad.py"}) < 0
    assert feedback_boost(model, {"path": "/repo/good.py"}, query_family=family) > feedback_boost(
        model,
        {"path": "/repo/good.py"},
        query_family="session_history",
    )

    sources = fuse_candidates(
        [
            {
                "path": "/repo/bad.py",
                "source_group": "git_repositories",
                "score": 0.4,
                "score_parts": {},
                "matched_queries": ["ranking"],
            },
            {
                "path": "/repo/good.py",
                "source_group": "git_repositories",
                "score": 0.4,
                "score_parts": {},
                "matched_queries": ["ranking"],
            },
        ],
        2,
        feedback_model=model,
        query_family=family,
    )

    assert sources[0]["path"] == "/repo/good.py"
    assert sources[0]["resolver_score_parts"]["feedback"] > 0
    assert sources[0]["resolver_score_parts"]["feedback_query_family_source"] > 0
    assert sources[0]["resolver_score_parts"]["feedback_pairwise_elo_source"] > 0
    assert sources[0]["resolver_score_parts"]["feedback_query_family_pairwise_elo_source"] > 0
    assert sources[0]["resolver_score_parts"]["feedback_pairwise_bradley_terry_source"] > 0
    assert sources[0]["resolver_score_parts"]["feedback_query_family_pairwise_bradley_terry_source"] > 0
    assert sources[1]["resolver_score_parts"]["feedback"] < 0
    assert sources[1]["resolver_score_parts"]["feedback_query_family_source"] < 0
    broad_parts = feedback_boost_parts(
        model,
        {"source_group": "git_repositories"},
        query_family=family,
    )
    assert broad_parts["query_family_source"] == 0.0


def test_replay_expected_source_supervision_can_rerank_within_query_family(tmp_path: Path) -> None:
    out = tmp_path / "out"
    goal = "告诉我本地项目里如何构建个人推荐系统"
    expected = "data/preference_state.json"
    write_jsonl(
        out / "manifests" / "project_documents.jsonl",
        [
            {
                "path": f"/repo/{expected}",
                "relative_path": expected,
                "project_name": "ranker",
                "source_group": "git_repositories",
            },
            {
                "path": "/repo/config/default.json",
                "relative_path": "config/default.json",
                "project_name": "ranker",
                "source_group": "git_repositories",
            },
        ],
    )
    write_jsonl(
        out / "feedback" / "replay_cases.generated.jsonl",
        [
            {
                "goal": goal,
                "source_scope": "gitProjects",
                "expected_source": expected,
                "origin": "retrieval_eval_report",
            }
        ],
    )

    model = load_feedback_model(out)
    family = query_family_for_text(goal)

    assert model["feedback_model_version"] == "0.7"
    assert model["replay_supervision_cases"] == 1
    assert model["source_scores"][expected] > 0
    assert "config/default.json" not in model["source_scores"]
    assert model["query_family_source_scores"][family][expected] > model["source_scores"][expected]

    sources = fuse_candidates(
        [
            {
                "path": "/repo/config/default.json",
                "relative_path": "config/default.json",
                "source_group": "git_repositories",
                "score": 0.75,
                "score_parts": {},
                "matched_queries": ["recommendation system"],
            },
            {
                "path": f"/repo/{expected}",
                "relative_path": expected,
                "source_group": "git_repositories",
                "score": 0.55,
                "score_parts": {},
                "matched_queries": ["recommendation system"],
            },
        ],
        2,
        feedback_model=model,
        query_family=family,
    )

    assert sources[0]["relative_path"] == expected
    assert sources[0]["resolver_score_parts"]["feedback_source"] > 0
    assert sources[0]["resolver_score_parts"]["feedback_query_family_source"] > 0


def test_feedback_tiebreak_prefers_stronger_exact_source_signal() -> None:
    family = "recommendation_system+project_code"
    model = {
        "source_scores": {
            "config/default.json": 0.25,
            "data/preference_state.json": 0.25,
        },
        "query_family_source_scores": {
            family: {
                "data/preference_state.json": 0.14,
            }
        },
    }

    sources = fuse_candidates(
        [
            {
                "path": "/repo/config/default.json",
                "relative_path": "config/default.json",
                "source_group": "git_repositories",
                "score": 0.85,
                "score_parts": {},
                "matched_queries": ["recommendation system", "ranking feedback", "local project"],
            },
            {
                "path": "/repo/data/preference_state.json",
                "relative_path": "data/preference_state.json",
                "source_group": "git_repositories",
                "score": 0.65,
                "score_parts": {},
                "matched_queries": ["recommendation system", "ranking feedback", "local project"],
            },
        ],
        2,
        feedback_model=model,
        query_family=family,
    )

    assert sources[0]["relative_path"] == "data/preference_state.json"
    assert sources[0]["score"] == sources[1]["score"] == 1.0
    assert sources[0]["resolver_score_parts"]["feedback"] > sources[1]["resolver_score_parts"]["feedback"]


def test_feedback_model_is_deterministic_serializable_and_resolver_loadable(tmp_path: Path) -> None:
    out = tmp_path / "out"
    source = {
        "path": "/projects/ranker/src/rank.py",
        "source_id": "source-1",
        "source_chunk_id": "chunk-1",
        "project_id": "project-1",
        "project_path": "/projects/ranker",
        "project_name": "ranker",
        "source_group": "git_repositories",
    }
    write_jsonl(out / "feedback" / "mcp_feedback.jsonl", [{"selected_source": source, "rating": 5}])

    model = load_feedback_model(out)
    serialized = json.loads(json.dumps(model, ensure_ascii=False, sort_keys=True))

    assert serialized == model
    assert json.loads(feedback_model_path(out).read_text(encoding="utf-8")) == model
    assert load_feedback_model(out) == model
    assert list(model["source_scores"]) == sorted(model["source_scores"])

    sources = fuse_candidates(
        [
            {
                "path": source["path"],
                "source_id": source["source_id"],
                "source_chunk_id": source["source_chunk_id"],
                "project_id": source["project_id"],
                "project_path": source["project_path"],
                "project_name": source["project_name"],
                "source_group": source["source_group"],
                "score": 0.4,
                "score_parts": {},
                "matched_queries": ["ranking"],
                "snippet": "ranking feedback loop",
            }
        ],
        1,
        feedback_model=serialized,
    )

    assert sources[0]["path"] == source["path"]
    assert sources[0]["resolver_score_parts"]["feedback"] > 0
