from __future__ import annotations

import json
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .embedding_benchmark import load_benchmark_script
from .feedback_model import query_family_for_text
from .io import ensure_dir, read_jsonl, write_text
from .retrieval_backends import FASTEMBED_BACKEND_ID, HASH_VECTOR_BACKEND_ID, RetrievalConfig
from .route_selector import write_route_selector_model


RETRIEVAL_EVAL_VERSION = "0.1"
DEFAULT_RETRIEVAL_EVAL_CASES = "retrieval_eval_cases.jsonl"
DEFAULT_CURATED_RETRIEVAL_EVAL_CASES = "retrieval_eval_cases.curated.jsonl"
BACKEND_ORDER = ("hash-vector-lite", "fastembed-rerank", "semantic-fusion")


def run_retrieval_eval(
    out_root: Path,
    *,
    cases_path: Path | None = None,
    inline_cases: list[str] | None = None,
    source: str = "projects",
    limit: int = 8,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    normalized_limit = max(1, int(limit))
    effective_cases_path = (
        cases_path.expanduser().resolve()
        if cases_path
        else default_eval_cases_path(out_root)
    )
    cases = load_eval_cases(
        out_root,
        cases_path=cases_path,
        inline_cases=inline_cases or [],
        default_source=source,
    )
    module = load_benchmark_script()
    results = []
    groups = group_cases_by_source(cases)
    temp_roots: list[str] = []
    started_at = datetime.now().astimezone()

    for source_name, source_cases in groups.items():
        config = module.SOURCE_CONFIGS.get(source_name)
        if config is None:
            supported = ", ".join(sorted(module.SOURCE_CONFIGS))
            raise ValueError(f"unsupported eval source: {source_name}; expected one of: {supported}")
        results.extend(
            evaluate_source_cases(
                module,
                out_root,
                config,
                source_cases,
                limit=normalized_limit,
                temp_roots=temp_roots,
            )
        )

    finished_at = datetime.now().astimezone()
    summary = summarize_eval(results)
    payload = {
        "retrieval_eval_version": RETRIEVAL_EVAL_VERSION,
        "created_at": finished_at.isoformat(timespec="seconds"),
        "out_root": str(out_root),
        "cases_path": str(effective_cases_path),
        "case_count": len(results),
        "limit": normalized_limit,
        "summary": summary,
        "cases": results,
        "temp_roots": temp_roots,
        "policy": "Builds temporary hash indexes under /tmp; only writes eval reports under --out.",
    }
    report_id = finished_at.strftime("%Y%m%d%H%M%S%f")
    reports = ensure_dir(out_root / "reports")
    json_path = reports / f"retrieval_eval_{report_id}.json"
    md_path = reports / f"retrieval_eval_{report_id}.md"
    write_text(json_path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(md_path, render_eval_report(payload))
    route_selector_model = write_route_selector_model(out_root)
    return {
        "retrieval_eval_version": RETRIEVAL_EVAL_VERSION,
        "status": "ok",
        "created_at": payload["created_at"],
        "cases": len(results),
        "limit": normalized_limit,
        "summary": summary,
        "report_json_path": str(json_path),
        "report_md_path": str(md_path),
        "route_selector_model_path": route_selector_model.get("model_path"),
        "elapsed_ms": int((finished_at - started_at).total_seconds() * 1000),
    }


def load_eval_cases(
    out_root: Path,
    *,
    cases_path: Path | None,
    inline_cases: list[str],
    default_source: str,
) -> list[dict[str, Any]]:
    records = []
    if cases_path:
        records.extend(read_jsonl(cases_path.expanduser().resolve()))
    else:
        records.extend(read_jsonl(default_eval_cases_path(out_root)))
    records.extend(parse_inline_case(raw) for raw in inline_cases)
    if not records:
        raise FileNotFoundError(
            "no retrieval eval cases found; pass --case or create "
            f"{out_root / 'feedback' / DEFAULT_CURATED_RETRIEVAL_EVAL_CASES} "
            f"or {out_root / 'feedback' / DEFAULT_RETRIEVAL_EVAL_CASES}"
        )
    return [normalize_eval_case(record, default_source=default_source) for record in records]


def default_eval_cases_path(out_root: Path) -> Path:
    curated = out_root / "feedback" / DEFAULT_CURATED_RETRIEVAL_EVAL_CASES
    if curated.exists() and curated.stat().st_size > 0:
        return curated
    return out_root / "feedback" / DEFAULT_RETRIEVAL_EVAL_CASES


def parse_inline_case(raw: str) -> dict[str, Any]:
    parts = [part.strip() for part in raw.split("=>")]
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise ValueError('inline retrieval eval case must look like "query => expected source"')
    return {"query": parts[0], "expected_sources": [part for part in parts[1:] if part]}


def normalize_eval_case(record: dict[str, Any], *, default_source: str) -> dict[str, Any]:
    query = str(record.get("query") or record.get("goal") or "").strip()
    if not query:
        raise ValueError("retrieval eval case requires query or goal")
    expected = (
        record.get("expected_sources")
        or record.get("expected_source")
        or record.get("expected_path")
        or record.get("expected")
        or []
    )
    if isinstance(expected, str):
        expected_sources = [expected.strip()] if expected.strip() else []
    else:
        expected_sources = [str(value).strip() for value in expected if str(value).strip()]
    return {
        "query": query,
        "source": str(record.get("source") or default_source),
        "expected_sources": expected_sources,
        "notes": str(record.get("notes") or "").strip(),
    }


def group_cases_by_source(cases: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        groups.setdefault(case["source"], []).append(case)
    return groups


def evaluate_source_cases(
    module: Any,
    out_root: Path,
    config: Any,
    cases: list[dict[str, Any]],
    *,
    limit: int,
    temp_roots: list[str],
) -> list[dict[str, Any]]:
    module.ensure_required_manifests(out_root, config)
    temp_parent = Path(tempfile.mkdtemp(prefix=f"agent-context-{config.name}-retrieval-eval-", dir="/tmp"))
    temp_out_root = temp_parent / "out"
    temp_roots.append(f"{temp_out_root} (removed)")
    try:
        module.copy_manifests(out_root, temp_out_root, config)
        semantic_index_copied = module.copy_semantic_index(out_root, temp_out_root)
        dedupe_stats = module.dedupe_temp_manifests(temp_out_root, config)
        hash_build = config.build_hash_index(temp_out_root)
        return [
            evaluate_case(
                module,
                temp_out_root,
                config,
                case,
                limit=limit,
                semantic_index_copied=semantic_index_copied,
                dedupe_stats=dedupe_stats,
                hash_build=hash_build,
            )
            for case in cases
        ]
    finally:
        shutil.rmtree(temp_parent, ignore_errors=True)


def evaluate_case(
    module: Any,
    temp_out_root: Path,
    config: Any,
    case: dict[str, Any],
    *,
    limit: int,
    semantic_index_copied: bool,
    dedupe_stats: dict[str, tuple[int, int]],
    hash_build: dict[str, Any],
) -> dict[str, Any]:
    query = case["query"]
    started = time.perf_counter()
    runs = {
        HASH_VECTOR_BACKEND_ID: module.run_backend_search(
            HASH_VECTOR_BACKEND_ID,
            temp_out_root,
            query,
            limit,
            config,
            RetrievalConfig(embedding_backend=HASH_VECTOR_BACKEND_ID),
        ),
        "fastembed-rerank": module.run_backend_search(
            "fastembed-rerank",
            temp_out_root,
            query,
            limit,
            config,
            RetrievalConfig(embedding_backend=HASH_VECTOR_BACKEND_ID, rerank_backend=FASTEMBED_BACKEND_ID),
        ),
        "semantic-fusion": module.run_semantic_fusion_search(
            temp_out_root,
            query,
            limit,
            config,
            semantic_index_copied=semantic_index_copied,
        ),
    }
    backends = {
        backend: evaluate_backend_run(run, case["expected_sources"])
        for backend, run in runs.items()
    }
    winner = best_backend(backends)
    return {
        "query": query,
        "query_family": query_family_for_text(query),
        "source": config.name,
        "expected_sources": case["expected_sources"],
        "notes": case.get("notes") or "",
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "manifest_records_used": {name: kept for name, (_original, kept) in dedupe_stats.items()},
        "hash_build": {key: hash_build.get(key) for key in ("documents", "chunks", "failures", "embedding_backend")},
        "backends": backends,
        "winner_backend": winner,
    }


def evaluate_backend_run(run: Any, expected_sources: list[str]) -> dict[str, Any]:
    sources = list(getattr(run, "sources", []) or [])
    rank = expected_rank(sources, expected_sources)
    return {
        "status": getattr(run, "status", ""),
        "message": getattr(run, "message", ""),
        "elapsed_ms": getattr(run, "elapsed_ms", None),
        "sources": len(sources),
        "expected_rank": rank,
        "top1_hit": rank == 1,
        "recall_at_k": bool(rank),
        "reciprocal_rank": round((1.0 / rank) if rank else 0.0, 6),
        "top_source": source_summary(sources[0]) if sources else None,
        "matched_source": source_summary(sources[rank - 1]) if rank else None,
    }


def expected_rank(sources: list[dict[str, Any]], expected_sources: list[str]) -> int:
    if not expected_sources:
        return 0
    for index, source in enumerate(sources, start=1):
        if source_matches_expected(source, expected_sources):
            return index
    return 0


def source_matches_expected(source: dict[str, Any], expected_sources: list[str]) -> bool:
    values = [
        str(source.get("path") or ""),
        str(source.get("relative_path") or ""),
        str(source.get("source_id") or ""),
        str(source.get("source_chunk_id") or ""),
        str(source.get("chunk_id") or ""),
        str(source.get("doc_id") or ""),
        str(source.get("project_name") or ""),
    ]
    lowered_values = [value.lower() for value in values if value]
    for expected in expected_sources:
        needle = expected.lower()
        if any(needle == value or needle in value for value in lowered_values):
            return True
    return False


def source_summary(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "score": source.get("score"),
        "path": source.get("path"),
        "relative_path": source.get("relative_path"),
        "source_id": source.get("source_id"),
        "source_chunk_id": source.get("source_chunk_id"),
        "retrieval_channels": source.get("retrieval_channels") or [],
        "score_parts": source.get("score_parts") or source.get("resolver_score_parts") or {},
    }


def best_backend(backends: dict[str, dict[str, Any]]) -> str:
    ranked = sorted(
        backends.items(),
        key=lambda item: (
            item[1]["expected_rank"] == 0,
            item[1]["expected_rank"] or 10**9,
            BACKEND_ORDER.index(item[0]) if item[0] in BACKEND_ORDER else len(BACKEND_ORDER),
        ),
    )
    if not ranked or ranked[0][1]["expected_rank"] == 0:
        return ""
    return ranked[0][0]


def summarize_eval(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {}
    for backend in BACKEND_ORDER:
        backend_results = [case["backends"].get(backend) for case in results if backend in case["backends"]]
        expected_cases = [case for case in backend_results if case is not None]
        count = len(expected_cases)
        if not count:
            summary[backend] = {
                "cases": 0,
                "top1_hits": 0,
                "recall_at_k": 0,
                "mrr": 0.0,
                "ok_runs": 0,
                "avg_elapsed_ms": 0,
            }
            continue
        summary[backend] = {
            "cases": count,
            "top1_hits": sum(1 for item in expected_cases if item["top1_hit"]),
            "recall_at_k": sum(1 for item in expected_cases if item["recall_at_k"]),
            "mrr": round(sum(float(item["reciprocal_rank"]) for item in expected_cases) / count, 6),
            "ok_runs": sum(1 for item in expected_cases if item["status"] == "ok"),
            "avg_elapsed_ms": int(sum(int(item["elapsed_ms"] or 0) for item in expected_cases) / count),
        }
    summary["winner_counts"] = winner_counts(results)
    return summary


def winner_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        winner = result.get("winner_backend") or "none"
        counts[winner] = counts.get(winner, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def render_eval_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Retrieval Eval Report",
        "",
        f"- Created at: `{payload['created_at']}`",
        f"- Out root: `{payload['out_root']}`",
        f"- Cases: `{payload['case_count']}`",
        f"- Limit: `{payload['limit']}`",
        f"- Policy: {payload['policy']}",
        "",
        "## Summary",
        "",
        "| Backend | Cases | Top1 hits | Recall@K | MRR | OK runs | Avg elapsed |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for backend in BACKEND_ORDER:
        item = payload["summary"].get(backend) or {}
        lines.append(
            f"| `{backend}` | {item.get('cases', 0)} | {item.get('top1_hits', 0)} | "
            f"{item.get('recall_at_k', 0)} | {item.get('mrr', 0.0)} | "
            f"{item.get('ok_runs', 0)} | {item.get('avg_elapsed_ms', 0)} ms |"
        )
    lines.extend(["", "## Cases", "", "| Case | Source | Expected | Winner | Hash rank | Rerank rank | Semantic rank |", "| ---: | --- | --- | --- | ---: | ---: | ---: |"])
    for index, case in enumerate(payload["cases"], start=1):
        lines.append(
            "| "
            f"{index} | "
            f"`{escape_table_value(case['source'])}` | "
            f"`{escape_table_value(', '.join(case['expected_sources']))}` | "
            f"`{escape_table_value(case.get('winner_backend') or '')}` | "
            f"{backend_rank(case, HASH_VECTOR_BACKEND_ID)} | "
            f"{backend_rank(case, 'fastembed-rerank')} | "
            f"{backend_rank(case, 'semantic-fusion')} |"
        )
    lines.extend(["", "## Case Detail", ""])
    for index, case in enumerate(payload["cases"], start=1):
        lines.extend(render_case_detail(index, case))
    return "\n".join(lines) + "\n"


def render_case_detail(index: int, case: dict[str, Any]) -> list[str]:
    lines = [
        f"### Case {index}",
        "",
        f"- Query: `{case['query']}`",
        f"- Expected: `{', '.join(case['expected_sources'])}`",
        f"- Winner backend: `{case.get('winner_backend') or 'none'}`",
        "",
        "| Backend | Rank | Top source | Matched source | Status |",
        "| --- | ---: | --- | --- | --- |",
    ]
    for backend in BACKEND_ORDER:
        result = case["backends"].get(backend) or {}
        top = result.get("top_source") or {}
        matched = result.get("matched_source") or {}
        lines.append(
            f"| `{backend}` | {result.get('expected_rank', 0)} | "
            f"`{escape_table_value(top.get('relative_path') or top.get('path') or '')}` | "
            f"`{escape_table_value(matched.get('relative_path') or matched.get('path') or '')}` | "
            f"`{escape_table_value(result.get('status') or '')}` |"
        )
    lines.append("")
    return lines


def backend_rank(case: dict[str, Any], backend: str) -> int:
    return int((case.get("backends") or {}).get(backend, {}).get("expected_rank") or 0)


def escape_table_value(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")
