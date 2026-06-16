from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .io import read_jsonl, write_text

FEEDBACK_MODEL_VERSION = "0.7"
MAX_ABS_SOURCE_BOOST = 0.25
MAX_ABS_ROUTE_BOOST = 0.15
MAX_ABS_QUERY_FAMILY_SOURCE_BOOST = 0.18
MAX_ABS_QUERY_FAMILY_ROUTE_BOOST = 0.10
MAX_ABS_ELO_SOURCE_BOOST = 0.06
MAX_ABS_QUERY_FAMILY_ELO_SOURCE_BOOST = 0.05
MAX_ABS_BT_SOURCE_BOOST = 0.07
MAX_ABS_QUERY_FAMILY_BT_SOURCE_BOOST = 0.06
MODEL_FILENAME = "model.json"
ELO_BASE_RATING = 1000.0
ELO_K_FACTOR = 24.0
BT_ITERATIONS = 80
BT_LEARNING_RATE = 0.08
ARENA_PAIRWISE_SOURCE_WIN_DELTA = 0.06
ARENA_PAIRWISE_SOURCE_LOSS_DELTA = -0.04
ARENA_PAIRWISE_ROUTE_WIN_DELTA = 0.04
ARENA_PAIRWISE_ROUTE_LOSS_DELTA = -0.02
REPLAY_EXPECTED_SOURCE_DELTA = 0.04
REPLAY_EXPECTED_QUERY_FAMILY_SOURCE_DELTA = 0.14
MAX_PAIRWISE_STATS_KEYS = 64
SOURCE_KEY_FIELDS = ("path", "relative_path", "source_id", "source_chunk_id", "doc_id")
PROJECT_CONTEXT_FIELDS = ("project_id", "project_path", "project_name", "source_group")
MODEL_SOURCE_KEY_FIELDS = (*SOURCE_KEY_FIELDS, *PROJECT_CONTEXT_FIELDS)
POSITIVE_RATINGS = {"positive", "useful", "helpful", "relevant", "up", "like", "yes"}
NEGATIVE_RATINGS = {"negative", "irrelevant", "unhelpful", "not_useful", "down", "dislike", "no"}
QUERY_FAMILY_MARKERS = (
    ("recommendation_system", ("推荐系统", "推荐", "recommendation", "recommender", "ranking", "ranker")),
    ("agent_context_runtime", ("上下文", "context", "resolver", "mcp", "agent", "热包", "冷索引")),
    ("project_code", ("项目", "代码", "project", "code", "repo", "repository", "github", "implementation", "architecture", "实现", "构建", "架构")),
    ("document_memory", ("downloads", "下载", "文档", "资料", "长期记忆", "个人助手")),
    ("session_history", ("会话", "历史", "之前", "聊过", "codex", "claude", "cursor")),
)
QUERY_FAMILY_STOP_TERMS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "哪些",
    "如何",
    "怎么",
    "一个",
    "告诉我",
    "本地",
    "所有",
}


def feedback_model_path(out_root: Path) -> Path:
    return out_root.expanduser().resolve() / "feedback" / MODEL_FILENAME


def load_feedback_model(out_root: Path) -> dict[str, Any]:
    return build_feedback_model(out_root, persist=True)


