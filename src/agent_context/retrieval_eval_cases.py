from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .feedback_model import query_family_for_text
from .io import ensure_dir, read_jsonl, write_jsonl, write_text
from .retrieval_eval import (
    DEFAULT_CURATED_RETRIEVAL_EVAL_CASES,
    DEFAULT_RETRIEVAL_EVAL_CASES,
    normalize_eval_case,
)


RETRIEVAL_EVAL_CASES_VERSION = "0.1"
SUPPORTED_EVAL_SOURCES = {"downloads", "projects"}
RUNTIME_BOOTSTRAP_EVAL_CASES = [
    {
        "query": "agent context resolver source registry query planner hot context pack",
        "expected_sources": ["src/agent_context/resolver.py"],
        "notes": "runtime bootstrap case for resolver planning, fusion, and hot context pack generation",
    },
    {
        "query": "project discovery full code index manifests symbols project sqlite",
        "expected_sources": ["src/agent_context/project_index.py"],
        "notes": "runtime bootstrap case for project discovery and project code indexing",
    },
    {
        "query": "downloads cold index sqlite fts chunks hash vector retrieval",
        "expected_sources": ["src/agent_context/cold_index.py"],
        "notes": "runtime bootstrap case for cold index and keyword retrieval",
    },
    {
        "query": "arena three candidate feedback retrieval eval cases user selection",
        "expected_sources": ["src/agent_context/arena.py"],
        "notes": "runtime bootstrap case for arena feedback and eval case generation",
    },
    {
        "query": "provider discovery git projects codex sessions workflow manifests",
        "expected_sources": ["src/agent_context/providers.py"],
        "notes": "runtime bootstrap case for provider discovery and registry manifests",
    },
]


