from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from .io import append_jsonl, read_jsonl, write_text


MIRROR_RANKER_VERSION = "0.2"
TRAINING_EXAMPLES_FILENAME = "training_examples.jsonl"
RANKER_MODEL_FILENAME = "ranker_model.json"
RANKER_EVAL_FILENAME = "ranker_eval_latest.md"
FEATURE_NAMES = (
    "bm25",
    "vector",
    "path",
    "source_zone",
    "profile_prior",
    "recent_feedback",
)
DEFAULT_WEIGHTS = {
    "bm25": 0.18,
    "vector": 0.18,
    "path": 0.10,
    "source_zone": 0.06,
    "profile_prior": 0.16,
    "recent_feedback": 0.32,
}
LEARNING_RATE = 0.18
EPOCHS = 12
MAX_ABS_FEATURE_VALUE = 1.0
MAX_ABS_WEIGHT = 3.0
RECENT_FEEDBACK_WINDOW = 50


def record_pairwise_feedback(
    out_root: Path,
    *,
    goal: str,
    winner: dict,
    loser: dict,
    reason: str = "",
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    if not str(goal).strip():
        raise ValueError("goal is required")
    example = {
        "mirror_ranker_training_example_version": MIRROR_RANKER_VERSION,
        "created_at": now_iso(),
        "goal": str(goal),
        "reason": str(reason or ""),
        "winner": json_safe(winner),
        "loser": json_safe(loser),
        "winner_keys": candidate_keys(winner),
        "loser_keys": candidate_keys(loser),
        "winner_features": feature_values(winner, goal=str(goal), recent_feedback_override=1.0),
        "loser_features": feature_values(loser, goal=str(goal), recent_feedback_override=-1.0),
    }
    path = training_examples_path(out_root)
    append_jsonl(path, example)
    examples_seen = len(read_jsonl(path))
    return {
        "training_example_path": str(path),
        "examples_seen": examples_seen,
        "example": example,
    }


def train_pairwise_ranker(out_root: Path) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    examples = normalized_training_examples(read_jsonl(training_examples_path(out_root)))
    weights = dict(DEFAULT_WEIGHTS)

    for _ in range(EPOCHS):
        for example in examples:
            winner_features = example["winner_features"]
            loser_features = example["loser_features"]
            diff = {name: winner_features[name] - loser_features[name] for name in FEATURE_NAMES}
            margin = sum(weights[name] * diff[name] for name in FEATURE_NAMES)
            gradient = logistic_loss_gradient(margin)
            for name in FEATURE_NAMES:
                weights[name] = clamp(weights[name] + LEARNING_RATE * gradient * diff[name], MAX_ABS_WEIGHT)

    eval_result = evaluate_examples(weights, examples)
    model = {
        "mirror_ranker_model_version": MIRROR_RANKER_VERSION,
        "created_at": now_iso(),
        "feature_names": list(FEATURE_NAMES),
        "weights": {name: round(weights[name], 6) for name in FEATURE_NAMES},
        "defaults": {
            "missing_feature": 0.0,
            "max_abs_feature_value": MAX_ABS_FEATURE_VALUE,
            "exploration_slots_supported": 1,
        },
        "training": {
            "examples_seen": len(examples),
            "epochs": EPOCHS,
            "learning_rate": LEARNING_RATE,
            "accuracy": eval_result["accuracy"],
        },
        "model_path": str(ranker_model_path(out_root)),
        "training_example_path": str(training_examples_path(out_root)),
        "report_path": str(ranker_eval_path(out_root)),
    }
    write_text(ranker_model_path(out_root), json.dumps(model, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(ranker_eval_path(out_root), render_eval_report(model, eval_result, examples))
    return model


def score_candidates(
    out_root: Path,
    goal: str,
    candidates: list[dict[str, Any]],
    *,
    exploration_slots: int = 0,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    model = load_or_train_ranker(out_root)
    weights = {name: float((model.get("weights") or {}).get(name, DEFAULT_WEIGHTS[name])) for name in FEATURE_NAMES}
    feedback_index = recent_feedback_index(read_jsonl(training_examples_path(out_root)))
    scored = []
    for position, candidate in enumerate(candidates):
        features = feature_values(candidate, goal=str(goal), feedback_index=feedback_index)
        score_parts = {
            name: round(weights[name] * features[name], 6)
            for name in FEATURE_NAMES
        }
        score = round(sum(score_parts.values()), 6)
        scored.append(
            {
                **json_safe(candidate),
                "ranker_candidate_index": position,
                "score": score,
                "score_parts": {**score_parts, "total": score},
                "feature_values": features,
                "explanation": explain_score(score_parts),
                "exploration": False,
            }
        )

    ranked = sorted(scored, key=ranking_key)
    ranked = apply_exploration_slot(ranked, exploration_slots=exploration_slots)
    for index, candidate in enumerate(ranked, start=1):
        candidate["rank"] = index

    return {
        "mirror_ranker_score_version": MIRROR_RANKER_VERSION,
        "goal": str(goal),
        "model_path": str(ranker_model_path(out_root)),
        "ranked_candidates": ranked,
    }


def training_examples_path(out_root: Path) -> Path:
    return out_root.expanduser().resolve() / "feedback" / TRAINING_EXAMPLES_FILENAME


def ranker_model_path(out_root: Path) -> Path:
    return out_root.expanduser().resolve() / "feedback" / RANKER_MODEL_FILENAME


def ranker_eval_path(out_root: Path) -> Path:
    return out_root.expanduser().resolve() / "reports" / RANKER_EVAL_FILENAME


def load_or_train_ranker(out_root: Path) -> dict[str, Any]:
    model_path = ranker_model_path(out_root)
    examples_path = training_examples_path(out_root)
    if model_path.exists() and model_is_fresh(model_path, examples_path):
        try:
            model = json.loads(model_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return train_pairwise_ranker(out_root)
        if model.get("mirror_ranker_model_version") == MIRROR_RANKER_VERSION:
            return model
    return train_pairwise_ranker(out_root)


def model_is_fresh(model_path: Path, examples_path: Path) -> bool:
    if not model_path.exists():
        return False
    if not examples_path.exists():
        return True
    try:
        return model_path.stat().st_mtime >= examples_path.stat().st_mtime
    except OSError:
        return False


def normalized_training_examples(records: list[dict]) -> list[dict[str, Any]]:
    examples = []
    for record in records:
        winner_features = normalize_feature_mapping(record.get("winner_features") or {})
        loser_features = normalize_feature_mapping(record.get("loser_features") or {})
        examples.append(
            {
                "goal": str(record.get("goal") or ""),
                "winner": record.get("winner") or {},
                "loser": record.get("loser") or {},
                "winner_features": winner_features,
                "loser_features": loser_features,
            }
        )
    return examples


def normalize_feature_mapping(values: dict[str, Any]) -> dict[str, float]:
    return {name: numeric_feature(values.get(name)) for name in FEATURE_NAMES}


def feature_values(
    candidate: dict[str, Any],
    *,
    goal: str,
    feedback_index: dict[str, float] | None = None,
    recent_feedback_override: float | None = None,
) -> dict[str, float]:
    values = {
        "bm25": numeric_candidate_feature(candidate, "bm25", aliases=("bm25_score", "text_score", "keyword_score")),
        "vector": numeric_candidate_feature(candidate, "vector", aliases=("vector_score", "semantic_score", "embedding_score")),
        "path": numeric_candidate_feature(candidate, "path", aliases=("path_score", "path_match", "path_prior")),
        "source_zone": source_zone_feature(candidate),
        "profile_prior": numeric_candidate_feature(candidate, "profile_prior", aliases=("profile_score", "personal_prior")),
        "recent_feedback": 0.0,
    }
    if recent_feedback_override is not None:
        values["recent_feedback"] = numeric_feature(recent_feedback_override)
    else:
        explicit_feedback = numeric_candidate_feature(candidate, "recent_feedback", aliases=("feedback_score",))
        indexed_feedback = feedback_value_for_candidate(candidate, feedback_index or {})
        values["recent_feedback"] = indexed_feedback if indexed_feedback != 0.0 else explicit_feedback
    return {name: numeric_feature(values[name]) for name in FEATURE_NAMES}


def numeric_candidate_feature(candidate: dict[str, Any], name: str, *, aliases: tuple[str, ...] = ()) -> float:
    score_parts = candidate.get("score_parts") if isinstance(candidate.get("score_parts"), dict) else {}
    feature_parts = candidate.get("feature_values") if isinstance(candidate.get("feature_values"), dict) else {}
    for key in (name, *aliases):
        if key in feature_parts:
            return numeric_feature(feature_parts.get(key))
        if key in score_parts:
            return numeric_feature(score_parts.get(key))
        value = candidate.get(key)
        if is_number_like(value):
            return numeric_feature(value)
    return 0.0


def source_zone_feature(candidate: dict[str, Any]) -> float:
    explicit = numeric_candidate_feature(
        candidate,
        "source_zone",
        aliases=("source_zone_score", "zone_score", "zone_prior"),
    )
    if explicit != 0.0:
        return explicit
    zone = str(candidate.get("source_zone") or candidate.get("zone") or "").strip().lower()
    if not zone:
        return 0.0
    zone_priors = {
        "hot": 1.0,
        "workspace": 0.8,
        "project": 0.7,
        "repo": 0.7,
        "memory": 0.5,
        "docs": 0.3,
        "download": 0.2,
        "external": -0.2,
        "archive": -0.4,
    }
    return numeric_feature(zone_priors.get(zone, 0.0))


def numeric_feature(value: Any) -> float:
    if not is_number_like(value):
        return 0.0
    return round(clamp(float(value), MAX_ABS_FEATURE_VALUE), 6)


def is_number_like(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, str):
        try:
            return math.isfinite(float(value))
        except ValueError:
            return False
    return False


def recent_feedback_index(records: list[dict]) -> dict[str, float]:
    stats: dict[str, float] = {}
    window = records[-RECENT_FEEDBACK_WINDOW:]
    total = max(len(window), 1)
    for index, record in enumerate(window, start=1):
        recency_weight = index / total
        for key in record.get("winner_keys") or candidate_keys(record.get("winner") or {}):
            stats[str(key)] = stats.get(str(key), 0.0) + recency_weight
        for key in record.get("loser_keys") or candidate_keys(record.get("loser") or {}):
            stats[str(key)] = stats.get(str(key), 0.0) - recency_weight
    return {key: numeric_feature(value) for key, value in stats.items()}


def feedback_value_for_candidate(candidate: dict[str, Any], feedback_index: dict[str, float]) -> float:
    matches = [feedback_index[key] for key in candidate_keys(candidate) if key in feedback_index]
    if not matches:
        return 0.0
    return numeric_feature(max(matches, key=abs))


def candidate_keys(candidate: dict[str, Any]) -> list[str]:
    keys = []
    for field in ("id", "candidate_id", "source_id", "source_chunk_id", "doc_id", "path", "relative_path", "title", "url"):
        value = candidate.get(field) if isinstance(candidate, dict) else None
        if value is None:
            continue
        text = str(value).strip()
        if text:
            keys.append(text)
            keys.append(f"{field}:{text}")
    if not keys:
        keys.append("hash:" + stable_candidate_hash(candidate))
    return list(dict.fromkeys(keys))


def stable_candidate_hash(candidate: dict[str, Any]) -> str:
    payload = json.dumps(json_safe(candidate), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def logistic_loss_gradient(margin: float) -> float:
    if margin >= 30:
        return 0.0
    if margin <= -30:
        return 1.0
    return 1.0 / (1.0 + math.exp(margin))


def evaluate_examples(weights: dict[str, float], examples: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    correct = 0
    for example in examples:
        winner_score = dot(weights, example["winner_features"])
        loser_score = dot(weights, example["loser_features"])
        is_correct = winner_score > loser_score
        correct += 1 if is_correct else 0
        rows.append(
            {
                "goal": example["goal"],
                "winner_score": round(winner_score, 6),
                "loser_score": round(loser_score, 6),
                "correct": is_correct,
            }
        )
    accuracy = round(correct / len(examples), 6) if examples else 0.0
    return {"accuracy": accuracy, "rows": rows}


def dot(weights: dict[str, float], features: dict[str, float]) -> float:
    return sum(float(weights.get(name, 0.0)) * float(features.get(name, 0.0)) for name in FEATURE_NAMES)


def render_eval_report(model: dict[str, Any], eval_result: dict[str, Any], examples: list[dict[str, Any]]) -> str:
    lines = [
        "# Mirror Ranker Eval",
        "",
        f"- Version: `{model['mirror_ranker_model_version']}`",
        f"- Examples: `{len(examples)}`",
        f"- Training accuracy: `{eval_result['accuracy']}`",
        f"- Model: `{model['model_path']}`",
        "",
        "## Weights",
        "",
    ]
    for name, weight in (model.get("weights") or {}).items():
        lines.append(f"- `{name}`: `{weight}`")
    lines.extend(["", "## Training Checks", ""])
    if not eval_result["rows"]:
        lines.append("- No pairwise examples yet; using default weights.")
    else:
        for index, row in enumerate(eval_result["rows"][:20], start=1):
            status = "pass" if row["correct"] else "fail"
            lines.append(
                f"- {index}. `{status}` winner=`{row['winner_score']}` loser=`{row['loser_score']}` goal={row['goal']}"
            )
    return "\n".join(lines) + "\n"


def explain_score(score_parts: dict[str, float]) -> list[str]:
    contributions = [
        (name, value)
        for name, value in score_parts.items()
        if name in FEATURE_NAMES and value != 0.0
    ]
    contributions.sort(key=lambda item: abs(item[1]), reverse=True)
    if not contributions:
        return ["no non-zero ranker signals; missing features defaulted to 0"]
    return [f"{name} {'+' if value > 0 else ''}{value}" for name, value in contributions[:3]]


def apply_exploration_slot(ranked: list[dict[str, Any]], *, exploration_slots: int) -> list[dict[str, Any]]:
    if exploration_slots <= 0 or len(ranked) <= 1:
        return ranked
    ranked = [dict(candidate) for candidate in ranked]
    for candidate in ranked:
        candidate["exploration"] = False
    pool = ranked[1:]
    exploration_candidate = min(pool, key=lambda candidate: (exposure_count(candidate), candidate["score"], candidate["ranker_candidate_index"]))
    ranked = [candidate for candidate in ranked if candidate is not exploration_candidate]
    exploration_candidate["exploration"] = True
    insert_at = len(ranked) if len(ranked) <= 2 else len(ranked) - 1
    ranked.insert(insert_at, exploration_candidate)
    return ranked


def exposure_count(candidate: dict[str, Any]) -> float:
    for key in ("exposure_count", "impressions", "shown_count", "view_count"):
        if is_number_like(candidate.get(key)):
            return float(candidate[key])
    return 0.0


def ranking_key(candidate: dict[str, Any]) -> tuple[float, int]:
    return (-float(candidate["score"]), int(candidate["ranker_candidate_index"]))


def clamp(value: float, max_abs: float) -> float:
    return max(-max_abs, min(max_abs, value))


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
