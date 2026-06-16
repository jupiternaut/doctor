#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists() and str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_context.cold_index import build_cold_index, search_cold_index
from agent_context.io import ensure_dir, read_jsonl, write_jsonl, write_text
from agent_context.project_index import ProjectIndexPaths, build_project_sqlite, search_project_index
from agent_context.retrieval_backends import FASTEMBED_BACKEND_ID, HASH_VECTOR_BACKEND_ID, RetrievalConfig
from agent_context.resolver import apply_semantic_resolver_weight
from agent_context.semantic_index import search_semantic_index, semantic_index_path_for

FASTEMBED_RERANK_BACKEND_ID = "fastembed-rerank"
SEMANTIC_FUSION_BACKEND_ID = "semantic-fusion"

SearchFn = Callable[[Path, str, int, RetrievalConfig], dict]
BuildHashFn = Callable[[Path], dict]


@dataclass(frozen=True)
class SourceConfig:
    name: str
    index_relative_path: Path
    required_manifests: tuple[str, ...]
    optional_manifests: tuple[str, ...]
    semantic_source_kind: str
    search: SearchFn
    build_hash_index: BuildHashFn


@dataclass(frozen=True)
class BackendRun:
    backend: str
    status: str
    message: str
    index_path: str
    elapsed_ms: int | None
    index_meta: dict[str, str]
    sources: list[dict]


def search_downloads(out_root: Path, query: str, limit: int, retrieval_config: RetrievalConfig) -> dict:
    return search_cold_index(out_root, query, limit=limit, retrieval_config=retrieval_config)


def search_projects(out_root: Path, query: str, limit: int, retrieval_config: RetrievalConfig) -> dict:
    return search_project_index(out_root, query, limit=limit, retrieval_config=retrieval_config)


def build_downloads_hash_index(out_root: Path) -> dict:
    return build_cold_index(out_root, retrieval_config=RetrievalConfig(embedding_backend=HASH_VECTOR_BACKEND_ID))


def build_projects_hash_index(out_root: Path) -> dict:
    paths = ProjectIndexPaths.from_root(out_root)
    ensure_dir(paths.indexes)
    documents = read_jsonl(paths.documents_jsonl)
    chunks = read_jsonl(paths.chunks_jsonl)
    failures = read_jsonl(paths.failures_jsonl)
    if not documents and not chunks:
        raise FileNotFoundError(f"no project manifests found under {paths.manifests}")
    build_project_sqlite(
        paths,
        documents,
        chunks,
        failures,
        retrieval_config=RetrievalConfig(embedding_backend=HASH_VECTOR_BACKEND_ID),
    )
    return {
        "index_path": str(paths.sqlite),
        "documents": len(documents),
        "chunks": len(chunks),
        "failures": len(failures),
        "embedding_backend": HASH_VECTOR_BACKEND_ID,
    }


