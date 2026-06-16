from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .feedback_model import query_family_for_text
from .feedback_replay import DEFAULT_GENERATED_REPLAY_CASES, DEFAULT_REPLAY_CASES, normalize_replay_case
from .io import ensure_dir, read_jsonl, write_jsonl, write_text
from .resolver import DEFAULT_RESOLVE_LIMIT


FEEDBACK_REPLAY_CASES_VERSION = "0.1"
SOURCE_TO_SCOPE = {
    "downloads": "downloads",
    "projects": "gitProjects",
    "sessions": "codexSessions",
    "workflows": "workflowDocs",
}
SOURCE_GROUP_TO_SCOPE = {
    "downloads_documents": "downloads",
    "git_repositories": "gitProjects",
    "codex_sessions": "codexSessions",
    "agent_sessions": "agentSessions",
    "workflow_docs": "workflowDocs",
}
POSITIVE_RATINGS = {"positive", "useful", "helpful", "relevant", "up", "like", "yes", "5", "4"}


def run_feedback_replay_case_maintenance(
    out_root: Path,
    *,
    output_cases_path: Path | None = None,
    source_scope: str = "all",
    limit: int = DEFAULT_RESOLVE_LIMIT,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    output_path = (
        output_cases_path.expanduser().resolve()
        if output_cases_path
        else out_root / "feedback" / DEFAULT_GENERATED_REPLAY_CASES
    )
    created_at = datetime.now().astimezone().isoformat(timespec="seconds")
    raw_cases = collect_replay_case_candidates(
        out_root,
        source_scope=source_scope,
        limit=max(1, int(limit)),
        created_at=created_at,
    )
    cases, summary = curate_replay_cases(raw_cases, source_scope=source_scope, limit=max(1, int(limit)))
    write_jsonl(output_path, cases)

    payload = {
        "feedback_replay_cases_version": FEEDBACK_REPLAY_CASES_VERSION,
        "created_at": created_at,
        "out_root": str(out_root),
        "output_cases_path": str(output_path),
        "summary": summary,
        "policy": "Builds generated replay cases from feedback logs without editing raw feedback or manual replay cases.",
    }
    report_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
    reports = ensure_dir(out_root / "reports")
    json_path = reports / f"feedback_replay_cases_{report_id}.json"
    md_path = reports / f"feedback_replay_cases_{report_id}.md"
    write_text(json_path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(md_path, render_replay_case_report(payload))
    return {
        "feedback_replay_cases_version": FEEDBACK_REPLAY_CASES_VERSION,
        "status": "ok",
        "created_at": created_at,
        "output_cases_path": str(output_path),
        "cases": len(cases),
        "dropped_cases": summary["dropped_cases"],
        "report_json_path": str(json_path),
        "report_md_path": str(md_path),
    }


def collect_replay_case_candidates(
    out_root: Path,
    *,
    source_scope: str,
    limit: int,
    created_at: str,
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    cases.extend(cases_from_retrieval_eval(out_root, source_scope=source_scope, limit=limit, created_at=created_at))
    cases.extend(cases_from_retrieval_eval_reports(out_root, source_scope=source_scope, limit=limit, created_at=created_at))
    cases.extend(cases_from_arena_feedback(out_root, source_scope=source_scope, limit=limit, created_at=created_at))
    cases.extend(cases_from_mcp_feedback(out_root, source_scope=source_scope, limit=limit, created_at=created_at))
    cases.extend(cases_from_panel_feedback(out_root, source_scope=source_scope, limit=limit, created_at=created_at))
    cases.extend(cases_from_alternative_feedback(out_root, source_scope=source_scope, limit=limit, created_at=created_at))
    return cases


def cases_from_retrieval_eval(
    out_root: Path,
    *,
    source_scope: str,
    limit: int,
    created_at: str,
) -> list[dict[str, Any]]:
    records = []
    curated_path = out_root / "feedback" / "retrieval_eval_cases.curated.jsonl"
    raw_path = out_root / "feedback" / "retrieval_eval_cases.jsonl"
    if curated_path.exists() and curated_path.stat().st_size > 0:
        records.extend(read_jsonl(curated_path))
    else:
        records.extend(read_jsonl(raw_path))
    cases = []
    for record in records:
        goal = str(record.get("query") or record.get("goal") or "").strip()
        expected = first_expected_source(record.get("expected_sources") or record.get("expected_source"))
        if goal:
            cases.append(
                replay_case(
                    goal,
                    expected_source=expected,
                    source_scope=scope_from_eval_source(record.get("source")) or source_scope,
                    limit=limit,
                    origin="retrieval_eval_case",
                    origin_id=str(record.get("origin_id") or ""),
                    notes=str(record.get("notes") or ""),
                    created_at=created_at,
                )
            )
    return cases


def cases_from_retrieval_eval_reports(
    out_root: Path,
    *,
    source_scope: str,
    limit: int,
    created_at: str,
) -> list[dict[str, Any]]:
    cases = []
    reports = sorted(
        (
            path
            for path in (out_root / "reports").glob("retrieval_eval_*.json")
            if not path.name.startswith("retrieval_eval_cases_")
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for report in reports[:50]:
        try:
            payload = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for index, record in enumerate(payload.get("cases") or [], start=1):
            goal = str(record.get("query") or "").strip()
            expected = first_expected_source(record.get("expected_sources"))
            if goal:
                cases.append(
                    replay_case(
                        goal,
                        expected_source=expected,
                        source_scope=scope_from_eval_source(record.get("source")) or source_scope,
                        limit=limit,
                        origin="retrieval_eval_report",
                        origin_id=f"retrieval_eval_report:{report.name}:{index}",
                        notes=str(record.get("notes") or ""),
                        created_at=created_at,
                    )
                )
    return cases


def cases_from_arena_feedback(
    out_root: Path,
    *,
    source_scope: str,
    limit: int,
    created_at: str,
) -> list[dict[str, Any]]:
    cases = []
    for record in read_jsonl(out_root / "feedback" / "arena_feedback.jsonl"):
        goal = str(record.get("goal") or "").strip()
        expected = first_expected_source(record.get("winner_sources"))
        if goal:
            cases.append(
                replay_case(
                    goal,
                    expected_source=expected,
                    source_scope=scope_from_scope_text(record.get("scope")) or scope_from_candidates(record.get("candidates")) or source_scope,
                    limit=limit,
                    origin="arena_feedback",
                    origin_id=f"arena:{record.get('arena_id') or goal}:{record.get('winner') or ''}",
                    notes=str(record.get("reason") or ""),
                    created_at=created_at,
                )
            )
    return cases


def cases_from_mcp_feedback(
    out_root: Path,
    *,
    source_scope: str,
    limit: int,
    created_at: str,
) -> list[dict[str, Any]]:
    cases = []
    for record in read_jsonl(out_root / "feedback" / "mcp_feedback.jsonl"):
        goal = str(record.get("goal") or record.get("query") or "").strip()
        if not goal:
            continue
        expected = source_identity(record.get("selected_source"))
        cases.append(
            replay_case(
                goal,
                expected_source=expected,
                source_scope=scope_from_source(record.get("selected_source")) or source_scope,
                limit=limit,
                origin="mcp_feedback",
                origin_id=stable_origin_id("mcp_feedback", goal, expected),
                notes=str(record.get("reason") or ""),
                created_at=created_at,
            )
        )
    return cases


def cases_from_panel_feedback(
    out_root: Path,
    *,
    source_scope: str,
    limit: int,
    created_at: str,
) -> list[dict[str, Any]]:
    cases = []
    for record in read_jsonl(out_root / "feedback" / "panel_feedback.jsonl"):
        if not is_positive_rating(record.get("rating")):
            continue
        status = read_status(record.get("status_path"))
        goal = str(status.get("goal") or status.get("last_goal") or record.get("goal") or record.get("query") or "").strip()
        if not goal:
            continue
        expected = source_identity(record.get("selected_source") or record.get("source"))
        cases.append(
            replay_case(
                goal,
                expected_source=expected,
                source_scope=str(status.get("source_scope") or source_scope),
                limit=limit,
                origin="panel_feedback",
                origin_id=stable_origin_id("panel_feedback", goal, expected),
                notes=str(record.get("reason") or ""),
                created_at=created_at,
            )
        )
    return cases


def cases_from_alternative_feedback(
    out_root: Path,
    *,
    source_scope: str,
    limit: int,
    created_at: str,
) -> list[dict[str, Any]]:
    cases = []
    for record in read_jsonl(out_root / "feedback" / "alternative_feedback.jsonl"):
        goal = str(record.get("goal") or "").strip()
        if goal:
            cases.append(
                replay_case(
                    goal,
                    expected_source="",
                    source_scope=source_scope,
                    limit=limit,
                    origin="alternative_feedback",
                    origin_id=stable_origin_id("alternative_feedback", goal, ",".join(record.get("rejected_sources") or [])),
                    notes="replay after rejected source feedback",
                    created_at=created_at,
                )
            )
    return cases


def curate_replay_cases(
    raw_cases: list[dict[str, Any]],
    *,
    source_scope: str,
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cases = []
    seen: set[str] = set()
    summary: dict[str, Any] = {
        "raw_cases": len(raw_cases),
        "generated_cases": 0,
        "dropped_cases": 0,
        "dropped_invalid_goal": 0,
        "dropped_duplicate": 0,
        "origins": {},
    }
    for raw in raw_cases:
        try:
            normalized = normalize_replay_case(raw, source_scope=source_scope, limit=limit)
        except ValueError:
            summary["dropped_invalid_goal"] += 1
            continue
        key = replay_case_key(raw, normalized)
        if key in seen:
            summary["dropped_duplicate"] += 1
            continue
        seen.add(key)
        case = {
            **normalized,
            "origin": raw.get("origin") or "generated",
            "origin_id": raw.get("origin_id") or key,
            "query_family": raw.get("query_family") or query_family_for_text(normalized["goal"]),
            "created_at": raw.get("created_at") or "",
        }
        cases.append(case)
        origin = str(case["origin"])
        summary["origins"][origin] = int(summary["origins"].get(origin) or 0) + 1
    summary["generated_cases"] = len(cases)
    summary["dropped_cases"] = summary["dropped_invalid_goal"] + summary["dropped_duplicate"]
    return cases, summary


def replay_case(
    goal: str,
    *,
    expected_source: str,
    source_scope: str,
    limit: int,
    origin: str,
    origin_id: str,
    notes: str,
    created_at: str,
) -> dict[str, Any]:
    return {
        "goal": goal,
        "source_scope": source_scope,
        "limit": limit,
        "expected_source": expected_source,
        "notes": notes,
        "origin": origin,
        "origin_id": origin_id,
        "query_family": query_family_for_text(goal),
        "created_at": created_at,
    }


def first_expected_source(value: Any) -> str:
    if isinstance(value, list):
        for item in value:
            identity = source_identity(item)
            if identity:
                return identity
        return ""
    return source_identity(value)


def source_identity(source: Any) -> str:
    if isinstance(source, dict):
        for field in ("path", "source_id", "source_chunk_id", "project_name", "selected_source"):
            value = source.get(field)
            if value:
                return str(value)
        return ""
    return str(source or "").strip()


def scope_from_eval_source(source: Any) -> str:
    return SOURCE_TO_SCOPE.get(str(source or ""), "")


def scope_from_scope_text(scope: Any) -> str:
    text = str(scope or "").lower()
    if "downloads" in text or "下载" in text:
        return "downloads"
    if ".codex" in text or ".claude" in text:
        return "codexSessions"
    return ""


def scope_from_candidates(candidates: Any) -> str:
    if not isinstance(candidates, list):
        return ""
    for candidate in candidates:
        scope = scope_from_source(candidate)
        if scope:
            return scope
    return ""


def scope_from_source(source: Any) -> str:
    if isinstance(source, dict):
        group = str(source.get("source_group") or source.get("provider") or "")
        if group in SOURCE_GROUP_TO_SCOPE:
            return SOURCE_GROUP_TO_SCOPE[group]
        path_scope = scope_from_scope_text(source.get("path"))
        if path_scope:
            return path_scope
    return scope_from_scope_text(source)


def is_positive_rating(rating: Any) -> bool:
    if rating is None:
        return True
    return str(rating).strip().lower() in POSITIVE_RATINGS


def read_status(path: Any) -> dict[str, Any]:
    if not path:
        return {}
    try:
        return json.loads(Path(str(path)).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def replay_case_key(raw: dict[str, Any], normalized: dict[str, Any]) -> str:
    origin_id = str(raw.get("origin_id") or "").strip()
    if origin_id:
        return f"origin:{origin_id}"
    payload = {
        "goal": normalized["goal"],
        "source_scope": normalized["source_scope"],
        "expected_source": normalized.get("expected_source") or "",
    }
    return "case:" + hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:24]


def stable_origin_id(origin: str, goal: str, expected: str) -> str:
    payload = f"{origin}\n{goal}\n{expected}"
    return f"{origin}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def render_replay_case_report(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Feedback Replay Case Maintenance",
        "",
        f"- Created at: `{payload['created_at']}`",
        f"- Output cases: `{payload['output_cases_path']}`",
        f"- Policy: {payload['policy']}",
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "| --- | ---: |",
    ]
    for key in ("raw_cases", "generated_cases", "dropped_cases", "dropped_invalid_goal", "dropped_duplicate"):
        lines.append(f"| `{key}` | {summary.get(key, 0)} |")
    lines.extend(["", "## Origins", "", "| Origin | Count |", "| --- | ---: |"])
    for origin, count in sorted((summary.get("origins") or {}).items()):
        lines.append(f"| `{origin}` | {count} |")
    lines.append("")
    return "\n".join(lines)
