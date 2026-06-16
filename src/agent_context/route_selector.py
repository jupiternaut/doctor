from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .feedback_model import query_family_for_text
from .io import write_text


ROUTE_SELECTOR_VERSION = "0.1"
ROUTE_SELECTOR_MODEL_FILENAME = "route_selector_model.json"
DEFAULT_MAX_ROUTE_SELECTOR_REPORTS = 50
MAX_ABS_BACKEND_PRIOR = 0.08
MAX_ABS_SOURCE_BACKEND_PRIOR = 0.08
MAX_ABS_QUERY_FAMILY_BACKEND_PRIOR = 0.08
MAX_ABS_ROUTE_SELECTOR_TOTAL = 0.12
BACKEND_ORDER = ("hash-vector-lite", "fastembed-rerank", "semantic-fusion")
BACKEND_CHANNELS = {
    "hash-vector-lite": {"chunk", "metadata_only", "project_code_index", "session_index", "project_code", "session_chunk"},
    "fastembed-rerank": {"chunk", "metadata_only", "project_code_index", "session_index", "project_code", "session_chunk"},
    "semantic-fusion": {"semantic_index"},
}
SOURCE_GROUP_TO_EVAL_SOURCE = {
    "downloads_documents": "downloads",
    "git_repositories": "projects",
    "codex_sessions": "sessions",
}


def load_route_selector_model(out_root: Path, *, max_reports: int = DEFAULT_MAX_ROUTE_SELECTOR_REPORTS) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    reports = latest_retrieval_eval_reports(out_root, max_reports=max_reports)
    path = route_selector_model_path(out_root)
    persisted = read_persisted_route_selector_model(path)
    if persisted and route_selector_model_is_fresh(path, reports):
        return persisted
    return build_route_selector_model(out_root, reports=reports, persist=True)


def write_route_selector_model(out_root: Path, *, max_reports: int = DEFAULT_MAX_ROUTE_SELECTOR_REPORTS) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    return build_route_selector_model(
        out_root,
        reports=latest_retrieval_eval_reports(out_root, max_reports=max_reports),
        persist=True,
    )


def route_selector_model_path(out_root: Path) -> Path:
    return out_root.expanduser().resolve() / "feedback" / ROUTE_SELECTOR_MODEL_FILENAME