SOURCE_CONFIGS: dict[str, SourceConfig] = {
    "downloads": SourceConfig(
        name="downloads",
        index_relative_path=Path("indexes/context.sqlite"),
        required_manifests=("documents.jsonl", "chunks.jsonl"),
        optional_manifests=("failures.jsonl",),
        semantic_source_kind="downloads",
        search=search_downloads,
        build_hash_index=build_downloads_hash_index,
    ),
    "projects": SourceConfig(
        name="projects",
        index_relative_path=Path("indexes/projects.sqlite"),
        required_manifests=("project_documents.jsonl", "project_chunks.jsonl"),
        optional_manifests=("project_failures.jsonl", "symbols.jsonl"),
        semantic_source_kind="projects",
        search=search_projects,
        build_hash_index=build_projects_hash_index,
    ),
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare hash-vector-lite against query-time FastEmbed rerank.",
    )
    parser.add_argument("--out", default=".", help="Existing agent-context output root.")
    parser.add_argument(
        "--query",
        action="append",
        required=True,
        help="Retrieval query. Repeat --query to benchmark multiple queries.",
    )
    parser.add_argument("--source", choices=sorted(SOURCE_CONFIGS), default="downloads")
    parser.add_argument("--limit", type=int, default=8, help="Maximum sources returned per backend/query.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out_root = Path(args.out).expanduser().resolve()
    config = SOURCE_CONFIGS[args.source]

    report_path = run_benchmark(out_root, config, args.query, max(1, args.limit))
    print(str(report_path))
    return 0


def run_benchmark(out_root: Path, config: SourceConfig, queries: list[str], limit: int) -> Path:
    ensure_required_manifests(out_root, config)
    main_index_path = out_root / config.index_relative_path
    main_index_meta = read_sqlite_meta(main_index_path)
    temp_parent = Path(tempfile.mkdtemp(prefix=f"agent-context-{config.name}-benchmark-", dir="/tmp"))
    temp_out_root = temp_parent / "out"
    hash_build: dict
    results: dict[str, dict[str, BackendRun]] = {}
    temp_removed = False
    try:
        copy_manifests(out_root, temp_out_root, config)
        semantic_index_copied = copy_semantic_index(out_root, temp_out_root)
        dedupe_stats = dedupe_temp_manifests(temp_out_root, config)
        hash_build = config.build_hash_index(temp_out_root)
        for query in queries:
            results[query] = {
                HASH_VECTOR_BACKEND_ID: run_backend_search(
                    HASH_VECTOR_BACKEND_ID,
                    temp_out_root,
                    query,
                    limit,
                    config,
                    RetrievalConfig(embedding_backend=HASH_VECTOR_BACKEND_ID),
                )
            }
            results[query][FASTEMBED_RERANK_BACKEND_ID] = run_backend_search(
                FASTEMBED_RERANK_BACKEND_ID,
                temp_out_root,
                query,
                limit,
                config,
                RetrievalConfig(embedding_backend=HASH_VECTOR_BACKEND_ID, rerank_backend=FASTEMBED_BACKEND_ID),
            )
            results[query][SEMANTIC_FUSION_BACKEND_ID] = run_semantic_fusion_search(
                temp_out_root,
                query,
                limit,
                config,
                semantic_index_copied=semantic_index_copied,
            )
    finally:
        shutil.rmtree(temp_parent, ignore_errors=True)
        temp_removed = True

    report = render_report(
        out_root=out_root,
        config=config,
        queries=queries,
        limit=limit,
        main_index_path=main_index_path,
        main_index_meta=main_index_meta,
        hash_build=hash_build,
        dedupe_stats=dedupe_stats,
        hash_temp_root=temp_out_root,
        hash_temp_removed=temp_removed,
        results=results,
    )
    report_path = out_root / "reports" / f"embedding_backend_benchmark_{config.name}_{timestamp_id()}.md"
    write_text(report_path, report)
    return report_path


def ensure_required_manifests(out_root: Path, config: SourceConfig) -> None:
    missing = [name for name in config.required_manifests if not (out_root / "manifests" / name).exists()]
    if missing:
        missing_list = ", ".join(f"manifests/{name}" for name in missing)
        raise FileNotFoundError(f"missing required {config.name} manifest(s): {missing_list}")


def copy_manifests(out_root: Path, temp_out_root: Path, config: SourceConfig) -> None:
    manifest_root = ensure_dir(temp_out_root / "manifests")
    for name in config.required_manifests + config.optional_manifests:
        source = out_root / "manifests" / name
        if source.exists():
            shutil.copy2(source, manifest_root / name)


def copy_semantic_index(out_root: Path, temp_out_root: Path) -> bool:
    source = semantic_index_path_for(out_root)
    if not source.exists():
        return False
    target = semantic_index_path_for(temp_out_root)
    ensure_dir(target.parent)
    shutil.copy2(source, target)
    return True


def dedupe_temp_manifests(temp_out_root: Path, config: SourceConfig) -> dict[str, tuple[int, int]]:
    stats: dict[str, tuple[int, int]] = {}
    for name in config.required_manifests + config.optional_manifests:
        path = temp_out_root / "manifests" / name
        if not path.exists():
            stats[name] = (0, 0)
            continue
        records = read_jsonl(path)
        key_fn = dedupe_key_for_manifest(name)
        if key_fn is None:
            stats[name] = (len(records), len(records))
            continue
        deduped = dedupe_records(records, key_fn)
        if len(deduped) != len(records):
            write_jsonl(path, deduped)
        stats[name] = (len(records), len(deduped))
    return stats


def dedupe_records(records: list[dict], key_fn: Callable[[dict], tuple[object, ...]]) -> list[dict]:
    order: list[tuple[object, ...]] = []
    by_key: dict[tuple[object, ...], dict] = {}
    for record in records:
        key = key_fn(record)
        if key not in by_key:
            order.append(key)
        by_key[key] = record
    return [by_key[key] for key in order]


def dedupe_key_for_manifest(name: str) -> Callable[[dict], tuple[object, ...]] | None:
    if name in {"documents.jsonl", "project_documents.jsonl"}:
        return lambda record: (record.get("doc_id"), record.get("path"))
    if name in {"chunks.jsonl", "project_chunks.jsonl"}:
        return lambda record: (
            record.get("doc_id"),
            record.get("path"),
            record.get("chunk_id"),
            record.get("chunk_index"),
        )
    if name in {"failures.jsonl", "project_failures.jsonl"}:
        return lambda record: (
            record.get("path"),
            record.get("sha256"),
            record.get("stage"),
            record.get("parser"),
            record.get("error_type"),
            record.get("error"),
        )
    if name == "symbols.jsonl":
        return lambda record: (
            record.get("project_name"),
            record.get("path"),
            record.get("relative_path"),
            record.get("line"),
            record.get("symbol"),
        )
    return None


def read_sqlite_meta(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT key, value FROM meta").fetchall()
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return {}
    return {str(row["key"]): str(row["value"]) for row in rows}


def fastembed_index_status(index_path: Path, meta: dict[str, str]) -> tuple[bool, str]:
    if not index_path.exists():
        return False, f"main index not found: {index_path}"
    backend = meta.get("embedding_backend")
    if backend != FASTEMBED_BACKEND_ID:
        observed = backend or "missing"
        return False, f"main index embedding_backend is {observed}, not {FASTEMBED_BACKEND_ID}"
    return True, "main index declares embedding_backend=fastembed"


def run_backend_search(
    backend: str,
    out_root: Path,
    query: str,
    limit: int,
    config: SourceConfig,
    retrieval_config: RetrievalConfig,
) -> BackendRun:
    start = time.perf_counter()
    try:
        result = config.search(out_root, query, limit, retrieval_config)
    except Exception as exc:  # noqa: BLE001 - report tool should degrade to a report row.
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return BackendRun(
            backend=backend,
            status="error",
            message=f"{type(exc).__name__}: {exc}",
            index_path=str(out_root / config.index_relative_path),
            elapsed_ms=elapsed_ms,
            index_meta={},
            sources=[],
        )
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    return BackendRun(
        backend=backend,
        status="ok",
        message=f"{result.get('sources_included', 0)} sources returned",
        index_path=str(result.get("index_path") or out_root / config.index_relative_path),
        elapsed_ms=elapsed_ms,
        index_meta={str(key): str(value) for key, value in (result.get("index_meta") or {}).items()},
        sources=list(result.get("sources") or []),
    )


def run_semantic_fusion_search(
    out_root: Path,
    query: str,
    limit: int,
    config: SourceConfig,
    *,
    semantic_index_copied: bool,
) -> BackendRun:
    start = time.perf_counter()
    index_path = semantic_index_path_for(out_root)
    if not semantic_index_copied:
        return BackendRun(
            backend=SEMANTIC_FUSION_BACKEND_ID,
            status="skipped",
            message="semantic.sqlite not found in source out root",
            index_path=str(index_path),
            elapsed_ms=0,
            index_meta={},
            sources=[],
        )
    try:
        base = config.search(
            out_root,
            query,
            limit,
            RetrievalConfig(embedding_backend=HASH_VECTOR_BACKEND_ID),
        )
        semantic = search_semantic_index(
            out_root,
            query,
            limit=max(limit * 4, 20),
            source_kinds=[config.semantic_source_kind],
        )
        if semantic.get("skipped_reason"):
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            return BackendRun(
                backend=SEMANTIC_FUSION_BACKEND_ID,
                status="skipped",
                message=str(semantic["skipped_reason"]),
                index_path=str(index_path),
                elapsed_ms=elapsed_ms,
                index_meta={str(key): str(value) for key, value in (semantic.get("index_meta") or {}).items()},
                sources=[],
            )
        sources = fuse_benchmark_sources(base.get("sources") or [], semantic.get("sources") or [], query, limit)
    except Exception as exc:  # noqa: BLE001 - report tool should degrade to a report row.
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return BackendRun(
            backend=SEMANTIC_FUSION_BACKEND_ID,
            status="error",
            message=f"{type(exc).__name__}: {exc}",
            index_path=str(index_path),
            elapsed_ms=elapsed_ms,
            index_meta={},
            sources=[],
        )
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    return BackendRun(
        backend=SEMANTIC_FUSION_BACKEND_ID,
        status="ok",
        message=f"{len(sources)} fused sources returned",
        index_path=str(index_path),
        elapsed_ms=elapsed_ms,
        index_meta={str(key): str(value) for key, value in (semantic.get("index_meta") or {}).items()},
        sources=sources,
    )


def fuse_benchmark_sources(base_sources: list[dict], semantic_sources: list[dict], query: str, limit: int) -> list[dict]:
    merged: dict[str, dict] = {}
    weighted_semantic_sources = []
    for source in semantic_sources:
        candidate = dict(source)
        apply_semantic_resolver_weight(candidate, query)
        weighted_semantic_sources.append(candidate)
    for source in [*base_sources, *weighted_semantic_sources]:
        key = source_key(source)
        existing = merged.get(key)
        if not existing:
            candidate = dict(source)
            candidate["retrieval_channels"] = retrieval_channels_for(source)
            merged[key] = candidate
            continue
        existing["score_parts"] = {**existing.get("score_parts", {}), **source.get("score_parts", {})}
        existing["retrieval_channels"] = sorted(
            set(existing.get("retrieval_channels", [])) | set(retrieval_channels_for(source))
        )
        if float(source.get("score") or 0.0) > float(existing.get("score") or 0.0):
            for field in ("score", "snippet", "provider", "retrieval_channel"):
                if source.get(field) is not None:
                    existing[field] = source.get(field)
    ranked = sorted(
        merged.values(),
        key=lambda item: (-float(item.get("score") or 0.0), str(item.get("path") or ""), str(item.get("chunk_index") or "")),
    )
    return ranked[:limit]


def retrieval_channels_for(source: dict) -> list[str]:
    existing = source.get("retrieval_channels")
    if isinstance(existing, list):
        return [str(value) for value in existing if value]
    channel = source.get("retrieval_channel") or source.get("provider") or source.get("type")
    return [str(channel)] if channel else []


def render_report(
    *,
    out_root: Path,
    config: SourceConfig,
    queries: list[str],
    limit: int,
    main_index_path: Path,
    main_index_meta: dict[str, str],
    hash_build: dict,
    dedupe_stats: dict[str, tuple[int, int]],
    hash_temp_root: Path,
    hash_temp_removed: bool,
    results: dict[str, dict[str, BackendRun]],
) -> str:
    created_at = datetime.now().astimezone().isoformat(timespec="seconds")
    lines = [
        "# Embedding Backend Retrieval Benchmark",
        "",
        f"- Created at: `{created_at}`",
        f"- Out root: `{out_root}`",
        f"- Source: `{config.name}`",
        f"- Limit: `{limit}`",
        f"- Queries: `{len(queries)}`",
        f"- Main index: `{main_index_path}`",
        f"- Main index embedding_backend: `{main_index_meta.get('embedding_backend') or 'missing'}`",
        f"- Hash temp root: `{hash_temp_root}` ({'removed' if hash_temp_removed else 'retained'})",
        "- Main out-root writes: this Markdown report only.",
        "- Hash-vector-lite index policy: copied manifests to `/tmp` and built the hash index there.",
        "- FastEmbed rerank policy: build a temporary hash index, then rerank the candidate pool at query time.",
        "- Semantic fusion policy: copy `indexes/semantic.sqlite` to `/tmp`, exact-scan it, and fuse semantic hits with hash results.",
        "",
        "## Manifest Inputs",
        "",
        "| Manifest | Main records | Temp records used |",
        "| --- | ---: | ---: |",
    ]
    for name in config.required_manifests + config.optional_manifests:
        path = out_root / "manifests" / name
        count = len(read_jsonl(path)) if path.exists() else 0
        _original, kept = dedupe_stats.get(name, (count, count))
        suffix = "" if path.exists() else " (missing)"
        lines.append(f"| `manifests/{name}`{suffix} | {count} | {kept} |")

    lines.extend(
        [
            "",
            "## Hash Build",
            "",
            "| Field | Value |",
            "| --- | --- |",
        ]
    )
    for key in ("index_path", "documents", "chunks", "failures", "embedding_backend", "embedding", "embedding_model"):
        if key in hash_build:
            lines.append(f"| `{key}` | `{escape_table_value(hash_build[key])}` |")

    lines.extend(["", "## Main Index Meta", "", "| Key | Value |", "| --- | --- |"])
    if main_index_meta:
        for key, value in sorted(main_index_meta.items()):
            lines.append(f"| `{escape_table_value(key)}` | `{escape_table_value(value)}` |")
    else:
        lines.append("| `meta` | `missing or empty` |")

    for query in queries:
        hash_run = results[query][HASH_VECTOR_BACKEND_ID]
        rerank_run = results[query][FASTEMBED_RERANK_BACKEND_ID]
        semantic_run = results[query][SEMANTIC_FUSION_BACKEND_ID]
        lines.extend(render_query_section(query, hash_run, rerank_run, semantic_run))

    lines.append("")
    return "\n".join(lines)


def render_query_section(query: str, hash_run: BackendRun, rerank_run: BackendRun, semantic_run: BackendRun) -> list[str]:
    overlap = overlap_summary(hash_run.sources, rerank_run.sources) if rerank_run.status == "ok" else "not available"
    semantic_overlap = overlap_summary(hash_run.sources, semantic_run.sources) if semantic_run.status == "ok" else "not available"
    lines = [
        "",
        f"## Query: {query}",
        "",
        "| Backend | Status | Elapsed | Sources | Message |",
        "| --- | --- | ---: | ---: | --- |",
        render_run_summary_row(hash_run),
        render_run_summary_row(rerank_run),
        render_run_summary_row(semantic_run),
        "",
        f"- Hash vs FastEmbed rerank overlap: `{overlap}`",
        f"- Hash vs semantic-fusion overlap: `{semantic_overlap}`",
        "",
    ]
    lines.extend(render_sources_table("hash-vector-lite Top Sources", hash_run))
    lines.extend(render_sources_table("fastembed-rerank Top Sources", rerank_run))
    lines.extend(render_sources_table("semantic-fusion Top Sources", semantic_run))
    return lines


def render_run_summary_row(run: BackendRun) -> str:
    elapsed = "" if run.elapsed_ms is None else f"{run.elapsed_ms} ms"
    return (
        f"| `{run.backend}` | `{run.status}` | {elapsed} | {len(run.sources)} | "
        f"{escape_table_value(run.message)} |"
    )


def render_sources_table(title: str, run: BackendRun) -> list[str]:
    lines = [f"### {title}", ""]
    if run.status != "ok":
        lines.extend([f"- {run.status}: {run.message}", ""])
        return lines
    if not run.sources:
        lines.extend(["- No sources returned.", ""])
        return lines
    lines.extend(["| Rank | Score | Path | Chunk | Channels | Score parts |", "| ---: | ---: | --- | --- | --- | --- |"])
    for index, source in enumerate(run.sources, start=1):
        path = source.get("relative_path") or source.get("path") or ""
        chunk = source.get("chunk_id") or source.get("source_id") or ""
        parts = json.dumps(source.get("score_parts") or {}, ensure_ascii=False, sort_keys=True)
        channels = ",".join(retrieval_channels_for(source))
        lines.append(
            f"| {index} | {source.get('score', '')} | `{escape_table_value(path)}` | "
            f"`{escape_table_value(chunk)}` | `{escape_table_value(channels)}` | `{escape_table_value(parts)}` |"
        )
    lines.append("")
    return lines


def overlap_summary(left_sources: list[dict], right_sources: list[dict]) -> str:
    left = [source_key(source) for source in left_sources]
    right = [source_key(source) for source in right_sources]
    left_set = set(left)
    right_set = set(right)
    union = left_set | right_set
    if not union:
        return "0/0 shared"
    shared = left_set & right_set
    jaccard = len(shared) / len(union)
    top1 = "same" if left and right and left[0] == right[0] else "different"
    return f"{len(shared)}/{len(union)} shared, jaccard={jaccard:.3f}, top1={top1}"


def source_key(source: dict) -> str:
    if source.get("source_chunk_id"):
        return str(source["source_chunk_id"])
    if source.get("source_id"):
        return str(source["source_id"])
    return f"{source.get('path')}#{source.get('chunk_index')}"


def escape_table_value(value: object) -> str:
    text = str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def timestamp_id() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S%f")


if __name__ == "__main__":
    raise SystemExit(main())