def run_retrieval_eval_case_maintenance(
    out_root: Path,
    *,
    cases_path: Path | None = None,
    output_cases_path: Path | None = None,
    max_age_days: int = 0,
    default_source: str = "projects",
    include_runtime_bootstrap: bool = False,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    input_path = (
        cases_path.expanduser().resolve()
        if cases_path
        else out_root / "feedback" / DEFAULT_RETRIEVAL_EVAL_CASES
    )
    output_path = (
        output_cases_path.expanduser().resolve()
        if output_cases_path
        else out_root / "feedback" / DEFAULT_CURATED_RETRIEVAL_EVAL_CASES
    )
    created_at = datetime.now().astimezone()
    loaded = load_case_records(input_path)
    records = list(loaded["records"])
    bootstrap = runtime_bootstrap_case_records(out_root, curated_at=created_at.isoformat(timespec="seconds"))
    if include_runtime_bootstrap:
        records.extend(bootstrap["records"])
    curated, summary = curate_eval_cases(
        records,
        malformed_count=loaded["malformed_count"],
        max_age_days=max(0, int(max_age_days)),
        default_source=default_source,
        curated_at=created_at.isoformat(timespec="seconds"),
    )
    summary["runtime_bootstrap_enabled"] = int(include_runtime_bootstrap)
    summary["runtime_bootstrap_candidates"] = bootstrap["candidate_cases"]
    summary["runtime_bootstrap_included"] = len(bootstrap["records"]) if include_runtime_bootstrap else 0
    summary["runtime_bootstrap_skipped"] = bootstrap["skipped_cases"] if include_runtime_bootstrap else 0
    write_jsonl(output_path, curated)

    payload = {
        "retrieval_eval_cases_version": RETRIEVAL_EVAL_CASES_VERSION,
        "created_at": created_at.isoformat(timespec="seconds"),
        "out_root": str(out_root),
        "input_cases_path": str(input_path),
        "output_cases_path": str(output_path),
        "max_age_days": max(0, int(max_age_days)),
        "runtime_bootstrap": {
            "enabled": include_runtime_bootstrap,
            "candidate_cases": bootstrap["candidate_cases"],
            "included_cases": len(bootstrap["records"]) if include_runtime_bootstrap else 0,
            "skipped_cases": bootstrap["skipped_cases"] if include_runtime_bootstrap else 0,
            "skipped_expected_sources": bootstrap["skipped_expected_sources"] if include_runtime_bootstrap else [],
        },
        "summary": summary,
        "malformed_records": loaded["malformed_records"][:20],
        "policy": (
            "Raw feedback is append-only; this command writes a curated eval-case view without editing raw logs. "
            "Runtime bootstrap cases are labeled system self-test cases and are not user preference feedback."
        ),
    }
    report_id = created_at.strftime("%Y%m%d%H%M%S%f")
    reports = ensure_dir(out_root / "reports")
    json_path = reports / f"retrieval_eval_cases_{report_id}.json"
    md_path = reports / f"retrieval_eval_cases_{report_id}.md"
    write_text(json_path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(md_path, render_case_maintenance_report(payload))
    return {
        "retrieval_eval_cases_version": RETRIEVAL_EVAL_CASES_VERSION,
        "status": "ok",
        "created_at": payload["created_at"],
        "input_cases_path": str(input_path),
        "output_cases_path": str(output_path),
        "curated_cases": summary["curated_cases"],
        "dropped_cases": summary["dropped_cases"],
        "runtime_bootstrap_included": summary["runtime_bootstrap_included"],
        "report_json_path": str(json_path),
        "report_md_path": str(md_path),
    }


def load_case_records(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"records": [], "malformed_count": 0, "malformed_records": []}
    records: list[dict[str, Any]] = []
    malformed_records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                value = json.loads(stripped)
            except json.JSONDecodeError as exc:
                malformed_records.append({"line": line_number, "error": str(exc)})
                continue
            if isinstance(value, dict):
                records.append(value)
            else:
                malformed_records.append({"line": line_number, "error": "record is not a JSON object"})
    return {
        "records": records,
        "malformed_count": len(malformed_records),
        "malformed_records": malformed_records,
    }


def runtime_bootstrap_case_records(out_root: Path, *, curated_at: str) -> dict[str, Any]:
    available_paths = indexed_project_paths(out_root)
    records = []
    skipped_expected_sources = []
    for index, seed in enumerate(RUNTIME_BOOTSTRAP_EVAL_CASES, start=1):
        expected_sources = [
            expected
            for expected in seed["expected_sources"]
            if project_path_is_available(expected, available_paths)
        ]
        if not expected_sources:
            skipped_expected_sources.extend(seed["expected_sources"])
            continue
        records.append(
            {
                "query": seed["query"],
                "source": "projects",
                "expected_sources": expected_sources,
                "notes": seed["notes"],
                "origin": "runtime_bootstrap",
                "origin_id": f"runtime-bootstrap:{index}:{expected_sources[0]}",
                "created_at": curated_at,
            }
        )
    return {
        "candidate_cases": len(RUNTIME_BOOTSTRAP_EVAL_CASES),
        "records": records,
        "skipped_cases": len(RUNTIME_BOOTSTRAP_EVAL_CASES) - len(records),
        "skipped_expected_sources": skipped_expected_sources,
    }


def indexed_project_paths(out_root: Path) -> set[str]:
    paths = set()
    for record in read_jsonl(out_root / "manifests" / "project_documents.jsonl"):
        for field in ("path", "relative_path"):
            value = str(record.get(field) or "").strip()
            if value:
                paths.add(value)
                paths.add(value.lower())
    return paths


def project_path_is_available(expected_source: str, available_paths: set[str]) -> bool:
    expected = expected_source.strip()
    if not expected:
        return False
    expected_lower = expected.lower()
    for value in available_paths:
        lowered = value.lower()
        if expected_lower == lowered or lowered.endswith("/" + expected_lower):
            return True
    return False


def curate_eval_cases(
    records: list[dict[str, Any]],
    *,
    malformed_count: int,
    max_age_days: int,
    default_source: str,
    curated_at: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    curated: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    summary = {
        "raw_records": len(records) + malformed_count,
        "parsed_records": len(records),
        "curated_cases": 0,
        "dropped_cases": 0,
        "dropped_malformed": malformed_count,
        "dropped_invalid_query": 0,
        "dropped_empty_expected_sources": 0,
        "dropped_unsupported_source": 0,
        "dropped_duplicate": 0,
        "dropped_expired": 0,
    }
    now = datetime.fromisoformat(curated_at)
    for record in records:
        try:
            normalized = normalize_eval_case(record, default_source=default_source)
        except ValueError:
            summary["dropped_invalid_query"] += 1
            continue
        source = normalized["source"]
        if source not in SUPPORTED_EVAL_SOURCES:
            summary["dropped_unsupported_source"] += 1
            continue
        if not normalized["expected_sources"]:
            summary["dropped_empty_expected_sources"] += 1
            continue
        if max_age_days and is_expired(record.get("created_at"), now=now, max_age_days=max_age_days):
            summary["dropped_expired"] += 1
            continue
        key = case_dedupe_key(record, normalized)
        if key in seen_keys:
            summary["dropped_duplicate"] += 1
            continue
        seen_keys.add(key)
        curated.append(curated_case(record, normalized, case_key=key, curated_at=curated_at))

    summary["curated_cases"] = len(curated)
    summary["dropped_cases"] = (
        summary["dropped_malformed"]
        + summary["dropped_invalid_query"]
        + summary["dropped_empty_expected_sources"]
        + summary["dropped_unsupported_source"]
        + summary["dropped_duplicate"]
        + summary["dropped_expired"]
    )
    return curated, summary


def curated_case(
    record: dict[str, Any],
    normalized: dict[str, Any],
    *,
    case_key: str,
    curated_at: str,
) -> dict[str, Any]:
    case = {
        "query": normalized["query"],
        "source": normalized["source"],
        "expected_sources": normalized["expected_sources"],
        "notes": normalized.get("notes") or "",
        "query_family": record.get("query_family") or query_family_for_text(normalized["query"]),
        "curated_at": curated_at,
        "case_key": case_key,
    }
    for field in (
        "origin",
        "origin_id",
        "arena_id",
        "winner",
        "winner_route",
        "created_at",
    ):
        value = record.get(field)
        if value:
            case[field] = value
    return case


def case_dedupe_key(record: dict[str, Any], normalized: dict[str, Any]) -> str:
    origin_id = str(record.get("origin_id") or "").strip()
    if origin_id:
        return f"origin:{origin_id}"
    payload = {
        "query": normalized["query"],
        "source": normalized["source"],
        "expected_sources": sorted(normalized["expected_sources"]),
    }
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return f"case:{digest[:24]}"


def is_expired(raw_created_at: object, *, now: datetime, max_age_days: int) -> bool:
    if not raw_created_at:
        return False
    try:
        created_at = datetime.fromisoformat(str(raw_created_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if created_at.tzinfo is None:
        created_at = created_at.astimezone()
    return (now - created_at).days > max_age_days


def render_case_maintenance_report(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Retrieval Eval Case Maintenance",
        "",
        f"- Created at: `{payload['created_at']}`",
        f"- Input cases: `{payload['input_cases_path']}`",
        f"- Curated cases: `{payload['output_cases_path']}`",
        f"- Policy: {payload['policy']}",
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "| --- | ---: |",
    ]
    for key in (
        "raw_records",
        "parsed_records",
        "curated_cases",
        "dropped_cases",
        "dropped_malformed",
        "dropped_invalid_query",
        "dropped_empty_expected_sources",
        "dropped_unsupported_source",
        "dropped_duplicate",
        "dropped_expired",
        "runtime_bootstrap_enabled",
        "runtime_bootstrap_candidates",
        "runtime_bootstrap_included",
        "runtime_bootstrap_skipped",
    ):
        lines.append(f"| `{key}` | {summary.get(key, 0)} |")
    runtime_bootstrap = payload.get("runtime_bootstrap") or {}
    if runtime_bootstrap.get("enabled") and runtime_bootstrap.get("skipped_expected_sources"):
        lines.extend(["", "## Runtime Bootstrap Skips", ""])
        for expected in runtime_bootstrap["skipped_expected_sources"][:20]:
            lines.append(f"- `{expected}` was not present in `manifests/project_documents.jsonl`")
    if payload.get("malformed_records"):
        lines.extend(["", "## Malformed Records", ""])
        for record in payload["malformed_records"]:
            lines.append(f"- Line `{record['line']}`: {record['error']}")
    return "\n".join(lines) + "\n"