def build_feedback_model(out_root: Path, *, persist: bool = True) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    route_scores: dict[str, float] = {}
    source_scores: dict[str, float] = {}
    query_family_route_scores: dict[str, dict[str, float]] = {}
    query_family_source_scores: dict[str, dict[str, float]] = {}
    pairwise_stats = empty_pairwise_stats()
    elo_ratings = empty_elo_ratings()
    bt_pairs = empty_bradley_terry_pairs()
    related_keys = related_source_keys_by_path(out_root)

    apply_arena_feedback(
        out_root,
        route_scores,
        source_scores,
        related_keys,
        pairwise_stats,
        elo_ratings,
        bt_pairs,
        query_family_route_scores,
        query_family_source_scores,
    )
    apply_mcp_feedback(out_root, source_scores, related_keys, query_family_source_scores)
    apply_panel_feedback(out_root, source_scores, related_keys, query_family_source_scores)
    apply_alternative_feedback(out_root, source_scores, related_keys, query_family_source_scores)
    replay_supervision_cases = apply_replay_supervision(
        out_root,
        source_scores,
        related_keys,
        query_family_source_scores,
    )

    model = {
        "feedback_model_version": FEEDBACK_MODEL_VERSION,
        "source_key_fields": list(MODEL_SOURCE_KEY_FIELDS),
        "score_limits": {
            "source": MAX_ABS_SOURCE_BOOST,
            "route": MAX_ABS_ROUTE_BOOST,
            "elo_source": MAX_ABS_ELO_SOURCE_BOOST,
            "query_family_elo_source": MAX_ABS_QUERY_FAMILY_ELO_SOURCE_BOOST,
            "bradley_terry_source": MAX_ABS_BT_SOURCE_BOOST,
            "query_family_bradley_terry_source": MAX_ABS_QUERY_FAMILY_BT_SOURCE_BOOST,
            "replay_expected_source": REPLAY_EXPECTED_SOURCE_DELTA,
            "replay_expected_query_family_source": REPLAY_EXPECTED_QUERY_FAMILY_SOURCE_DELTA,
        },
        "route_scores": clamp_scores(route_scores, MAX_ABS_ROUTE_BOOST),
        "source_scores": clamp_scores(source_scores, MAX_ABS_SOURCE_BOOST),
        "query_family_route_scores": clamp_nested_scores(
            query_family_route_scores,
            MAX_ABS_QUERY_FAMILY_ROUTE_BOOST,
        ),
        "query_family_source_scores": clamp_nested_scores(
            query_family_source_scores,
            MAX_ABS_QUERY_FAMILY_SOURCE_BOOST,
        ),
        "pairwise_stats": normalize_pairwise_stats(pairwise_stats),
        "pairwise_elo": normalize_elo_ratings(elo_ratings),
        "query_family_pairwise_elo": normalize_query_family_elo_ratings(elo_ratings),
        "pairwise_bradley_terry": normalize_bradley_terry_pairs(bt_pairs),
        "query_family_pairwise_bradley_terry": normalize_query_family_bradley_terry_pairs(bt_pairs),
        "related_key_sources": len(related_keys),
        "replay_supervision_cases": replay_supervision_cases,
    }
    if persist:
        write_text(feedback_model_path(out_root), json.dumps(model, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return model


def feedback_boost(model: dict[str, Any], candidate: dict[str, Any], query_family: str | None = None) -> float:
    return feedback_boost_parts(model, candidate, query_family=query_family)["total"]


def feedback_boost_parts(
    model: dict[str, Any],
    candidate: dict[str, Any],
    query_family: str | None = None,
) -> dict[str, float]:
    source_scores = model.get("source_scores") or {}
    route_scores = model.get("route_scores") or {}
    keys = candidate_source_keys(candidate)
    source_boosts = [float(source_scores.get(key, 0.0)) for key in keys]
    source_boost = max(source_boosts, key=abs) if source_boosts else 0.0
    route_boost = float(route_scores.get(str(candidate.get("source_group") or ""), 0.0))
    family_source_boost = 0.0
    family_route_boost = 0.0
    elo_source_boost = elo_source_prior(model.get("pairwise_elo") or {}, keys)
    bt_source_boost = bradley_terry_source_prior(model.get("pairwise_bradley_terry") or {}, keys)
    family_elo_source_boost = 0.0
    family_bt_source_boost = 0.0
    family = source_key_value(query_family)
    if family:
        family_source_scores = (model.get("query_family_source_scores") or {}).get(family) or {}
        family_route_scores = (model.get("query_family_route_scores") or {}).get(family) or {}
        family_keys = [key for key in keys if not is_broad_feedback_key(key)]
        family_source_boosts = [float(family_source_scores.get(key, 0.0)) for key in family_keys]
        family_source_boost = max(family_source_boosts, key=abs) if family_source_boosts else 0.0
        family_route_boost = float(family_route_scores.get(str(candidate.get("source_group") or ""), 0.0))
        family_elo_source_boost = elo_source_prior(
            ((model.get("query_family_pairwise_elo") or {}).get(family) or {}),
            family_keys,
        )
        family_bt_source_boost = bradley_terry_source_prior(
            ((model.get("query_family_pairwise_bradley_terry") or {}).get(family) or {}),
            family_keys,
        )
    total = (
        source_boost
        + route_boost
        + family_source_boost
        + family_route_boost
        + elo_source_boost
        + family_elo_source_boost
        + bt_source_boost
        + family_bt_source_boost
    )
    return {
        "source": round(source_boost, 6),
        "route": round(route_boost, 6),
        "query_family_source": round(family_source_boost, 6),
        "query_family_route": round(family_route_boost, 6),
        "pairwise_elo_source": round(elo_source_boost, 6),
        "query_family_pairwise_elo_source": round(family_elo_source_boost, 6),
        "pairwise_bradley_terry_source": round(bt_source_boost, 6),
        "query_family_pairwise_bradley_terry_source": round(family_bt_source_boost, 6),
        "total": round(total, 6),
    }


def candidate_source_keys(candidate: dict[str, Any]) -> list[str]:
    keys = []
    for field in SOURCE_KEY_FIELDS:
        value = source_key_value(candidate.get(field))
        if value:
            keys.append(value)
            keys.append(model_key(field, value))
    for field in PROJECT_CONTEXT_FIELDS:
        value = source_key_value(candidate.get(field))
        if value:
            keys.append(value)
            keys.append(model_key(field, value))
    for field in ("session_id", "thread_name"):
        value = source_key_value(candidate.get(field))
        if value:
            keys.append(value)
            keys.append(model_key(field, value))
    for field in ("workflow_id", "title"):
        value = source_key_value(candidate.get(field))
        if value:
            keys.append(value)
            keys.append(model_key(field, value))
    if candidate.get("project_id"):
        keys.append(f"project:{candidate['project_id']}")
    if candidate.get("project_path"):
        keys.append(f"project_path:{candidate['project_path']}")
    if candidate.get("project_name"):
        keys.append(f"project_name:{candidate['project_name']}")
    if candidate.get("source_group"):
        keys.append(f"group:{candidate['source_group']}")
    if candidate.get("provider") in {"project_code_index", "git_project"}:
        keys.append("group:git_repositories")
    if candidate.get("provider") in {"codex_session", "claude_session"}:
        keys.append("group:codex_sessions")
    if candidate.get("provider") == "workflow_doc":
        keys.append("group:workflow_docs")
    return list(dict.fromkeys(keys))


def is_broad_feedback_key(key: str) -> bool:
    return (
        key.startswith("group:")
        or key.startswith("source_group:")
        or key
        in {
            "downloads_documents",
            "workflow_docs",
            "git_repositories",
            "codex_sessions",
        }
    )


def apply_arena_feedback(
    out_root: Path,
    route_scores: dict[str, float],
    source_scores: dict[str, float],
    related_keys: dict[str, list[str]],
    pairwise_stats: dict[str, Any],
    elo_ratings: dict[str, Any],
    bt_pairs: dict[str, Any],
    query_family_route_scores: dict[str, dict[str, float]],
    query_family_source_scores: dict[str, dict[str, float]],
) -> None:
    for record in read_jsonl(out_root / "feedback" / "arena_feedback.jsonl"):
        query_family = record_query_family(record)
        family_route_scores = query_family_route_scores.setdefault(query_family, {}) if query_family else None
        family_source_scores = query_family_source_scores.setdefault(query_family, {}) if query_family else None
        candidates = record.get("candidates") or []
        winner_id = str(record.get("winner") or "")
        winner_candidate = selected_arena_candidate(candidates, winner_id)
        loser_candidates = [
            candidate
            for candidate in candidates
            if not is_winning_arena_candidate(candidate, winner_id)
        ]
        winner_route = str(record.get("winner_route") or "")
        if winner_route:
            route_scores[winner_route] = route_scores.get(winner_route, 0.0) + 0.05
            if family_route_scores is not None:
                family_route_scores[winner_route] = family_route_scores.get(winner_route, 0.0) + 0.05
        for candidate in candidates:
            route = str(candidate.get("route") or "")
            if not route:
                continue
            delta = 0.03 if candidate.get("selected") else -0.015
            route_scores[route] = route_scores.get(route, 0.0) + delta
            if family_route_scores is not None:
                family_route_scores[route] = family_route_scores.get(route, 0.0) + delta
        for source in record.get("winner_sources") or []:
            apply_source_delta(source_scores, source, 0.05, related_keys)
            if family_source_scores is not None:
                apply_source_delta(family_source_scores, source, 0.05, related_keys)
        apply_pairwise_arena_feedback(
            route_scores,
            source_scores,
            family_route_scores,
            family_source_scores,
            related_keys,
            pairwise_stats,
            elo_ratings,
            bt_pairs,
            query_family,
            winner_id,
            winner_candidate,
            loser_candidates,
        )


def apply_pairwise_arena_feedback(
    route_scores: dict[str, float],
    source_scores: dict[str, float],
    family_route_scores: dict[str, float] | None,
    family_source_scores: dict[str, float] | None,
    related_keys: dict[str, list[str]],
    pairwise_stats: dict[str, Any],
    elo_ratings: dict[str, Any],
    bt_pairs: dict[str, Any],
    query_family: str,
    winner_id: str,
    winner_candidate: dict[str, Any] | None,
    loser_candidates: list[dict[str, Any]],
) -> None:
    if not winner_candidate or not loser_candidates:
        return
    winner_route = str(winner_candidate.get("route") or "")
    winner_keys = arena_candidate_source_keys(winner_candidate)
    for loser in loser_candidates:
        loser_route = str(loser.get("route") or "")
        loser_keys = arena_candidate_source_keys(loser)
        pairwise_stats["comparisons"] = int(pairwise_stats.get("comparisons") or 0) + 1
        if winner_route:
            route_scores[winner_route] = route_scores.get(winner_route, 0.0) + ARENA_PAIRWISE_ROUTE_WIN_DELTA
            if family_route_scores is not None:
                family_route_scores[winner_route] = family_route_scores.get(winner_route, 0.0) + ARENA_PAIRWISE_ROUTE_WIN_DELTA
            increment_counter(pairwise_stats["route_wins"], winner_route)
        if loser_route:
            route_scores[loser_route] = route_scores.get(loser_route, 0.0) + ARENA_PAIRWISE_ROUTE_LOSS_DELTA
            if family_route_scores is not None:
                family_route_scores[loser_route] = family_route_scores.get(loser_route, 0.0) + ARENA_PAIRWISE_ROUTE_LOSS_DELTA
            increment_counter(pairwise_stats["route_losses"], loser_route)
        if winner_keys:
            apply_source_keys_delta(source_scores, winner_keys, ARENA_PAIRWISE_SOURCE_WIN_DELTA, related_keys)
            if family_source_scores is not None:
                apply_source_keys_delta(family_source_scores, winner_keys, ARENA_PAIRWISE_SOURCE_WIN_DELTA, related_keys)
            increment_many(pairwise_stats["source_wins"], winner_keys)
        if loser_keys:
            apply_source_keys_delta(source_scores, loser_keys, ARENA_PAIRWISE_SOURCE_LOSS_DELTA, related_keys)
            if family_source_scores is not None:
                apply_source_keys_delta(family_source_scores, loser_keys, ARENA_PAIRWISE_SOURCE_LOSS_DELTA, related_keys)
            increment_many(pairwise_stats["source_losses"], loser_keys)
        comparison_key = f"{winner_id}>{loser.get('candidate_id')}"
        increment_counter(pairwise_stats["candidate_matchups"], comparison_key)
        update_elo_pair(elo_ratings["route"], winner_route, loser_route)
        record_bt_pair(bt_pairs["route"], winner_route, loser_route)
        if query_family:
            update_elo_pair(
                elo_ratings["query_family_route"].setdefault(query_family, {}),
                winner_route,
                loser_route,
            )
            record_bt_pair(
                bt_pairs["query_family_route"].setdefault(query_family, {}),
                winner_route,
                loser_route,
            )
        update_source_elo_pairs(elo_ratings["source"], winner_keys, loser_keys)
        record_source_bt_pairs(bt_pairs["source"], winner_keys, loser_keys)
        if query_family:
            update_source_elo_pairs(
                elo_ratings["query_family_source"].setdefault(query_family, {}),
                winner_keys,
                loser_keys,
            )
            record_source_bt_pairs(
                bt_pairs["query_family_source"].setdefault(query_family, {}),
                winner_keys,
                loser_keys,
            )


def selected_arena_candidate(candidates: list[dict[str, Any]], winner_id: str) -> dict[str, Any] | None:
    for candidate in candidates:
        if is_winning_arena_candidate(candidate, winner_id):
            return candidate
    return None


def is_winning_arena_candidate(candidate: dict[str, Any], winner_id: str) -> bool:
    return bool(candidate.get("selected")) or str(candidate.get("candidate_id") or "") == winner_id


def arena_candidate_source_keys(candidate: dict[str, Any]) -> list[str]:
    keys = feedback_source_keys(candidate.get("source_keys"))
    if keys:
        return keys
    return feedback_source_keys(candidate.get("sources"))


def apply_mcp_feedback(
    out_root: Path,
    source_scores: dict[str, float],
    related_keys: dict[str, list[str]],
    query_family_source_scores: dict[str, dict[str, float]],
) -> None:
    for record in read_jsonl(out_root / "feedback" / "mcp_feedback.jsonl"):
        query_family = record_query_family(record)
        family_source_scores = query_family_source_scores.setdefault(query_family, {}) if query_family else None
        delta = rating_delta(record.get("rating"), default=0.06)
        keys = feedback_source_keys(record.get("selected_source"))
        keys.extend(candidate_source_keys(record))
        apply_source_keys_delta(source_scores, keys, delta, related_keys)
        if family_source_scores is not None:
            apply_source_keys_delta(family_source_scores, keys, delta, related_keys)


def apply_panel_feedback(
    out_root: Path,
    source_scores: dict[str, float],
    related_keys: dict[str, list[str]],
    query_family_source_scores: dict[str, dict[str, float]],
) -> None:
    paths = [
        out_root / "feedback" / "panel_feedback.jsonl",
        Path.home() / ".codex-session-delete" / "agent-context-feedback.jsonl",
    ]
    for path in paths:
        if not path.exists():
            continue
        for record in read_jsonl(path):
            status_path = str(record.get("status_path") or "")
            query_family = record_query_family(record) or query_family_from_status_path(status_path)
            family_source_scores = query_family_source_scores.setdefault(query_family, {}) if query_family else None
            rating = record.get("rating")
            if rating is None:
                rating = record.get("winner")
            delta = rating_delta(rating, default=0.05)
            source = record.get("selected_source") or record.get("source")
            if source:
                apply_source_delta(source_scores, source, delta, related_keys)
                if family_source_scores is not None:
                    apply_source_delta(family_source_scores, source, delta, related_keys)
            keys = candidate_source_keys(record)
            if keys:
                apply_source_keys_delta(source_scores, keys, delta, related_keys)
                if family_source_scores is not None:
                    apply_source_keys_delta(family_source_scores, keys, delta, related_keys)
            if status_path:
                apply_status_feedback(source_scores, Path(status_path), delta)
                if family_source_scores is not None:
                    apply_status_feedback(family_source_scores, Path(status_path), delta)


def apply_alternative_feedback(
    out_root: Path,
    source_scores: dict[str, float],
    related_keys: dict[str, list[str]],
    query_family_source_scores: dict[str, dict[str, float]],
) -> None:
    for record in read_jsonl(out_root / "feedback" / "alternative_feedback.jsonl"):
        query_family = record_query_family(record)
        family_source_scores = query_family_source_scores.setdefault(query_family, {}) if query_family else None
        delta = rating_delta(record.get("rating"), default=0.08)
        rejected_sources = record.get("rejected_sources") or record.get("rejected_source") or []
        keys = feedback_source_keys(rejected_sources)
        apply_source_keys_delta(source_scores, keys, delta, related_keys)
        if family_source_scores is not None:
            apply_source_keys_delta(family_source_scores, keys, delta, related_keys)


def apply_replay_supervision(
    out_root: Path,
    source_scores: dict[str, float],
    related_keys: dict[str, list[str]],
    query_family_source_scores: dict[str, dict[str, float]],
) -> int:
    cases = []
    cases.extend(read_jsonl(out_root / "feedback" / "replay_cases.jsonl"))
    cases.extend(read_jsonl(out_root / "feedback" / "replay_cases.generated.jsonl"))
    applied = 0
    seen: set[tuple[str, str]] = set()
    for record in cases:
        expected_sources = replay_expected_sources(record)
        if not expected_sources:
            continue
        query_family = record_query_family(record)
        family_source_scores = query_family_source_scores.setdefault(query_family, {}) if query_family else None
        for expected_source in expected_sources:
            key = source_key_value(expected_source)
            if not key:
                continue
            dedupe_key = (query_family, key)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            apply_source_keys_delta(
                source_scores,
                [key],
                REPLAY_EXPECTED_SOURCE_DELTA,
                None,
            )
            if family_source_scores is not None:
                apply_source_keys_delta(
                    family_source_scores,
                    [key],
                    REPLAY_EXPECTED_QUERY_FAMILY_SOURCE_DELTA,
                    None,
                )
            applied += 1
    return applied


def replay_expected_sources(record: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    for field in ("expected_source", "expected_path"):
        if record.get(field):
            values.append(record.get(field))
    expected_sources = record.get("expected_sources")
    if isinstance(expected_sources, list):
        values.extend(expected_sources)
    elif expected_sources:
        values.append(expected_sources)
    keys: list[str] = []
    for value in values:
        if isinstance(value, dict):
            keys.extend(candidate_source_keys(value))
        elif isinstance(value, (list, tuple, set)):
            keys.extend(source_key_value(item) for item in value)
        else:
            keys.append(source_key_value(value))
    return [key for key in dict.fromkeys(keys) if key]


def apply_status_feedback(source_scores: dict[str, float], status_path: Path, delta: float) -> None:
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    sources_jsonl = status.get("lastSourcesJsonl") or status.get("last_sources_jsonl")
    if sources_jsonl:
        sources = read_jsonl(Path(str(sources_jsonl)).expanduser())
        if sources:
            for source in sources:
                for key in candidate_source_keys(source):
                    apply_source_delta(source_scores, key, delta)
            return
    for key in (
        status.get("lastGeneratedPack"),
        status.get("lastSourcesJsonl"),
        status.get("lastManifestJson"),
    ):
        apply_source_delta(source_scores, key, delta)


def apply_source_delta(
    source_scores: dict[str, float],
    source: Any,
    delta: float,
    related_keys: dict[str, list[str]] | None = None,
) -> None:
    apply_source_keys_delta(source_scores, feedback_source_keys(source), delta, related_keys)


def apply_source_keys_delta(
    source_scores: dict[str, float],
    keys: list[str],
    delta: float,
    related_keys: dict[str, list[str]] | None = None,
) -> None:
    if not keys or delta == 0:
        return
    unique_keys = list(dict.fromkeys(keys))
    for key in unique_keys:
        source_scores[key] = source_scores.get(key, 0.0) + delta
    if not related_keys:
        return
    direct_key_set = set(unique_keys)
    expanded_keys = []
    for key in unique_keys:
        for related_key in related_keys.get(key, []):
            if related_key not in direct_key_set:
                expanded_keys.append(related_key)
    for related_key in list(dict.fromkeys(expanded_keys)):
        source_scores[related_key] = source_scores.get(related_key, 0.0) + delta * 0.6


def feedback_source_keys(source: Any) -> list[str]:
    if not source:
        return []
    if isinstance(source, dict):
        return candidate_source_keys(source)
    if isinstance(source, (list, tuple, set)):
        keys: list[str] = []
        for value in source:
            keys.extend(feedback_source_keys(value))
        return list(dict.fromkeys(keys))
    value = source_key_value(source)
    return [value] if value else []


def rating_delta(rating: Any, default: float) -> float:
    if rating is None or rating == "":
        return default
    if isinstance(rating, bool):
        return default if rating else -default
    if isinstance(rating, (int, float)):
        return numeric_rating_delta(float(rating), default)

    value = str(rating).strip().lower()
    if value in POSITIVE_RATINGS:
        return default
    if value in NEGATIVE_RATINGS:
        return -default
    try:
        return numeric_rating_delta(float(value), default)
    except ValueError:
        return default


def numeric_rating_delta(rating: float, default: float) -> float:
    if rating < 0:
        return max(-0.08, min(0.0, rating * default))
    if rating == 0:
        return 0.0
    return max(-0.08, min(0.08, (rating - 3.0) * 0.03))


def source_key_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def model_key(field: str, value: Any) -> str:
    return f"{field}:{source_key_value(value)}"


def record_query_family(record: dict[str, Any]) -> str:
    explicit = source_key_value(record.get("query_family"))
    if explicit:
        return explicit
    for field in ("goal", "query", "task", "user_goal"):
        family = query_family_for_text(record.get(field))
        if family:
            return family
    return ""


def query_family_from_status_path(status_path: str) -> str:
    if not status_path:
        return ""
    try:
        status = json.loads(Path(status_path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return query_family_for_text(status.get("goal") or status.get("last_goal"))


def query_family_for_text(text: Any) -> str:
    raw = source_key_value(text).lower()
    if not raw:
        return ""
    families = [
        family
        for family, markers in QUERY_FAMILY_MARKERS
        if any(marker.lower() in raw for marker in markers)
    ]
    if families:
        return "+".join(families[:3])
    terms = [
        term
        for term in re.findall(r"[a-zA-Z0-9_\-\u4e00-\u9fff]+", raw)
        if len(term) >= 2 and term not in QUERY_FAMILY_STOP_TERMS
    ]
    if not terms:
        return "general"
    return "terms:" + "-".join(terms[:3])[:80]


def related_source_keys_by_path(out_root: Path) -> dict[str, list[str]]:
    related: dict[str, list[str]] = {}
    records = []
    records.extend(read_jsonl(out_root / "manifests" / "project_documents.jsonl"))
    records.extend(read_jsonl(out_root / "manifests" / "projects.jsonl"))
    records.extend(read_jsonl(out_root / "manifests" / "sessions.jsonl"))
    records.extend(read_jsonl(out_root / "manifests" / "workflows.jsonl"))

    for record in records:
        keys = candidate_source_keys(record)
        for key in keys:
            bucket = related.setdefault(key, [])
            for related_key in keys:
                if related_key != key and related_key not in bucket:
                    bucket.append(related_key)
    return related


def empty_pairwise_stats() -> dict[str, Any]:
    return {
        "comparisons": 0,
        "route_wins": {},
        "route_losses": {},
        "source_wins": {},
        "source_losses": {},
        "candidate_matchups": {},
    }


def normalize_pairwise_stats(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "comparisons": int(stats.get("comparisons") or 0),
        "route_wins": sorted_counts(stats.get("route_wins") or {}),
        "route_losses": sorted_counts(stats.get("route_losses") or {}),
        "source_wins": sorted_counts(stats.get("source_wins") or {}, limit=MAX_PAIRWISE_STATS_KEYS),
        "source_losses": sorted_counts(stats.get("source_losses") or {}, limit=MAX_PAIRWISE_STATS_KEYS),
        "candidate_matchups": sorted_counts(stats.get("candidate_matchups") or {}),
    }


def sorted_counts(counter: dict[str, int], *, limit: int | None = None) -> dict[str, int]:
    items = sorted(counter.items(), key=lambda item: (-int(item[1]), item[0]))
    if limit is not None:
        items = items[:limit]
    return {str(key): int(value) for key, value in items if int(value) > 0}


def increment_counter(counter: dict[str, int], key: Any, amount: int = 1) -> None:
    value = source_key_value(key)
    if not value:
        return
    counter[value] = int(counter.get(value, 0)) + amount


def increment_many(counter: dict[str, int], keys: list[str]) -> None:
    for key in list(dict.fromkeys(keys))[:MAX_PAIRWISE_STATS_KEYS]:
        increment_counter(counter, key)


def empty_elo_ratings() -> dict[str, Any]:
    return {
        "route": {},
        "source": {},
        "query_family_route": {},
        "query_family_source": {},
    }


def empty_bradley_terry_pairs() -> dict[str, Any]:
    return {
        "route": {},
        "source": {},
        "query_family_route": {},
        "query_family_source": {},
    }


def update_source_elo_pairs(
    ratings: dict[str, float],
    winner_keys: list[str],
    loser_keys: list[str],
) -> None:
    winners = concrete_elo_keys(winner_keys)
    losers = concrete_elo_keys(loser_keys)
    for winner in winners:
        for loser in losers:
            update_elo_pair(ratings, winner, loser)


def concrete_elo_keys(keys: list[str]) -> list[str]:
    return [
        key
        for key in list(dict.fromkeys(source_key_value(key) for key in keys))[:MAX_PAIRWISE_STATS_KEYS]
        if key and not is_broad_feedback_key(key)
    ][:16]


def update_elo_pair(ratings: dict[str, float], winner: str, loser: str) -> None:
    winner_key = source_key_value(winner)
    loser_key = source_key_value(loser)
    if not winner_key or not loser_key or winner_key == loser_key:
        return
    winner_rating = float(ratings.get(winner_key, ELO_BASE_RATING))
    loser_rating = float(ratings.get(loser_key, ELO_BASE_RATING))
    expected_winner = 1.0 / (1.0 + 10 ** ((loser_rating - winner_rating) / 400.0))
    expected_loser = 1.0 - expected_winner
    ratings[winner_key] = winner_rating + ELO_K_FACTOR * (1.0 - expected_winner)
    ratings[loser_key] = loser_rating + ELO_K_FACTOR * (0.0 - expected_loser)


def record_source_bt_pairs(
    pairs: dict[str, int],
    winner_keys: list[str],
    loser_keys: list[str],
) -> None:
    winners = concrete_elo_keys(winner_keys)
    losers = concrete_elo_keys(loser_keys)
    for winner in winners:
        for loser in losers:
            record_bt_pair(pairs, winner, loser)


def record_bt_pair(pairs: dict[str, int], winner: str, loser: str) -> None:
    winner_key = source_key_value(winner)
    loser_key = source_key_value(loser)
    if not winner_key or not loser_key or winner_key == loser_key:
        return
    key = bradley_terry_pair_key(winner_key, loser_key)
    pairs[key] = int(pairs.get(key) or 0) + 1


def bradley_terry_pair_key(winner: str, loser: str) -> str:
    return f"{winner}\u241f{loser}"


def split_bradley_terry_pair_key(key: str) -> tuple[str, str]:
    if "\u241f" not in key:
        return "", ""
    winner, loser = key.split("\u241f", 1)
    return winner, loser


def normalize_bradley_terry_pairs(pairs: dict[str, Any]) -> dict[str, Any]:
    route_model = fit_bradley_terry(pairs.get("route") or {})
    source_model = fit_bradley_terry(pairs.get("source") or {}, limit=MAX_PAIRWISE_STATS_KEYS)
    return {
        "iterations": BT_ITERATIONS,
        "learning_rate": BT_LEARNING_RATE,
        "route": route_model,
        "source": {
            **source_model,
            "source_priors": bradley_terry_prior_scores(source_model["abilities"], MAX_ABS_BT_SOURCE_BOOST),
        },
    }


def normalize_query_family_bradley_terry_pairs(pairs: dict[str, Any]) -> dict[str, Any]:
    families = sorted(
        set((pairs.get("query_family_route") or {}))
        | set((pairs.get("query_family_source") or {}))
    )
    normalized: dict[str, Any] = {}
    for family in families:
        route_model = fit_bradley_terry((pairs.get("query_family_route") or {}).get(family) or {})
        source_model = fit_bradley_terry(
            (pairs.get("query_family_source") or {}).get(family) or {},
            limit=MAX_PAIRWISE_STATS_KEYS,
        )
        if route_model["comparisons"] <= 0 and source_model["comparisons"] <= 0:
            continue
        normalized[family] = {
            "route": route_model,
            "source": {
                **source_model,
                "source_priors": bradley_terry_prior_scores(
                    source_model["abilities"],
                    MAX_ABS_QUERY_FAMILY_BT_SOURCE_BOOST,
                ),
            },
        }
    return normalized


def fit_bradley_terry(raw_pairs: dict[str, int], *, limit: int | None = None) -> dict[str, Any]:
    pair_counts = {
        split_bradley_terry_pair_key(key): int(value)
        for key, value in raw_pairs.items()
        if int(value) > 0
    }
    pair_counts = {
        pair: count
        for pair, count in pair_counts.items()
        if pair[0] and pair[1]
    }
    item_counts: dict[str, int] = {}
    for (winner, loser), count in pair_counts.items():
        item_counts[winner] = item_counts.get(winner, 0) + count
        item_counts[loser] = item_counts.get(loser, 0) + count
    if limit is not None and len(item_counts) > limit:
        keep = {
            item
            for item, _count in sorted(item_counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
        }
        pair_counts = {
            pair: count
            for pair, count in pair_counts.items()
            if pair[0] in keep and pair[1] in keep
        }
        item_counts = {item: count for item, count in item_counts.items() if item in keep}
    abilities = {item: 0.0 for item in sorted(item_counts)}
    comparisons = sum(pair_counts.values())
    if not abilities or comparisons <= 0:
        return {"items": 0, "comparisons": 0, "abilities": {}}

    for _iteration in range(BT_ITERATIONS):
        gradients = {item: 0.0 for item in abilities}
        for (winner, loser), count in pair_counts.items():
            diff = max(-30.0, min(30.0, abilities[winner] - abilities[loser]))
            probability = 1.0 / (1.0 + pow(2.718281828459045, -diff))
            gradient = count * (1.0 - probability)
            gradients[winner] += gradient
            gradients[loser] -= gradient
        for item, gradient in gradients.items():
            abilities[item] += BT_LEARNING_RATE * gradient / max(1, item_counts[item])
        center = sum(abilities.values()) / len(abilities)
        for item in abilities:
            abilities[item] -= center

    ordered = sorted(abilities.items(), key=lambda item: (-item[1], item[0]))
    return {
        "items": len(ordered),
        "comparisons": comparisons,
        "abilities": {key: round(value, 6) for key, value in ordered if abs(value) > 0.000001},
    }


def bradley_terry_prior_scores(abilities: dict[str, float], limit: float) -> dict[str, float]:
    priors = {
        key: round(max(-limit, min(limit, limit * (float(value) / 2.0))), 6)
        for key, value in abilities.items()
    }
    return {key: value for key, value in priors.items() if abs(value) > 0.000001}


def bradley_terry_source_prior(bt_model: dict[str, Any], keys: list[str]) -> float:
    source_model = bt_model.get("source") or bt_model
    source_priors = source_model.get("source_priors") or {}
    concrete_keys = [key for key in keys if not is_broad_feedback_key(key)]
    values = [float(source_priors.get(key, 0.0)) for key in concrete_keys]
    return max(values, key=abs) if values else 0.0


def normalize_elo_ratings(ratings: dict[str, Any]) -> dict[str, Any]:
    route_ratings = normalize_rating_map(ratings.get("route") or {})
    source_ratings = normalize_rating_map(ratings.get("source") or {}, limit=MAX_PAIRWISE_STATS_KEYS)
    return {
        "base_rating": int(ELO_BASE_RATING),
        "k_factor": int(ELO_K_FACTOR),
        "route_ratings": route_ratings,
        "source_ratings": source_ratings,
        "route_priors": elo_prior_scores(route_ratings, MAX_ABS_ROUTE_BOOST),
        "source_priors": elo_prior_scores(source_ratings, MAX_ABS_ELO_SOURCE_BOOST),
    }


def normalize_query_family_elo_ratings(ratings: dict[str, Any]) -> dict[str, Any]:
    families = sorted(
        set((ratings.get("query_family_route") or {}))
        | set((ratings.get("query_family_source") or {}))
    )
    normalized: dict[str, Any] = {}
    for family in families:
        route_ratings = normalize_rating_map((ratings.get("query_family_route") or {}).get(family) or {})
        source_ratings = normalize_rating_map(
            (ratings.get("query_family_source") or {}).get(family) or {},
            limit=MAX_PAIRWISE_STATS_KEYS,
        )
        if not route_ratings and not source_ratings:
            continue
        normalized[family] = {
            "route_ratings": route_ratings,
            "source_ratings": source_ratings,
            "route_priors": elo_prior_scores(route_ratings, MAX_ABS_QUERY_FAMILY_ROUTE_BOOST),
            "source_priors": elo_prior_scores(source_ratings, MAX_ABS_QUERY_FAMILY_ELO_SOURCE_BOOST),
        }
    return normalized


def normalize_rating_map(ratings: dict[str, float], *, limit: int | None = None) -> dict[str, float]:
    items = [
        (key, round(float(value), 6))
        for key, value in ratings.items()
        if abs(float(value) - ELO_BASE_RATING) > 0.000001
    ]
    items.sort(key=lambda item: (-item[1], item[0]))
    if limit is not None:
        items = items[:limit]
    return {key: value for key, value in items}


def elo_prior_scores(ratings: dict[str, float], limit: float) -> dict[str, float]:
    priors = {
        key: round(max(-limit, min(limit, ((float(value) - ELO_BASE_RATING) / 100.0) * limit)), 6)
        for key, value in ratings.items()
    }
    return {key: value for key, value in priors.items() if abs(value) > 0.000001}


def elo_source_prior(elo_model: dict[str, Any], keys: list[str]) -> float:
    source_priors = elo_model.get("source_priors") or {}
    concrete_keys = [key for key in keys if not is_broad_feedback_key(key)]
    values = [float(source_priors.get(key, 0.0)) for key in concrete_keys]
    return max(values, key=abs) if values else 0.0


def clamp_scores(scores: dict[str, float], limit: float) -> dict[str, float]:
    return {
        key: round(max(-limit, min(limit, value)), 6)
        for key, value in sorted(scores.items())
        if abs(value) > 0.000001
    }


def clamp_nested_scores(scores: dict[str, dict[str, float]], limit: float) -> dict[str, dict[str, float]]:
    return {
        family: family_scores
        for family, family_scores in (
            (family, clamp_scores(values, limit))
            for family, values in sorted(scores.items())
        )
        if family_scores
    }


def write_feedback_model(out_root: Path) -> dict[str, Any]:
    model = build_feedback_model(out_root, persist=True)
    return {
        "feedback_model_path": str(feedback_model_path(out_root)),
        **model,
    }
