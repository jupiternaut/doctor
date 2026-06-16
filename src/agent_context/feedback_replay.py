from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .feedback_model import load_feedback_model
from .io import ensure_dir, read_jsonl, write_text
from .resolver import (
    DEFAULT_RESOLVE_LIMIT,
    build_resolution_plan,
    fuse_candidates,
    normalize_source_scope,
    retrieve_candidates_for_plan,
    retrieval_stats,
)


FEEDBACK_REPLAY_VERSION = "0.1"
DEFAULT_REPLAY_CASES = "replay_cases.jsonl"
DEFAULT_GENERATED_REPLAY_CASES = "replay_cases.generated.jsonl"


def run_feedback_replay(
    out_root: Path,
    *,
    cases_path: Path | None = None,
    case_goals: list[str] | None = None,
    source_scope: str = "all",
    limit: int = DEFAULT_RESOLVE_LIMIT,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    normalized_limit = max(1, int(limit))
    cases = replay_cases(
        out_root,
        cases_path=cases_path,
        case_goals=case_goals or [],
        source_scope=source_scope,
        limit=normalized_limit,
    )
    feedback_model = load_feedback_model(out_root)
    results = [
        evaluate_replay_case(out_root, case, feedback_model=feedback_model)
        for case in cases
    ]
    summary = replay_summary(results)
    created_at = datetime.now().astimezone().isoformat(timespec="seconds")
    report_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
    report_root = ensure_dir(out_root / "reports")
    json_path = report_root / f"feedback_replay_{report_id}.json"
    md_path = report_root / f"feedback_replay_{report_id}.md"
    payload = {
        "feedback_replay_version": FEEDBACK_REPLAY_VERSION,
        "created_at": created_at,
        "out_root": str(out_root),
        "cases_path": str(cases_path.expanduser().resolve()) if cases_path else None,
        "feedback_model_version": feedback_model.get("feedback_model_version"),
        "summary": summary,
        "cases": results,
    }
    write_text(json_path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(md_path, render_replay_report(payload))
    return {
        "feedback_replay_version": FEEDBACK_REPLAY_VERSION,
        "created_at": created_at,
        "cases": len(results),
        "changed_top1": summary["changed_top1"],
        "improved_expected_top1": summary["improved_expected_top1"],
        "regressed_expected_top1": summary["regressed_expected_top1"],
        "feedback_model_version": feedback_model.get("feedback_model_version"),
        "report_json_path": str(json_path),
        "report_md_path": str(md_path),
    }


def replay_cases(
    out_root: Path,
    *,
    cases_path: Path | None,
    case_goals: list[str],
    source_scope: str,
    limit: int,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if cases_path:
        records.extend(read_jsonl(cases_path.expanduser().resolve()))
    else:
        records.extend(read_jsonl(out_root / "feedback" / DEFAULT_REPLAY_CASES))
        records.extend(read_jsonl(out_root / "feedback" / DEFAULT_GENERATED_REPLAY_CASES))
    for goal in case_goals:
        records.append({"goal": goal, "source_scope": source_scope, "limit": limit})
    if not records:
        raise FileNotFoundError(
            "no replay cases found; pass --case, run feedback-replay-cases, or create "
            f"{out_root / 'feedback' / DEFAULT_REPLAY_CASES}"
        )
    return dedupe_replay_cases(
        [normalize_replay_case(record, source_scope=source_scope, limit=limit) for record in records]
    )


def normalize_replay_case(record: dict[str, Any], *, source_scope: str, limit: int) -> dict[str, Any]:
    goal = str(record.get("goal") or record.get("query") or "").strip()
    if not goal:
        raise ValueError("feedback replay case requires a non-empty goal")
    max_limit = max(1, int(limit))
    case_limit = max(1, int(record.get("limit") or max_limit))
    return {
        "goal": goal,
        "source_scope": normalize_source_scope(str(record.get("source_scope") or source_scope)),
        "limit": min(case_limit, max_limit),
        "expected_source": str(record.get("expected_source") or record.get("expected_path") or "").strip(),
        "notes": str(record.get("notes") or "").strip(),
    }


def dedupe_replay_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped = []
    seen: set[tuple[str, str, str]] = set()
    for case in cases:
        key = (case["goal"], case["source_scope"], case.get("expected_source") or "")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(case)
    return deduped


def evaluate_replay_case(
    out_root: Path,
    case: dict[str, Any],
    *,
    feedback_model: dict[str, Any],
) -> dict[str, Any]:
    plan = build_resolution_plan(
        out_root=out_root,
        goal=case["goal"],
        limit=case["limit"],
        source_scope=case["source_scope"],
    )
    candidates = retrieve_candidates_for_plan(out_root, plan)
    baseline_sources = fuse_candidates(candidates, case["limit"], feedback_model={})
    feedback_sources = fuse_candidates(
        candidates,
        case["limit"],
        feedback_model=feedback_model,
        query_family=plan.get("query_family"),
    )
    baseline_top = source_summary(baseline_sources[0]) if baseline_sources else None
    feedback_top = source_summary(feedback_sources[0]) if feedback_sources else None
    expected_source = case.get("expected_source") or ""
    return {
        "goal": case["goal"],
        "query_family": plan.get("query_family"),
        "source_scope": case["source_scope"],
        "limit": case["limit"],
        "expected_source": expected_source,
        "notes": case.get("notes") or "",
        "candidate_stats": retrieval_stats(candidates, feedback_sources),
        "baseline": {
            "top": baseline_top,
            "top_sources": [source_summary(source) for source in baseline_sources[: case["limit"]]],
            "expected_rank": expected_rank(baseline_sources, expected_source),
        },
        "with_feedback": {
            "top": feedback_top,
            "top_sources": [source_summary(source) for source in feedback_sources[: case["limit"]]],
            "expected_rank": expected_rank(feedback_sources, expected_source),
        },
        "delta": replay_delta(baseline_sources, feedback_sources, expected_source),
    }


def source_summary(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": source.get("path"),
        "relative_path": source.get("relative_path"),
        "source_id": source.get("source_id"),
        "source_chunk_id": source.get("source_chunk_id"),
        "source_group": source.get("source_group"),
        "project_name": source.get("project_name"),
        "score": source.get("score"),
        "resolver_score_parts": source.get("resolver_score_parts") or {},
        "retrieval_channels": source.get("retrieval_channels") or [],
    }


def replay_delta(
    baseline_sources: list[dict[str, Any]],
    feedback_sources: list[dict[str, Any]],
    expected_source: str,
) -> dict[str, Any]:
    baseline_top_key = source_identity(baseline_sources[0]) if baseline_sources else ""
    feedback_top_key = source_identity(feedback_sources[0]) if feedback_sources else ""
    before_rank = expected_rank(baseline_sources, expected_source)
    after_rank = expected_rank(feedback_sources, expected_source)
    return {
        "top1_changed": bool(baseline_top_key and feedback_top_key and baseline_top_key != feedback_top_key),
        "baseline_top_key": baseline_top_key,
        "feedback_top_key": feedback_top_key,
        "expected_rank_before": before_rank,
        "expected_rank_after": after_rank,
        "expected_top1_improved": before_rank != 1 and after_rank == 1,
        "expected_top1_regressed": before_rank == 1 and after_rank not in {0, 1},
    }


def expected_rank(sources: list[dict[str, Any]], expected_source: str) -> int:
    if not expected_source:
        return 0
    needle = expected_source.lower()
    for index, source in enumerate(sources, start=1):
        values = [
            str(source.get("path") or ""),
            str(source.get("relative_path") or ""),
            str(source.get("source_id") or ""),
            str(source.get("source_chunk_id") or ""),
            str(source.get("project_name") or ""),
        ]
        lowered_values = [value.lower() for value in values if value]
        if any(needle == value or needle in value for value in lowered_values):
            return index
    return 0


def source_identity(source: dict[str, Any]) -> str:
    return str(source.get("source_chunk_id") or source.get("source_id") or source.get("path") or "")


def replay_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "cases": len(results),
        "changed_top1": sum(1 for result in results if result["delta"]["top1_changed"]),
        "improved_expected_top1": sum(1 for result in results if result["delta"]["expected_top1_improved"]),
        "regressed_expected_top1": sum(1 for result in results if result["delta"]["expected_top1_regressed"]),
    }


def render_replay_report(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Feedback Replay Report",
        "",
        f"- Created at: `{payload['created_at']}`",
        f"- Out root: `{payload['out_root']}`",
        f"- Feedback model: `{payload.get('feedback_model_version')}`",
        f"- Cases: `{summary['cases']}`",
        f"- Changed top1: `{summary['changed_top1']}`",
        f"- Improved expected top1: `{summary['improved_expected_top1']}`",
        f"- Regressed expected top1: `{summary['regressed_expected_top1']}`",
        "",
        "| Case | Query family | Expected | Baseline top | Feedback top | Expected rank |",
        "| ---: | --- | --- | --- | --- | --- |",
    ]
    for index, result in enumerate(payload["cases"], start=1):
        baseline_top = result["baseline"]["top"] or {}
        feedback_top = result["with_feedback"]["top"] or {}
        expected_rank_value = f"{result['delta']['expected_rank_before']} -> {result['delta']['expected_rank_after']}"
        lines.append(
            "| "
            f"{index} | "
            f"`{escape_table_value(result.get('query_family') or '')}` | "
            f"`{escape_table_value(result.get('expected_source') or '')}` | "
            f"`{escape_table_value(baseline_top.get('path') or baseline_top.get('source_id') or '')}` | "
            f"`{escape_table_value(feedback_top.get('path') or feedback_top.get('source_id') or '')}` | "
            f"`{expected_rank_value}` |"
        )
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- Baseline uses the same resolver retrieval path with an empty feedback model.")
    lines.append("- With-feedback uses the current `feedback/model.json` and the case query family.")
    lines.append("- A changed top1 is not automatically good; expected-rank fields are the stronger signal when an expected source is provided.")
    lines.append("")
    return "\n".join(lines)


def escape_table_value(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")
