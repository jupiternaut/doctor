from __future__ import annotations

import json
from pathlib import Path

from agent_context.mirror_ranker import (
    ranker_eval_path,
    ranker_model_path,
    record_pairwise_feedback,
    score_candidates,
    train_pairwise_ranker,
    training_examples_path,
)


def test_pairwise_feedback_trains_and_ranks_winner_first(tmp_path: Path) -> None:
    out = tmp_path / "out"
    goal = "recommend local ranking feedback project"
    candidate_a = {
        "id": "A",
        "path": "/repo/a.md",
        "bm25": 0.3,
        "vector": 0.2,
        "path_score": 0.2,
        "source_zone": "workspace",
        "profile_prior": 0.4,
    }
    candidate_b = {
        "id": "B",
        "path": "/repo/b.md",
        "bm25": 0.3,
        "vector": 0.2,
        "path_score": 0.2,
        "source_zone": "workspace",
        "profile_prior": 0.4,
    }

    record = record_pairwise_feedback(out, goal=goal, winner=candidate_a, loser=candidate_b, reason="A matches intent")
    model = train_pairwise_ranker(out)
    scored = score_candidates(out, goal, [candidate_b, candidate_a])

    assert Path(record["training_example_path"]) == training_examples_path(out)
    assert training_examples_path(out).exists()
    assert ranker_model_path(out).exists()
    assert ranker_eval_path(out).exists()
    assert json.loads(ranker_model_path(out).read_text(encoding="utf-8")) == model
    assert scored["ranked_candidates"][0]["id"] == "A"
    assert scored["ranked_candidates"][1]["id"] == "B"


def test_negative_feedback_lowers_score_and_score_parts_explain(tmp_path: Path) -> None:
    out = tmp_path / "out"
    goal = "find best Mirror Lab candidate"
    good = {"id": "good", "path": "/repo/good.md", "bm25": 0.4}
    bad = {"id": "bad", "path": "/repo/bad.md", "bm25": 0.4}

    record_pairwise_feedback(out, goal=goal, winner=good, loser=bad, reason="bad was rejected")
    train_pairwise_ranker(out)
    ranked = score_candidates(out, goal, [bad, good])["ranked_candidates"]
    by_id = {candidate["id"]: candidate for candidate in ranked}

    assert by_id["bad"]["score"] < by_id["good"]["score"]
    assert by_id["bad"]["score_parts"]["recent_feedback"] < 0
    assert by_id["good"]["score_parts"]["recent_feedback"] > 0
    assert set(by_id["good"]["score_parts"]) >= {
        "bm25",
        "vector",
        "path",
        "source_zone",
        "profile_prior",
        "recent_feedback",
        "total",
    }
    assert by_id["good"]["explanation"]


def test_missing_features_default_to_zero(tmp_path: Path) -> None:
    out = tmp_path / "out"
    scored = score_candidates(out, "goal", [{"id": "empty"}])["ranked_candidates"][0]

    assert scored["feature_values"] == {
        "bm25": 0.0,
        "vector": 0.0,
        "path": 0.0,
        "source_zone": 0.0,
        "profile_prior": 0.0,
        "recent_feedback": 0.0,
    }
    assert scored["score"] == 0.0


def test_exploration_slot_marks_low_exposure_candidate(tmp_path: Path) -> None:
    out = tmp_path / "out"
    candidates = [
        {"id": "top", "bm25": 1.0, "exposure_count": 10},
        {"id": "middle", "bm25": 0.7, "exposure_count": 8},
        {"id": "low-exposure", "bm25": 0.1, "exposure_count": 0},
    ]

    ranked = score_candidates(out, "goal", candidates, exploration_slots=1)["ranked_candidates"]
    explored = [candidate for candidate in ranked if candidate["exploration"]]

    assert ranked[0]["id"] == "top"
    assert len(explored) == 1
    assert explored[0]["id"] == "low-exposure"
    assert explored[0]["rank"] in {len(ranked), len(ranked) - 1}
