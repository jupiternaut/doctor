from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .io import ensure_dir, write_text
from .retrieval_backends import FASTEMBED_BACKEND_ID
from .semantic_index import (
    DEFAULT_SEMANTIC_BUDGET,
    DEFAULT_SEMANTIC_TEXT_CHARS,
    create_semantic_schema,
    semantic_index_path_for,
    semantic_rows_fingerprint,
    semantic_query_rows,
    run_semantic_refresh,
    semantic_index_status,
)


SEMANTIC_MAINTENANCE_VERSION = "0.1"
DEFAULT_SEMANTIC_MAINTENANCE_JOBS = 1
DEFAULT_SEMANTIC_ANN_CACHE_MAX_ENTRIES = 32
DEFAULT_SEMANTIC_ANN_CACHE_MAX_BYTES = 1_000_000_000


def run_semantic_maintenance(
    out_root: Path,
    *,
    source: str = "all",
    budget: int = DEFAULT_SEMANTIC_BUDGET,
    backend: str = FASTEMBED_BACKEND_ID,
    text_chars: int = DEFAULT_SEMANTIC_TEXT_CHARS,
    max_jobs: int = DEFAULT_SEMANTIC_MAINTENANCE_JOBS,
    min_interval_minutes: int = 0,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    started_at = datetime.now().astimezone()
    run_id = f"semantic-maintain-{started_at.strftime('%Y%m%d%H%M%S%f')}"
    normalized_budget = max(1, int(budget))
    normalized_text_chars = max(80, int(text_chars))
    normalized_max_jobs = max(1, int(max_jobs))
    normalized_min_interval = max(0, int(min_interval_minutes))
    before = semantic_index_status(out_root)

    interval = semantic_interval_gate(before, started_at, normalized_min_interval)
    if interval["skip"]:
        result = maintenance_result(
            out_root=out_root,
            run_id=run_id,
            started_at=started_at,
            source=source,
            backend=backend,
            budget=normalized_budget,
            text_chars=normalized_text_chars,
            max_jobs=normalized_max_jobs,
            min_interval_minutes=normalized_min_interval,
            status="skipped",
            stop_reason="min_interval_not_elapsed",
            jobs=[],
            before=before,
            after=before,
            interval=interval,
        )
        return write_maintenance_reports(out_root, result)

    jobs: list[dict[str, Any]] = []
    stop_reason = "max_jobs"
    for _ in range(normalized_max_jobs):
        job = run_semantic_refresh(
            out_root,
            source=source,
            budget=normalized_budget,
            backend=backend,
            text_chars=normalized_text_chars,
        )
        jobs.append(job)
        if job.get("status") == "noop":
            stop_reason = "source_exhausted"
            break
        if job.get("status") == "failed":
            stop_reason = "job_failed"
            break

    after = semantic_index_status(out_root)
    total_processed = sum(int(job.get("processed") or 0) for job in jobs)
    failed = any(job.get("status") == "failed" for job in jobs)
    if failed:
        status = "failed"
    elif total_processed:
        status = "ok"
    elif jobs and jobs[-1].get("status") == "noop":
        status = "noop"
    else:
        status = "ok"

    result = maintenance_result(
        out_root=out_root,
        run_id=run_id,
        started_at=started_at,
        source=source,
        backend=backend,
        budget=normalized_budget,
        text_chars=normalized_text_chars,
        max_jobs=normalized_max_jobs,
        min_interval_minutes=normalized_min_interval,
        status=status,
        stop_reason=stop_reason,
        jobs=jobs,
        before=before,
        after=after,
        interval=interval,
    )
    return write_maintenance_reports(out_root, result)


def run_semantic_ann_prune(
    out_root: Path,
    *,
    max_entries: int = DEFAULT_SEMANTIC_ANN_CACHE_MAX_ENTRIES,
    max_bytes: int = DEFAULT_SEMANTIC_ANN_CACHE_MAX_BYTES,
    dry_run: bool = False,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    started_at = datetime.now().astimezone()
    run_id = f"semantic-ann-prune-{started_at.strftime('%Y%m%d%H%M%S%f')}"
    cache_dir = out_root / "indexes" / "semantic_ann"
    normalized_max_entries = max(1, int(max_entries))
    normalized_max_bytes = max(0, int(max_bytes))
    active_fingerprints = active_semantic_ann_fingerprints(out_root)
    entries = semantic_ann_cache_entries(cache_dir)
    stale_paths: set[Path] = set()
    removal_reasons: dict[Path, str] = {}

    for entry in entries:
        fingerprint = str(entry.get("fingerprint") or "")
        if not fingerprint or fingerprint not in active_fingerprints:
            for path in entry["paths"]:
                stale_paths.add(path)
                removal_reasons[path] = "stale_fingerprint"

    kept_entries = [entry for entry in entries if not any(path in stale_paths for path in entry["paths"])]
    if len(kept_entries) > normalized_max_entries:
        overflow = sorted(kept_entries, key=lambda entry: (entry["mtime"], entry["stem"]))[: len(kept_entries) - normalized_max_entries]
        for entry in overflow:
            for path in entry["paths"]:
                stale_paths.add(path)
                removal_reasons[path] = "max_entries"

    kept_entries = [entry for entry in entries if not any(path in stale_paths for path in entry["paths"])]
    total_kept_bytes = sum(int(entry["size_bytes"]) for entry in kept_entries)
    if normalized_max_bytes and total_kept_bytes > normalized_max_bytes:
        for entry in sorted(kept_entries, key=lambda item: (item["mtime"], item["stem"])):
            if total_kept_bytes <= normalized_max_bytes:
                break
            for path in entry["paths"]:
                stale_paths.add(path)
                removal_reasons[path] = "max_bytes"
            total_kept_bytes -= int(entry["size_bytes"])

    removed = []
    for path in sorted(stale_paths):
        size = path.stat().st_size if path.exists() else 0
        removed.append({"path": str(path), "size_bytes": size, "reason": removal_reasons.get(path, "unknown")})
        if not dry_run and path.exists():
            path.unlink()

    result = {
        "semantic_ann_prune_version": SEMANTIC_MAINTENANCE_VERSION,
        "run_id": run_id,
        "status": "ok",
        "started_at": started_at.isoformat(),
        "out_root": str(out_root),
        "cache_dir": str(cache_dir),
        "dry_run": dry_run,
        "max_entries": normalized_max_entries,
        "max_bytes": normalized_max_bytes,
        "active_fingerprints": sorted(active_fingerprints),
        "caches_seen": len(entries),
        "files_seen": sum(len(entry["paths"]) for entry in entries),
        "files_removed": len(removed),
        "bytes_removed": sum(int(item["size_bytes"]) for item in removed),
        "removed": removed,
        "kept_entries": len([entry for entry in entries if not any(path in stale_paths for path in entry["paths"])]),
    }
    return write_ann_prune_reports(out_root, result)


def active_semantic_ann_fingerprints(out_root: Path) -> set[str]:
    db_path = semantic_index_path_for(out_root)
    if not db_path.exists():
        return set()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        create_semantic_schema(conn)
        meta = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM meta")}
        embedding_backend = meta.get("embedding_backend") or FASTEMBED_BACKEND_ID
        source_kinds = [
            str(row["source_kind"])
            for row in conn.execute("SELECT DISTINCT source_kind FROM semantic_chunks ORDER BY source_kind")
            if row["source_kind"]
        ]
        fingerprints = set()
        for subset in semantic_source_kind_subsets(source_kinds):
            rows = semantic_query_rows(conn, subset)
            if rows:
                fingerprints.add(semantic_rows_fingerprint(rows, embedding_backend, subset))
        return fingerprints
    finally:
        conn.close()


def semantic_source_kind_subsets(source_kinds: list[str]) -> list[list[str]]:
    subsets: list[list[str]] = []
    count = len(source_kinds)
    for mask in range(1, 1 << count):
        subsets.append([source_kinds[index] for index in range(count) if mask & (1 << index)])
    return subsets


def semantic_ann_cache_entries(cache_dir: Path) -> list[dict[str, Any]]:
    if not cache_dir.exists():
        return []
    stems = {path.stem for path in cache_dir.glob("hnswlib_*.*") if path.is_file()}
    entries = []
    for stem in sorted(stems):
        metadata_path = cache_dir / f"{stem}.json"
        index_path = cache_dir / f"{stem}.bin"
        paths = [path for path in (metadata_path, index_path) if path.exists()]
        if not paths:
            continue
        metadata: dict[str, Any] = {}
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                metadata = {}
        entries.append(
            {
                "stem": stem,
                "fingerprint": metadata.get("fingerprint", ""),
                "paths": paths,
                "size_bytes": sum(path.stat().st_size for path in paths if path.exists()),
                "mtime": max(path.stat().st_mtime for path in paths if path.exists()),
            }
        )
    return entries


def semantic_interval_gate(status: dict[str, Any], now: datetime, min_interval_minutes: int) -> dict[str, Any]:
    latest_job = status.get("latest_job") or {}
    latest_created_at = latest_job.get("created_at") if isinstance(latest_job, dict) else None
    latest = parse_datetime(str(latest_created_at)) if latest_created_at else None
    if latest and latest.tzinfo is None:
        latest = latest.replace(tzinfo=now.tzinfo)
    age_minutes = ((now - latest).total_seconds() / 60.0) if latest else None
    next_eligible_at = latest + timedelta(minutes=min_interval_minutes) if latest else None
    return {
        "skip": bool(latest and min_interval_minutes > 0 and age_minutes is not None and age_minutes < min_interval_minutes),
        "latest_job_created_at": latest.isoformat() if latest else "",
        "age_minutes": round(age_minutes, 3) if age_minutes is not None else None,
        "min_interval_minutes": min_interval_minutes,
        "next_eligible_at": next_eligible_at.isoformat() if next_eligible_at else "",
    }


def parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def maintenance_result(
    *,
    out_root: Path,
    run_id: str,
    started_at: datetime,
    source: str,
    backend: str,
    budget: int,
    text_chars: int,
    max_jobs: int,
    min_interval_minutes: int,
    status: str,
    stop_reason: str,
    jobs: list[dict[str, Any]],
    before: dict[str, Any],
    after: dict[str, Any],
    interval: dict[str, Any],
) -> dict[str, Any]:
    return {
        "semantic_maintenance_version": SEMANTIC_MAINTENANCE_VERSION,
        "run_id": run_id,
        "status": status,
        "stop_reason": stop_reason,
        "started_at": started_at.isoformat(),
        "out_root": str(out_root),
        "source": source,
        "backend": backend,
        "budget": budget,
        "text_chars": text_chars,
        "max_jobs": max_jobs,
        "min_interval_minutes": min_interval_minutes,
        "jobs_run": len(jobs),
        "processed": sum(int(job.get("processed") or 0) for job in jobs),
        "skipped": sum(int(job.get("skipped") or 0) for job in jobs),
        "jobs": jobs,
        "before": compact_semantic_status(before),
        "after": compact_semantic_status(after),
        "interval": interval,
    }


def compact_semantic_status(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "exists": status.get("exists", False),
        "index_path": status.get("index_path", ""),
        "chunks": status.get("chunks", 0),
        "jobs": status.get("jobs", 0),
        "latest_job": status.get("latest_job"),
        "meta": status.get("meta", {}),
    }


def write_maintenance_reports(out_root: Path, result: dict[str, Any]) -> dict[str, Any]:
    reports_dir = ensure_dir(out_root / "reports")
    json_path = reports_dir / f"{result['run_id']}.json"
    md_path = reports_dir / f"{result['run_id']}.md"
    result_with_paths = {
        **result,
        "report_json_path": str(json_path),
        "report_md_path": str(md_path),
    }
    write_text(json_path, json.dumps(result_with_paths, ensure_ascii=False, indent=2, sort_keys=True))
    write_text(md_path, render_maintenance_markdown(result_with_paths))
    return result_with_paths


def write_ann_prune_reports(out_root: Path, result: dict[str, Any]) -> dict[str, Any]:
    reports_dir = ensure_dir(out_root / "reports")
    json_path = reports_dir / f"{result['run_id']}.json"
    md_path = reports_dir / f"{result['run_id']}.md"
    result_with_paths = {
        **result,
        "report_json_path": str(json_path),
        "report_md_path": str(md_path),
    }
    write_text(json_path, json.dumps(result_with_paths, ensure_ascii=False, indent=2, sort_keys=True))
    write_text(md_path, render_ann_prune_markdown(result_with_paths))
    return result_with_paths


def render_ann_prune_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Semantic ANN Cache Prune Report",
        "",
        f"- Run: `{result['run_id']}`",
        f"- Status: `{result['status']}`",
        f"- Dry run: `{result['dry_run']}`",
        f"- Cache dir: `{result['cache_dir']}`",
        f"- Caches seen: `{result['caches_seen']}`",
        f"- Files removed: `{result['files_removed']}`",
        f"- Bytes removed: `{result['bytes_removed']}`",
        f"- Max entries: `{result['max_entries']}`",
        f"- Max bytes: `{result['max_bytes']}`",
        "",
        "## Removed Files",
        "",
        "| path | reason | bytes |",
        "| --- | --- | ---: |",
    ]
    if result.get("removed"):
        for item in result["removed"]:
            lines.append(
                "| `{}` | `{}` | {} |".format(
                    escape_table_text(str(item.get("path") or "")),
                    escape_table_text(str(item.get("reason") or "")),
                    int(item.get("size_bytes") or 0),
                )
            )
    else:
        lines.append("|  |  | 0 |")
    lines.append("")
    return "\n".join(lines)


def render_maintenance_markdown(result: dict[str, Any]) -> str:
    before = result.get("before") or {}
    after = result.get("after") or {}
    lines = [
        "# Semantic Maintenance Report",
        "",
        f"- Run: `{result['run_id']}`",
        f"- Status: `{result['status']}`",
        f"- Stop reason: `{result['stop_reason']}`",
        f"- Source: `{result['source']}`",
        f"- Backend: `{result['backend']}`",
        f"- Budget per job: `{result['budget']}`",
        f"- Jobs run: `{result['jobs_run']}` / `{result['max_jobs']}`",
        f"- Processed chunks: `{result['processed']}`",
        f"- Index: `{after.get('index_path') or before.get('index_path') or ''}`",
        f"- Chunks: `{before.get('chunks', 0)}` -> `{after.get('chunks', 0)}`",
        "",
        "## Jobs",
        "",
        "| job_id | status | processed | skipped | error |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    if result.get("jobs"):
        for job in result["jobs"]:
            lines.append(
                "| `{}` | `{}` | {} | {} | {} |".format(
                    job.get("job_id", ""),
                    job.get("status", ""),
                    job.get("processed", 0),
                    job.get("skipped", 0),
                    escape_table_text(str(job.get("error") or "")),
                )
            )
    else:
        lines.append("|  |  | 0 | 0 |  |")
    interval = result.get("interval") or {}
    if interval.get("skip"):
        lines.extend(
            [
                "",
                "## Interval Gate",
                "",
                f"- Latest job: `{interval.get('latest_job_created_at', '')}`",
                f"- Age minutes: `{interval.get('age_minutes')}`",
                f"- Next eligible at: `{interval.get('next_eligible_at', '')}`",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def escape_table_text(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