def build_route_selector_model(
    out_root: Path,
    *,
    reports: list[Path] | None = None,
    persist: bool = False,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    reports = reports if reports is not None else latest_retrieval_eval_reports(out_root, max_reports=DEFAULT_MAX_ROUTE_SELECTOR_REPORTS)
    global_stats: dict[str, dict[str, float]] = {}
    source_stats: dict[str, dict[str, dict[str, float]]] = {}
    family_stats: dict[str, dict[str, dict[str, float]]] = {}
    cases_seen = 0

    for report in reports:
        try:
            payload = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for case in payload.get("cases") or []:
            if not case.get("expected_sources"):
                continue
            deltas = route_deltas_for_case(case)
            if not deltas:
                continue
            cases_seen += 1
            source = str(case.get("source") or "")
            query_family = str(case.get("query_family") or query_family_for_text(case.get("query")))
            for backend, delta in deltas.items():
                accumulate_backend_stat(global_stats, backend, delta)
                if source:
                    accumulate_backend_stat(source_stats.setdefault(source, {}), backend, delta)
                if query_family:
                    accumulate_backend_stat(family_stats.setdefault(query_family, {}), backend, delta)

    model = {
        "route_selector_version": ROUTE_SELECTOR_VERSION,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "model_path": str(route_selector_model_path(out_root)),
        "reports_seen": len(reports),
        "report_paths": [str(path) for path in reports],
        "cases_seen": cases_seen,
        "backend_priors": finalize_backend_stats(global_stats, MAX_ABS_BACKEND_PRIOR),
        "source_backend_priors": {
            source: finalize_backend_stats(stats, MAX_ABS_SOURCE_BACKEND_PRIOR)
            for source, stats in sorted(source_stats.items())
        },
        "query_family_backend_priors": {
            family: finalize_backend_stats(stats, MAX_ABS_QUERY_FAMILY_BACKEND_PRIOR)
            for family, stats in sorted(family_stats.items())
        },
    }
    if persist:
        write_text(route_selector_model_path(out_root), json.dumps(model, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return model


def read_persisted_route_selector_model(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        model = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if model.get("route_selector_version") != ROUTE_SELECTOR_VERSION:
        return None
    return model


def route_selector_model_is_fresh(path: Path, reports: list[Path]) -> bool:
    if not path.exists():
        return False
    try:
        model_mtime = path.stat().st_mtime
    except OSError:
        return False
    for report in reports:
        try:
            if report.stat().st_mtime > model_mtime:
                return False
        except OSError:
            return False
    return True


def latest_retrieval_eval_reports(out_root: Path, *, max_reports: int) -> list[Path]:
    reports_dir = out_root / "reports"
    if not reports_dir.exists():
        return []
    reports = sorted(
        (
            path
            for path in reports_dir.glob("retrieval_eval_*.json")
            if not path.name.startswith("retrieval_eval_cases_")
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return reports[: max(1, int(max_reports))]


def route_deltas_for_case(case: dict[str, Any]) -> dict[str, float]:
    backend_results = case.get("backends") or {}
    reciprocal_ranks = {}
    for backend in BACKEND_ORDER:
        result = backend_results.get(backend) or {}
        rank = int(result.get("expected_rank") or 0)
        reciprocal_ranks[backend] = (1.0 / rank) if rank else 0.0
    best_rr = max(reciprocal_ranks.values(), default=0.0)
    if best_rr <= 0:
        return {}

    winner_backend = str(case.get("winner_backend") or "")
    deltas = {}
    for backend, reciprocal_rank in reciprocal_ranks.items():
        result = backend_results.get(backend) or {}
        delta = (reciprocal_rank - best_rr) * 0.10
        if result.get("top1_hit"):
            delta += 0.04
        if backend == winner_backend and reciprocal_rank > 0:
            delta += 0.02
        deltas[backend] = delta
    return deltas


def accumulate_backend_stat(stats: dict[str, dict[str, float]], backend: str, delta: float) -> None:
    bucket = stats.setdefault(backend, {"sum": 0.0, "count": 0.0})
    bucket["sum"] += float(delta)
    bucket["count"] += 1.0


def finalize_backend_stats(stats: dict[str, dict[str, float]], max_abs: float) -> dict[str, float]:
    priors = {}
    for backend, values in sorted(stats.items()):
        count = max(float(values.get("count") or 0.0), 1.0)
        priors[backend] = round(clamp(float(values.get("sum") or 0.0) / count, max_abs), 6)
    return priors


def route_selector_boost_parts(
    model: dict[str, Any],
    candidate: dict[str, Any],
    *,
    query_family: str | None = None,
) -> dict[str, float]:
    if not model or int(model.get("cases_seen") or 0) <= 0:
        return empty_route_selector_parts()
    backends = matching_backends(candidate)
    if not backends:
        return empty_route_selector_parts()
    source = eval_source_for_candidate(candidate)
    family = str(query_family or "")
    global_prior = average_backend_prior(model.get("backend_priors") or {}, backends)
    source_prior = average_backend_prior((model.get("source_backend_priors") or {}).get(source) or {}, backends)
    family_prior = average_backend_prior((model.get("query_family_backend_priors") or {}).get(family) or {}, backends)
    total = clamp(global_prior + source_prior + family_prior, MAX_ABS_ROUTE_SELECTOR_TOTAL)
    return {
        "global": round(global_prior, 6),
        "source": round(source_prior, 6),
        "query_family": round(family_prior, 6),
        "total": round(total, 6),
    }


def empty_route_selector_parts() -> dict[str, float]:
    return {"global": 0.0, "source": 0.0, "query_family": 0.0, "total": 0.0}


def matching_backends(candidate: dict[str, Any]) -> list[str]:
    channels = set(retrieval_channels_for(candidate))
    matched = [
        backend
        for backend in BACKEND_ORDER
        if channels & BACKEND_CHANNELS.get(backend, set())
    ]
    return matched


def retrieval_channels_for(candidate: dict[str, Any]) -> list[str]:
    existing = candidate.get("retrieval_channels")
    if isinstance(existing, list):
        return [str(channel) for channel in existing if channel]
    channel = candidate.get("retrieval_channel") or candidate.get("provider") or candidate.get("type")
    return [str(channel)] if channel else []


def eval_source_for_candidate(candidate: dict[str, Any]) -> str:
    return SOURCE_GROUP_TO_EVAL_SOURCE.get(str(candidate.get("source_group") or ""), "")


def average_backend_prior(priors: dict[str, Any], backends: list[str]) -> float:
    values = [float(priors.get(backend) or 0.0) for backend in backends if backend in priors]
    if not values:
        return 0.0
    return sum(values) / len(values)


def clamp(value: float, max_abs: float) -> float:
    return max(-max_abs, min(max_abs, value))
