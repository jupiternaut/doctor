from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .access_policy import (
    assert_path_allowed,
    assert_record_allowed,
    grant_access_consent,
    load_access_policy,
    read_access_audit,
    update_access_policy,
)
from .acceptance import run_v1_acceptance, run_v1_followup, run_v1_refresh, run_v1_stage_status
from .agent_preflight import run_agent_preflight
from .alternatives import resolve_alternative_context
from .answer_review import run_answer_review
from .codebase_memory import build_codebase_memory_index, search_codebase_memory
from .codex_plus_smoke import run_codex_plus_smoke
from .cold_index import build_cold_index, index_path_for, query_cold_index
from .context_review import run_context_review
from .embedding_benchmark import run_embedding_benchmark
from .execution_review import run_execution_review
from .feedback_replay import run_feedback_replay
from .feedback_replay_cases import run_feedback_replay_case_maintenance
from .feedback_replay_trend import run_feedback_replay_trend
from .ingest import ingest_scope
from .io import ensure_dir, read_jsonl
from .launchd import (
    run_semantic_launchd_audit,
    run_semantic_launchd_monitor,
    run_semantic_launchd_recover,
    run_semantic_launchd_trend,
    semantic_launchd_status as read_semantic_launchd_status,
)
from .pack import build_context_pack
from .panel import build_context_panel, record_panel_feedback
from .project_index import build_project_index, project_index_path_for
from .providers import (
    load_project_records,
    load_session_records,
    load_workflow_records,
    refresh_providers as refresh_provider_manifests,
    session_transcript_text,
)
from .reproducibility import run_reproducibility_snapshot
from .resolver import resolve_context as run_context_resolver
from .retrieval_eval import run_retrieval_eval
from .retrieval_eval_cases import run_retrieval_eval_case_maintenance
from .route_selector import write_route_selector_model
from .runtime_adapters import export_runtime_adapter_package
from .runtime_health import run_runtime_health, run_semantic_readiness
from .runtime_review_client import export_runtime_review_client, export_runtime_review_launch
from .runtime_task import start_runtime_task
from .runtime_vm import export_runtime_handoff, inspect_runtime_session, run_runtime_vm_acceptance, start_runtime_session
from .semantic_index import run_semantic_refresh, semantic_index_status as read_semantic_index_status
from .semantic_maintenance import run_semantic_ann_prune, run_semantic_maintenance
from .session_index import build_session_index, session_index_path_for


MCP_VERSION = "0.1"
DEFAULT_ROOT_ENV = "AGENT_CONTEXT_ROOT"


def default_out_root() -> Path:
    return Path(os.environ.get(DEFAULT_ROOT_ENV, ".")).expanduser().resolve()


def resolve_out_root(out_root: str | None = None) -> Path:
    if out_root:
        return Path(out_root).expanduser().resolve()
    return default_out_root()


def mcp_index_context(out_root: str | None = None) -> dict[str, Any]:
    return build_cold_index(resolve_out_root(out_root))


def mcp_search_context(query: str, limit: int = 12, out_root: str | None = None) -> dict[str, Any]:
    root = resolve_out_root(out_root)
    result = query_cold_index(root, query, limit=max(1, limit))
    sources = read_jsonl(Path(result["sources_jsonl_path"]))
    return {
        "mcp_version": MCP_VERSION,
        **result,
        "top_sources": [
            {
                "score": source.get("score"),
                "path": source.get("path"),
                "relative_path": source.get("relative_path"),
                "type": source.get("type"),
                "source_id": source.get("source_id"),
                "source_chunk_id": source.get("source_chunk_id"),
                "snippet": source.get("snippet"),
            }
            for source in sources[:limit]
        ],
    }


def mcp_resolve_context(
    goal: str,
    limit: int = 12,
    out_root: str | None = None,
    source_scope: str = "all",
) -> dict[str, Any]:
    root = resolve_out_root(out_root)
    try:
        result = run_context_resolver(root, goal, limit=max(1, limit), source_scope=source_scope)
    except Exception as exc:
        return {
            "mcp_version": MCP_VERSION,
            "status": "resolver_failed",
            "goal": goal,
            "source_scope": source_scope,
            "error": str(exc),
            "fallback": "continue_without_context",
            "top_sources": [],
        }
    sources = read_jsonl(Path(result["sources_jsonl_path"]))
    return {
        "mcp_version": MCP_VERSION,
        "status": "ok",
        **result,
        "top_sources": [
            {
                "score": source.get("score"),
                "path": source.get("path"),
                "relative_path": source.get("relative_path"),
                "type": source.get("type"),
                "source_id": source.get("source_id"),
                "source_chunk_id": source.get("source_chunk_id"),
                "why_selected": source.get("why_selected"),
                "snippet": source.get("snippet"),
            }
            for source in sources[:limit]
        ],
    }


def mcp_doctor_run(
    goal: str,
    session_id: str | None = None,
    mode: str = "standard",
    out_root: str | None = None,
) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **start_runtime_session(resolve_out_root(out_root), goal, session_id=session_id, mode=mode),
    }


def mcp_doctor_runtime_task(
    goal: str,
    session_id: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    out_root: str | None = None,
) -> dict[str, Any]:
    try:
        return {
            "mcp_version": MCP_VERSION,
            **start_runtime_task(
                resolve_out_root(out_root),
                goal,
                session_id=session_id,
                host=host,
                port=max(1, port),
            ),
        }
    except (FileNotFoundError, ValueError) as exc:
        return runtime_mcp_error("runtime_task", "clarify", session_id, exc)


def mcp_doctor_agent_preflight(
    advance: str = "clarify",
    goal: str | None = None,
    session_id: str | None = None,
    source_scope: str = "all",
    limit: int = 8,
    mode: str = "fast",
    agent_command: str = "<agent command>",
    review_port: int = 8765,
    out_root: str | None = None,
) -> dict[str, Any]:
    try:
        return {
            "mcp_version": MCP_VERSION,
            **run_agent_preflight(
                resolve_out_root(out_root),
                advance=advance,
                goal=goal,
                session_id=session_id,
                source_scope=source_scope,
                limit=max(1, limit),
                mode=mode,
                agent_command=agent_command,
                review_port=max(1, review_port),
            ),
        }
    except (FileNotFoundError, ValueError) as exc:
        return runtime_mcp_error("agent_preflight", advance, session_id, exc)


def mcp_doctor_session(session_id: str, out_root: str | None = None) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **inspect_runtime_session(resolve_out_root(out_root), session_id),
    }


def mcp_doctor_runtime_acceptance(session_id: str, out_root: str | None = None) -> dict[str, Any]:
    try:
        return {
            "mcp_version": MCP_VERSION,
            **run_runtime_vm_acceptance(resolve_out_root(out_root), session_id),
        }
    except FileNotFoundError as exc:
        return runtime_mcp_error("runtime_acceptance", "verify", session_id, exc)


def mcp_doctor_runtime_handoff(session_id: str, out_root: str | None = None) -> dict[str, Any]:
    try:
        return {
            "mcp_version": MCP_VERSION,
            **export_runtime_handoff(resolve_out_root(out_root), session_id),
        }
    except (FileNotFoundError, ValueError) as exc:
        return runtime_mcp_error("runtime_handoff", "export", session_id, exc)


def mcp_doctor_runtime_adapter(
    session_id: str,
    targets: list[str] | None = None,
    agent_command: str = "<agent command>",
    review_port: int = 8765,
    out_root: str | None = None,
) -> dict[str, Any]:
    try:
        return {
            "mcp_version": MCP_VERSION,
            **export_runtime_adapter_package(
                resolve_out_root(out_root),
                session_id,
                targets=targets,
                agent_command=agent_command,
                review_port=max(1, review_port),
            ),
        }
    except (FileNotFoundError, ValueError) as exc:
        return runtime_mcp_error("runtime_adapter", "export", session_id, exc)


def mcp_doctor_runtime_review_client(
    session_id: str,
    review_server_url: str = "http://127.0.0.1:8765/",
    out_root: str | None = None,
) -> dict[str, Any]:
    try:
        return {
            "mcp_version": MCP_VERSION,
            **export_runtime_review_client(
                resolve_out_root(out_root),
                session_id,
                review_server_url=review_server_url,
            ),
        }
    except FileNotFoundError as exc:
        return runtime_mcp_error("runtime_review_client", "export", session_id, exc)


def mcp_doctor_runtime_review_launch(
    session_id: str,
    host: str = "127.0.0.1",
    port: int = 8765,
    out_root: str | None = None,
) -> dict[str, Any]:
    try:
        return {
            "mcp_version": MCP_VERSION,
            **export_runtime_review_launch(
                resolve_out_root(out_root),
                session_id,
                host=host,
                port=max(1, port),
            ),
        }
    except FileNotFoundError as exc:
        return runtime_mcp_error("runtime_review_launch", "export", session_id, exc)


def mcp_doctor_context_review(
    action: str = "generate",
    session_id: str | None = None,
    refined_prompt_path: str | None = None,
    reason: str = "",
    source_scope: str = "all",
    limit: int = 12,
    mode: str = "fast",
    out_root: str | None = None,
) -> dict[str, Any]:
    root = resolve_out_root(out_root)
    try:
        review = run_context_review(
            root,
            action=action,
            refined_prompt_path=refined_prompt_path,
            session_id=session_id,
            reason=reason,
            source_scope=source_scope,
            limit=max(1, limit),
            mode=mode,
        )
    except (FileNotFoundError, ValueError) as exc:
        return runtime_mcp_error("context_review", action, session_id, exc)
    return {
        "mcp_version": MCP_VERSION,
        **review,
        "runtime_session": maybe_runtime_session(root, str(review.get("session_id") or session_id or "")),
    }


def mcp_doctor_answer_review(
    action: str = "prepare",
    session_id: str = "",
    answer_text: str = "",
    answer_file: str | None = None,
    command: str = "",
    cwd: str | None = None,
    timeout_seconds: int = 120,
    reason: str = "",
    out_root: str | None = None,
) -> dict[str, Any]:
    root = resolve_out_root(out_root)
    try:
        review = run_answer_review(
            root,
            action=action,
            session_id=session_id,
            answer_text=answer_text,
            answer_file=answer_file,
            command=command,
            cwd=cwd,
            timeout_seconds=max(1, timeout_seconds),
            reason=reason,
        )
    except (FileNotFoundError, ValueError) as exc:
        return runtime_mcp_error("answer_review", action, session_id, exc)
    return {
        "mcp_version": MCP_VERSION,
        **review,
        "runtime_session": maybe_runtime_session(root, session_id),
    }


def mcp_doctor_execution_review(
    action: str = "prepare",
    session_id: str = "",
    command: str = "",
    cwd: str | None = None,
    timeout_seconds: int = 120,
    artifact_file: str | None = None,
    reason: str = "",
    out_root: str | None = None,
) -> dict[str, Any]:
    root = resolve_out_root(out_root)
    try:
        review = run_execution_review(
            root,
            action=action,
            session_id=session_id,
            command=command,
            cwd=cwd,
            timeout_seconds=max(1, timeout_seconds),
            artifact_file=artifact_file,
            reason=reason,
        )
    except (FileNotFoundError, ValueError) as exc:
        return runtime_mcp_error("execution_review", action, session_id, exc)
    return {
        "mcp_version": MCP_VERSION,
        **review,
        "runtime_session": maybe_runtime_session(root, session_id),
    }


def runtime_mcp_error(stage: str, action: str, session_id: str | None, exc: Exception) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        "status": "error",
        "stage": stage,
        "action": action,
        "session_id": session_id,
        "error": str(exc),
    }


def maybe_runtime_session(root: Path, session_id: str) -> dict[str, Any] | None:
    if not session_id:
        return None
    try:
        return inspect_runtime_session(root, session_id)
    except FileNotFoundError:
        return None


def mcp_resolve_alternative_context(
    goal: str,
    rejected_sources: list[str],
    reason: str = "",
    limit: int = 12,
    out_root: str | None = None,
    source_scope: str = "all",
) -> dict[str, Any]:
    root = resolve_out_root(out_root)
    try:
        result = resolve_alternative_context(
            root,
            goal=goal,
            rejected_sources=rejected_sources,
            reason=reason,
            source_scope=source_scope,
            limit=max(1, limit),
        )
    except Exception as exc:
        return {
            "mcp_version": MCP_VERSION,
            "status": "alternative_resolver_failed",
            "goal": goal,
            "source_scope": source_scope,
            "rejected_sources": rejected_sources,
            "error": str(exc),
            "top_sources": [],
        }
    sources = read_jsonl(Path(result["sources_jsonl_path"]))
    return {
        "mcp_version": MCP_VERSION,
        **result,
        "top_sources": [
            {
                "score": source.get("score"),
                "path": source.get("path"),
                "relative_path": source.get("relative_path"),
                "type": source.get("type"),
                "source_id": source.get("source_id"),
                "source_chunk_id": source.get("source_chunk_id"),
                "why_selected": source.get("why_selected"),
                "snippet": source.get("snippet"),
            }
            for source in sources[:limit]
        ],
    }


def mcp_refresh_providers(
    out_root: str | None = None,
    project_roots: list[str] | None = None,
    sessions_root: str | None = None,
    claude_root: str | None = None,
    workflow_roots: list[str] | None = None,
    max_projects: int = 300,
    max_sessions: int = 300,
    max_workflows: int = 300,
) -> dict[str, Any]:
    root = resolve_out_root(out_root)
    return {
        "mcp_version": MCP_VERSION,
        **refresh_provider_manifests(
            root,
            project_roots=[Path(value) for value in project_roots] if project_roots else None,
            sessions_root=Path(sessions_root) if sessions_root else None,
            claude_root=Path(claude_root) if claude_root else None,
            workflow_roots=[Path(value) for value in workflow_roots] if workflow_roots else None,
            max_projects=max(1, max_projects),
            max_sessions=max(1, max_sessions),
            max_workflows=max(1, max_workflows),
        ),
    }


def mcp_index_projects(
    out_root: str | None = None,
    project_roots: list[str] | None = None,
    max_projects: int = 300,
    max_files_per_project: int = 300,
) -> dict[str, Any]:
    root = resolve_out_root(out_root)
    return {
        "mcp_version": MCP_VERSION,
        **build_project_index(
            root,
            project_roots=[Path(value) for value in project_roots] if project_roots else None,
            max_projects=max(1, max_projects),
            max_files_per_project=max(1, max_files_per_project),
        ),
    }


def mcp_codebase_memory_index(
    out_root: str | None = None,
    repo_paths: list[str] | None = None,
    binary: str | None = None,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    root = resolve_out_root(out_root)
    return {
        "mcp_version": MCP_VERSION,
        **build_codebase_memory_index(
            root,
            repo_paths=[Path(value) for value in repo_paths] if repo_paths else None,
            binary=binary,
            timeout_seconds=max(1, timeout_seconds),
        ),
    }


def mcp_codebase_memory_search(
    query: str,
    limit: int = 12,
    out_root: str | None = None,
    binary: str | None = None,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **search_codebase_memory(
            resolve_out_root(out_root),
            query,
            limit=max(1, limit),
            binary=binary,
            timeout_seconds=max(1, timeout_seconds),
        ),
    }


def mcp_index_sessions(
    out_root: str | None = None,
    max_sessions: int = 300,
    max_messages_per_session: int = 1000,
) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **build_session_index(
            resolve_out_root(out_root),
            max_sessions=max(1, max_sessions),
            max_messages_per_session=max(1, max_messages_per_session),
        ),
    }


def mcp_build_hot_pack(
    scope: str,
    goal: str,
    out_root: str | None = None,
    with_index: bool = False,
) -> dict[str, Any]:
    root = resolve_out_root(out_root)
    ingest_result = ingest_scope(Path(scope), root)
    pack_result = build_context_pack(Path(scope), root, goal)
    result: dict[str, Any] = {
        "mcp_version": MCP_VERSION,
        "ingest": ingest_result,
        "pack": pack_result,
    }
    if with_index:
        result["index"] = build_cold_index(root)
    return result


def mcp_read_source(identifier: str, out_root: str | None = None, max_chars: int = 4000) -> dict[str, Any]:
    root = resolve_out_root(out_root)
    max_chars = max(200, min(max_chars, 20_000))
    db_path = index_path_for(root)
    if db_path.exists():
        record = lookup_index_record(db_path, identifier)
        if record:
            assert_record_allowed(root, record, identifier, action="mcp_read_context_index")
            return {
                "mcp_version": MCP_VERSION,
                "identifier": identifier,
                **record,
                "warnings": text_warnings(record.get("text") or ""),
                "text": trim_text(record.get("text") or "", max_chars),
            }

    project_db_path = project_index_path_for(root)
    if project_db_path.exists():
        record = lookup_index_record(project_db_path, identifier)
        if record:
            assert_record_allowed(root, record, identifier, action="mcp_read_project_index")
            return {
                "mcp_version": MCP_VERSION,
                "identifier": identifier,
                **record,
                "warnings": text_warnings(record.get("text") or ""),
                "text": trim_text(record.get("text") or "", max_chars),
            }

    session_db_path = session_index_path_for(root)
    if session_db_path.exists():
        record = lookup_index_record(session_db_path, identifier)
        if record:
            assert_record_allowed(root, record, identifier, action="mcp_read_session_index")
            return {
                "mcp_version": MCP_VERSION,
                "identifier": identifier,
                **record,
                "read_mode": "session_index_chunk",
                "warnings": text_warnings(record.get("text") or ""),
                "text": trim_text(record.get("text") or "", max_chars),
            }

    provider_record = lookup_provider_record(root, identifier)
    if provider_record:
        assert_record_allowed(root, provider_record, identifier, action="mcp_read_provider")
        is_session_provider = provider_record.get("provider") in {"codex_session", "claude_session"}
        text = session_transcript_text(provider_record) if is_session_provider else provider_record_text(provider_record)
        return {
            "mcp_version": MCP_VERSION,
            "identifier": identifier,
            "type": provider_record.get("provider", "provider_card"),
            "read_mode": "session_transcript_preview" if is_session_provider else "provider_card",
            "path": provider_record.get("path"),
            "relative_path": provider_record.get("relative_path"),
            "warnings": text_warnings(text),
            "text": trim_text(text, max_chars),
        }

    path = resolve_generated_artifact_path(root, identifier)
    if path is None:
        raise FileNotFoundError(
            "source not found in indexes/provider manifests, and raw path reads are limited "
            f"to generated files under out_root: {identifier}"
        )
    assert_path_allowed(root, path, identifier, action="mcp_read_generated_artifact")

    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "mcp_version": MCP_VERSION,
        "identifier": identifier,
        "type": "generated_artifact",
        "read_mode": "generated_artifact",
        "path": str(path),
        "warnings": text_warnings(text),
        "text": trim_text(text, max_chars),
    }


def lookup_record_for_consent(root: Path, identifier: str) -> dict[str, Any]:
    for db_path in [index_path_for(root), project_index_path_for(root), session_index_path_for(root)]:
        if db_path.exists():
            record = lookup_index_record(db_path, identifier)
            if record:
                return record
    provider_record = lookup_provider_record(root, identifier)
    if provider_record:
        return provider_record
    path = resolve_generated_artifact_path(root, identifier)
    if path is not None:
        return {"provider": "generated_artifact", "path": str(path)}
    raise FileNotFoundError(f"source not found for access consent: {identifier}")


def mcp_grant_access_consent(identifier: str, reason: str = "", out_root: str | None = None) -> dict[str, Any]:
    root = resolve_out_root(out_root)
    record = lookup_record_for_consent(root, identifier)
    return {
        "mcp_version": MCP_VERSION,
        **grant_access_consent(root, identifier=identifier, record=record, reason=reason),
    }


def lookup_index_record(db_path: Path, identifier: str) -> dict[str, Any] | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        try:
            chunk = conn.execute(
                """
                SELECT c.*, d.parser, d.policy, d.status, d.extension
                FROM chunks c
                LEFT JOIN documents d ON d.source_id = c.source_id
                WHERE c.source_chunk_id = ?
                   OR c.chunk_id = ?
                   OR c.path = ?
                   OR c.relative_path = ?
                ORDER BY c.chunk_index
                LIMIT 1
                """,
                (identifier, identifier, identifier, identifier),
            ).fetchone()
            if chunk:
                return {
                    "type": "chunk",
                    "source_chunk_id": chunk["source_chunk_id"],
                    "source_id": chunk["source_id"],
                    "chunk_id": chunk["chunk_id"],
                    "doc_id": chunk["doc_id"],
                    "path": chunk["path"],
                    "relative_path": chunk["relative_path"],
                    "chunk_index": chunk["chunk_index"],
                    "parser": chunk["parser"],
                    "policy": chunk["policy"],
                    "status": chunk["status"],
                    "extension": chunk["extension"],
                    "text": chunk["text"],
                }

            document = conn.execute(
                """
                SELECT *
                FROM documents
                WHERE source_id = ?
                   OR doc_id = ?
                   OR path = ?
                   OR relative_path = ?
                LIMIT 1
                """,
                (identifier, identifier, identifier, identifier),
            ).fetchone()
            if document:
                return {
                    "type": "document",
                    "source_id": document["source_id"],
                    "doc_id": document["doc_id"],
                    "path": document["path"],
                    "relative_path": document["relative_path"],
                    "parser": document["parser"],
                    "policy": document["policy"],
                    "status": document["status"],
                    "extension": document["extension"],
                    "text": document_text(document),
                }
        except sqlite3.DatabaseError:
            return None
    finally:
        conn.close()
    return None


def lookup_provider_record(root: Path, identifier: str) -> dict[str, Any] | None:
    for record in [*load_project_records(root), *load_session_records(root), *load_workflow_records(root)]:
        keys = {
            str(record.get("source_id") or ""),
            str(record.get("project_id") or ""),
            str(record.get("session_id") or ""),
            str(record.get("workflow_id") or ""),
            str(record.get("path") or ""),
            str(record.get("relative_path") or ""),
            str(record.get("name") or ""),
            str(record.get("title") or ""),
            str(record.get("thread_name") or ""),
        }
        if identifier in keys:
            return record
    return None


def resolve_generated_artifact_path(root: Path, identifier: str) -> Path | None:
    path = Path(identifier).expanduser()
    if not path.is_absolute():
        path = root / identifier
    try:
        resolved = path.resolve()
        resolved_root = root.resolve()
    except OSError:
        return None
    if not resolved.exists() or not resolved.is_file():
        return None
    if not is_relative_to(resolved, resolved_root):
        return None
    return resolved


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def provider_record_text(record: dict[str, Any]) -> str:
    lines = [
        f"provider: {record.get('provider')}",
        f"source_id: {record.get('source_id')}",
        f"path: {record.get('path')}",
        f"relative_path: {record.get('relative_path')}",
    ]
    for field in ("name", "title", "thread_name", "cwd", "updated_at", "first_user_message", "last_user_message"):
        value = record.get(field)
        if value:
            lines.append(f"{field}: {value}")
    if record.get("text"):
        lines.extend(["", str(record["text"])])
    return "\n".join(lines)


def document_text(document: sqlite3.Row) -> str:
    extracted = document["extracted_md_path"]
    if extracted:
        path = Path(extracted)
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    return (
        f"metadata-only source; path={document['path']}; "
        f"relative_path={document['relative_path']}; extension={document['extension']}; "
        f"parser={document['parser']}; policy={document['policy']}; status={document['status']}"
    )


def trim_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 80].rstrip() + "\n\n[agent-context: truncated]"


def text_warnings(text: str) -> list[str]:
    if not text:
        return ["empty text"]
    sample = text[:2000]
    replacement_count = sample.count("\ufffd")
    control_count = sum(1 for char in sample if ord(char) < 32 and char not in "\n\r\t")
    warnings = []
    if replacement_count / max(len(sample), 1) > 0.02:
        warnings.append("text contains many replacement characters; source may be binary or incorrectly decoded")
    if control_count / max(len(sample), 1) > 0.02:
        warnings.append("text contains many control characters; source may be binary")
    return warnings


def mcp_record_feedback(
    query_id: str,
    selected_source: str,
    reason: str = "",
    rating: int | None = None,
    out_root: str | None = None,
) -> dict[str, Any]:
    root = resolve_out_root(out_root)
    feedback_dir = ensure_dir(root / "feedback")
    feedback_path = feedback_dir / "mcp_feedback.jsonl"
    record = {
        "mcp_version": MCP_VERSION,
        "created_at": datetime.now().astimezone().isoformat(),
        "query_id": query_id,
        "selected_source": selected_source,
        "reason": reason,
        "rating": rating,
    }
    with feedback_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        handle.write("\n")
    return {"feedback_path": str(feedback_path), "record": record}


def mcp_context_panel(
    out_root: str | None = None,
    goal: str | None = None,
    source_scope: str = "all",
    mode: str = "fast",
    limit: int = 12,
    auto_context: bool = True,
) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **build_context_panel(
            resolve_out_root(out_root),
            goal=goal,
            source_scope=source_scope,
            mode=mode,
            limit=max(1, limit),
            auto_context=auto_context,
        ),
    }


def mcp_record_panel_feedback(
    source: str,
    rating: str,
    reason: str = "",
    status_path: str | None = None,
    out_root: str | None = None,
) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **record_panel_feedback(
            resolve_out_root(out_root),
            source=source,
            rating=rating,
            reason=reason,
            status_path=status_path,
        ),
    }


def mcp_semantic_refresh(
    out_root: str | None = None,
    source: str = "all",
    budget: int = 32,
    backend: str = "fastembed",
    text_chars: int = 800,
) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **run_semantic_refresh(
            resolve_out_root(out_root),
            source=source,
            budget=max(1, budget),
            backend=backend,
            text_chars=max(80, text_chars),
        ),
    }


def mcp_semantic_index_status(out_root: str | None = None) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **read_semantic_index_status(resolve_out_root(out_root)),
    }


def mcp_access_audit(out_root: str | None = None, limit: int = 50) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **read_access_audit(resolve_out_root(out_root), limit=max(1, limit)),
    }


def mcp_access_policy(
    out_root: str | None = None,
    allow_providers: list[str] | None = None,
    remove_allow_providers: list[str] | None = None,
    deny_providers: list[str] | None = None,
    remove_deny_providers: list[str] | None = None,
    deny_path_patterns: list[str] | None = None,
    remove_deny_path_patterns: list[str] | None = None,
    require_consent_providers: list[str] | None = None,
    remove_require_consent_providers: list[str] | None = None,
    require_consent_path_patterns: list[str] | None = None,
    remove_require_consent_path_patterns: list[str] | None = None,
    audit_max_bytes: int | None = None,
    audit_max_rotated_files: int | None = None,
) -> dict[str, Any]:
    root = resolve_out_root(out_root)
    has_update = any(
        [
            allow_providers,
            remove_allow_providers,
            deny_providers,
            remove_deny_providers,
            deny_path_patterns,
            remove_deny_path_patterns,
            require_consent_providers,
            remove_require_consent_providers,
            require_consent_path_patterns,
            remove_require_consent_path_patterns,
            audit_max_bytes is not None,
            audit_max_rotated_files is not None,
        ]
    )
    if not has_update:
        return {"mcp_version": MCP_VERSION, "policy": load_access_policy(root)}
    return {
        "mcp_version": MCP_VERSION,
        **update_access_policy(
            root,
            allow_providers=allow_providers,
            remove_allow_providers=remove_allow_providers,
            deny_providers=deny_providers,
            remove_deny_providers=remove_deny_providers,
            deny_path_patterns=deny_path_patterns,
            remove_deny_path_patterns=remove_deny_path_patterns,
            require_consent_providers=require_consent_providers,
            remove_require_consent_providers=remove_require_consent_providers,
            require_consent_path_patterns=require_consent_path_patterns,
            remove_require_consent_path_patterns=remove_require_consent_path_patterns,
            audit_max_bytes=audit_max_bytes,
            audit_max_rotated_files=audit_max_rotated_files,
        ),
    }


def mcp_semantic_maintain(
    out_root: str | None = None,
    source: str = "all",
    budget: int = 32,
    backend: str = "fastembed",
    text_chars: int = 800,
    max_jobs: int = 1,
    min_interval_minutes: int = 0,
) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **run_semantic_maintenance(
            resolve_out_root(out_root),
            source=source,
            budget=max(1, budget),
            backend=backend,
            text_chars=max(80, text_chars),
            max_jobs=max(1, max_jobs),
            min_interval_minutes=max(0, min_interval_minutes),
        ),
    }


def mcp_semantic_ann_prune(
    out_root: str | None = None,
    max_entries: int = 32,
    max_bytes: int = 1_000_000_000,
    dry_run: bool = False,
) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **run_semantic_ann_prune(
            resolve_out_root(out_root),
            max_entries=max(1, max_entries),
            max_bytes=max(0, max_bytes),
            dry_run=dry_run,
        ),
    }


def mcp_semantic_launchd_status(
    out_root: str | None = None,
    label: str = "com.gengrf.agent-context.semantic-maintenance",
    tail_lines: int = 20,
    launch_agents_dir: str | None = None,
    with_launchctl: bool = False,
) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **read_semantic_launchd_status(
            resolve_out_root(out_root),
            label=label,
            launch_agents_dir=Path(launch_agents_dir).expanduser().resolve() if launch_agents_dir else None,
            tail_lines=max(0, tail_lines),
            include_launchctl=with_launchctl,
        ),
    }


def mcp_semantic_launchd_monitor(
    out_root: str | None = None,
    label: str = "com.gengrf.agent-context.semantic-maintenance",
    tail_lines: int = 20,
    launch_agents_dir: str | None = None,
    with_launchctl: bool = True,
    max_history: int = 200,
) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **run_semantic_launchd_monitor(
            resolve_out_root(out_root),
            label=label,
            launch_agents_dir=Path(launch_agents_dir).expanduser().resolve() if launch_agents_dir else None,
            tail_lines=max(0, tail_lines),
            with_launchctl=with_launchctl,
            max_history=max(1, max_history),
        ),
    }


def mcp_semantic_launchd_audit(
    out_root: str | None = None,
    max_history: int = 200,
    min_snapshots: int = 2,
    consecutive_unhealthy_threshold: int = 3,
    max_snapshot_age_seconds: int | None = None,
    notify: bool = False,
    notify_on: str = "alert",
) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **run_semantic_launchd_audit(
            resolve_out_root(out_root),
            max_history=max(1, max_history),
            min_snapshots=max(1, min_snapshots),
            consecutive_unhealthy_threshold=max(1, consecutive_unhealthy_threshold),
            max_snapshot_age_seconds=max_snapshot_age_seconds,
            notify=notify,
            notify_on=notify_on,
        ),
    }


def mcp_semantic_launchd_recover(
    out_root: str | None = None,
    apply: bool = False,
    verify_after_apply: bool = False,
    label: str = "com.gengrf.agent-context.semantic-maintenance",
    launch_agents_dir: str | None = None,
    max_history: int = 200,
    agent_context_bin: str = "agent-context",
) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **run_semantic_launchd_recover(
            resolve_out_root(out_root),
            apply=apply,
            verify_after_apply=verify_after_apply,
            label=label,
            launch_agents_dir=Path(launch_agents_dir).expanduser().resolve() if launch_agents_dir else None,
            max_history=max(1, max_history),
            agent_context_bin=agent_context_bin,
        ),
    }


def mcp_semantic_launchd_trend(
    out_root: str | None = None,
    max_history: int = 1000,
    min_days: int = 2,
) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **run_semantic_launchd_trend(
            resolve_out_root(out_root),
            max_history=max(1, max_history),
            min_days=max(1, min_days),
        ),
    }


def mcp_semantic_benchmark(
    out_root: str | None = None,
    source: str = "projects",
    queries: list[str] | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **run_embedding_benchmark(
            resolve_out_root(out_root),
            source=source,
            queries=queries or [],
            limit=max(1, limit),
        ),
    }


def mcp_retrieval_eval(
    out_root: str | None = None,
    cases_path: str | None = None,
    inline_cases: list[str] | None = None,
    source: str = "projects",
    limit: int = 8,
) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **run_retrieval_eval(
            resolve_out_root(out_root),
            cases_path=Path(cases_path).expanduser().resolve() if cases_path else None,
            inline_cases=inline_cases or [],
            source=source,
            limit=max(1, limit),
        ),
    }


def mcp_retrieval_eval_cases(
    out_root: str | None = None,
    cases_path: str | None = None,
    output_cases_path: str | None = None,
    max_age_days: int = 0,
    source: str = "projects",
    bootstrap_runtime: bool = False,
) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **run_retrieval_eval_case_maintenance(
            resolve_out_root(out_root),
            cases_path=Path(cases_path).expanduser().resolve() if cases_path else None,
            output_cases_path=Path(output_cases_path).expanduser().resolve() if output_cases_path else None,
            max_age_days=max(0, max_age_days),
            default_source=source,
            include_runtime_bootstrap=bootstrap_runtime,
        ),
    }


def mcp_route_selector_model(
    out_root: str | None = None,
    max_reports: int = 50,
) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **write_route_selector_model(resolve_out_root(out_root), max_reports=max(1, max_reports)),
    }


def mcp_runtime_health(
    out_root: str | None = None,
    codex_plus_root: str | None = None,
    min_documents: int = 1,
    min_projects: int = 1,
    min_sessions: int = 1,
    min_workflows: int = 1,
    min_semantic_chunks: int = 16,
) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **run_runtime_health(
            resolve_out_root(out_root),
            codex_plus_root=Path(codex_plus_root).expanduser().resolve() if codex_plus_root else None,
            min_documents=max(0, min_documents),
            min_projects=max(0, min_projects),
            min_sessions=max(0, min_sessions),
            min_workflows=max(0, min_workflows),
            min_semantic_chunks=max(0, min_semantic_chunks),
        ),
    }


def mcp_v1_acceptance(
    out_root: str | None = None,
    codex_plus_root: str | None = None,
    refresh_health: bool = False,
    refresh_evidence: bool = False,
    min_documents: int = 1,
    min_projects: int = 1,
    min_sessions: int = 1,
    min_workflows: int = 1,
    min_semantic_chunks: int = 16,
    required_trend_days: int = 2,
    mcp_timeout_seconds: int = 60,
    codex_plus_timeout_seconds: int = 120,
    with_manager_feedback_smoke: bool = False,
) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **run_v1_acceptance(
            resolve_out_root(out_root),
            codex_plus_root=Path(codex_plus_root).expanduser().resolve() if codex_plus_root else None,
            refresh_health=refresh_health,
            refresh_evidence=refresh_evidence,
            min_documents=max(0, min_documents),
            min_projects=max(0, min_projects),
            min_sessions=max(0, min_sessions),
            min_workflows=max(0, min_workflows),
            min_semantic_chunks=max(0, min_semantic_chunks),
            required_trend_days=max(1, required_trend_days),
            mcp_timeout_seconds=max(5, mcp_timeout_seconds),
            codex_plus_timeout_seconds=max(5, codex_plus_timeout_seconds),
            with_manager_feedback_smoke=with_manager_feedback_smoke,
        ),
    }


def mcp_v1_followup(
    out_root: str | None = None,
    codex_plus_root: str | None = None,
    run_when_ready: bool = False,
    force: bool = False,
    min_documents: int = 1,
    min_projects: int = 1,
    min_sessions: int = 1,
    min_workflows: int = 1,
    min_semantic_chunks: int = 16,
    required_trend_days: int = 2,
    mcp_timeout_seconds: int = 60,
    codex_plus_timeout_seconds: int = 120,
    with_manager_feedback_smoke: bool = False,
) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **run_v1_followup(
            resolve_out_root(out_root),
            codex_plus_root=Path(codex_plus_root).expanduser().resolve() if codex_plus_root else None,
            run_when_ready=run_when_ready,
            force=force,
            min_documents=max(0, min_documents),
            min_projects=max(0, min_projects),
            min_sessions=max(0, min_sessions),
            min_workflows=max(0, min_workflows),
            min_semantic_chunks=max(0, min_semantic_chunks),
            required_trend_days=max(1, required_trend_days),
            mcp_timeout_seconds=max(5, mcp_timeout_seconds),
            codex_plus_timeout_seconds=max(5, codex_plus_timeout_seconds),
            with_manager_feedback_smoke=with_manager_feedback_smoke,
        ),
    }


def mcp_v1_refresh(
    out_root: str | None = None,
    codex_plus_root: str | None = None,
    force: bool = False,
    refresh_semantic_evidence: bool = True,
    refresh_mcp_smoke: bool = True,
    refresh_runtime_health: bool = True,
    min_documents: int = 1,
    min_projects: int = 1,
    min_sessions: int = 1,
    min_workflows: int = 1,
    min_semantic_chunks: int = 16,
    required_trend_days: int = 2,
    mcp_timeout_seconds: int = 60,
    codex_plus_timeout_seconds: int = 120,
    wait_for_semantic_evidence: bool = False,
    semantic_wait_timeout_seconds: int = 7200,
    semantic_wait_poll_seconds: int = 60,
    with_manager_feedback_smoke: bool = False,
) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **run_v1_refresh(
            resolve_out_root(out_root),
            codex_plus_root=Path(codex_plus_root).expanduser().resolve() if codex_plus_root else None,
            force=force,
            refresh_semantic_evidence=refresh_semantic_evidence,
            refresh_mcp_smoke=refresh_mcp_smoke,
            refresh_runtime_health=refresh_runtime_health,
            min_documents=max(0, min_documents),
            min_projects=max(0, min_projects),
            min_sessions=max(0, min_sessions),
            min_workflows=max(0, min_workflows),
            min_semantic_chunks=max(0, min_semantic_chunks),
            required_trend_days=max(1, required_trend_days),
            mcp_timeout_seconds=max(5, mcp_timeout_seconds),
            codex_plus_timeout_seconds=max(5, codex_plus_timeout_seconds),
            wait_for_semantic_evidence=wait_for_semantic_evidence,
            semantic_wait_timeout_seconds=max(0, semantic_wait_timeout_seconds),
            semantic_wait_poll_seconds=max(1, semantic_wait_poll_seconds),
            with_manager_feedback_smoke=with_manager_feedback_smoke,
        ),
    }


def mcp_v1_stage_status(
    out_root: str | None = None,
    codex_plus_root: str | None = None,
) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **run_v1_stage_status(
            resolve_out_root(out_root),
            codex_plus_root=Path(codex_plus_root).expanduser().resolve() if codex_plus_root else None,
        ),
    }


def mcp_codex_plus_smoke(
    out_root: str | None = None,
    codex_plus_root: str | None = None,
    timeout_seconds: int = 120,
    with_manager_feedback: bool = False,
    with_runtime: bool = False,
) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **run_codex_plus_smoke(
            resolve_out_root(out_root),
            codex_plus_root=Path(codex_plus_root).expanduser().resolve() if codex_plus_root else None,
            timeout_seconds=max(5, timeout_seconds),
            run_panel_status=True,
            run_manager_feedback=with_manager_feedback,
            run_runtime=with_runtime,
        ),
    }


def mcp_semantic_readiness(
    out_root: str | None = None,
    min_semantic_chunks: int = 16,
    required_trend_days: int = 2,
    label: str = "com.gengrf.agent-context.semantic-maintenance",
    launch_agents_dir: str | None = None,
    with_launchctl: bool = False,
) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **run_semantic_readiness(
            resolve_out_root(out_root),
            min_semantic_chunks=max(0, min_semantic_chunks),
            required_trend_days=max(1, required_trend_days),
            label=label,
            launch_agents_dir=Path(launch_agents_dir).expanduser().resolve() if launch_agents_dir else None,
            include_launchctl=with_launchctl,
        ),
    }


def mcp_reproducibility_snapshot(
    out_root: str | None = None,
    codex_plus_root: str | None = None,
) -> dict[str, Any]:
    root = resolve_out_root(out_root)
    roots = [root]
    if codex_plus_root:
        roots.append(Path(codex_plus_root).expanduser().resolve())
    return {
        "mcp_version": MCP_VERSION,
        **run_reproducibility_snapshot(root, roots=roots),
    }


def mcp_feedback_replay(
    out_root: str | None = None,
    cases_path: str | None = None,
    case_goals: list[str] | None = None,
    source_scope: str = "all",
    limit: int = 12,
) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **run_feedback_replay(
            resolve_out_root(out_root),
            cases_path=Path(cases_path).expanduser().resolve() if cases_path else None,
            case_goals=case_goals or [],
            source_scope=source_scope,
            limit=max(1, limit),
        ),
    }


def mcp_feedback_replay_cases(
    out_root: str | None = None,
    output_cases_path: str | None = None,
    source_scope: str = "all",
    limit: int = 12,
) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **run_feedback_replay_case_maintenance(
            resolve_out_root(out_root),
            output_cases_path=Path(output_cases_path).expanduser().resolve() if output_cases_path else None,
            source_scope=source_scope,
            limit=max(1, limit),
        ),
    }


def mcp_feedback_replay_trend(
    out_root: str | None = None,
    max_reports: int = 20,
    min_reports: int = 2,
) -> dict[str, Any]:
    return {
        "mcp_version": MCP_VERSION,
        **run_feedback_replay_trend(
            resolve_out_root(out_root),
            max_reports=max(1, max_reports),
            min_reports=max(1, min_reports),
        ),
    }


def create_mcp_server(out_root: str | None = None) -> FastMCP:
    root = resolve_out_root(out_root)
    server = FastMCP(
        "agent-context",
        instructions=(
            "Search and build local agent context packs from an agent-context-system "
            "workspace. Use doctor_runtime_task first for user-facing agent tasks that "
            "need review gates. Use resolve_context for approved direct retrieval, "
            "search_context for exact queries or fallback, then read_source for specific evidence."
        ),
    )

    @server.tool()
    def resolve_context(goal: str, limit: int = 12, source_scope: str = "all") -> dict[str, Any]:
        """Resolve a task goal into a Codex-readable hot context pack."""
        return mcp_resolve_context(goal=goal, limit=limit, source_scope=source_scope, out_root=str(root))

    @server.tool()
    def doctor_run(goal: str, session_id: str | None = None, mode: str = "standard") -> dict[str, Any]:
        """Start a Doctor runtime session with no-index clarification."""
        return mcp_doctor_run(goal=goal, session_id=session_id, mode=mode, out_root=str(root))

    @server.tool()
    def doctor_runtime_task(
        goal: str,
        session_id: str | None = None,
        host: str = "127.0.0.1",
        port: int = 8765,
    ) -> dict[str, Any]:
        """Start a one-shot Doctor task review session and export the review launch contract."""
        return mcp_doctor_runtime_task(
            goal=goal,
            session_id=session_id,
            host=host,
            port=port,
            out_root=str(root),
        )

    @server.tool()
    def doctor_agent_preflight(
        advance: str = "clarify",
        goal: str | None = None,
        session_id: str | None = None,
        source_scope: str = "all",
        limit: int = 8,
        mode: str = "fast",
        agent_command: str = "<agent command>",
        review_port: int = 8765,
    ) -> dict[str, Any]:
        """Default Doctor runtime preflight entrypoint for Codex++, Warp, Codex CLI, and MCP clients."""
        return mcp_doctor_agent_preflight(
            advance=advance,
            goal=goal,
            session_id=session_id,
            source_scope=source_scope,
            limit=limit,
            mode=mode,
            agent_command=agent_command,
            review_port=review_port,
            out_root=str(root),
        )

    @server.tool()
    def doctor_session(session_id: str) -> dict[str, Any]:
        """Inspect a Doctor runtime session and write DOCTOR_SESSION.md."""
        return mcp_doctor_session(session_id=session_id, out_root=str(root))

    @server.tool()
    def doctor_runtime_acceptance(session_id: str) -> dict[str, Any]:
        """Write a Doctor runtime VM acceptance handoff report."""
        return mcp_doctor_runtime_acceptance(session_id=session_id, out_root=str(root))

    @server.tool()
    def doctor_runtime_handoff(session_id: str) -> dict[str, Any]:
        """Export approved Doctor context for Codex++, Warp, or Doctor."""
        return mcp_doctor_runtime_handoff(session_id=session_id, out_root=str(root))

    @server.tool()
    def doctor_runtime_adapter(
        session_id: str,
        targets: list[str] | None = None,
        agent_command: str = "<agent command>",
        review_port: int = 8765,
    ) -> dict[str, Any]:
        """Export adapter files for Codex++, Warp, Codex CLI, and MCP clients."""
        return mcp_doctor_runtime_adapter(
            session_id=session_id,
            targets=targets,
            agent_command=agent_command,
            review_port=review_port,
            out_root=str(root),
        )

    @server.tool()
    def doctor_runtime_review_client(
        session_id: str,
        review_server_url: str = "http://127.0.0.1:8765/",
    ) -> dict[str, Any]:
        """Export embeddable review client files for Codex++, Warp, Codex CLI, and MCP clients."""
        return mcp_doctor_runtime_review_client(
            session_id=session_id,
            review_server_url=review_server_url,
            out_root=str(root),
        )

    @server.tool()
    def doctor_runtime_review_launch(
        session_id: str,
        host: str = "127.0.0.1",
        port: int = 8765,
    ) -> dict[str, Any]:
        """Export a launch manifest with review server/client URLs and commands."""
        return mcp_doctor_runtime_review_launch(
            session_id=session_id,
            host=host,
            port=port,
            out_root=str(root),
        )

    @server.tool()
    def doctor_context_review(
        action: str = "generate",
        session_id: str | None = None,
        refined_prompt_path: str | None = None,
        reason: str = "",
        source_scope: str = "all",
        limit: int = 12,
        mode: str = "fast",
    ) -> dict[str, Any]:
        """Generate, regenerate, approve, or reject a Doctor context payload."""
        return mcp_doctor_context_review(
            action=action,
            session_id=session_id,
            refined_prompt_path=refined_prompt_path,
            reason=reason,
            source_scope=source_scope,
            limit=limit,
            mode=mode,
            out_root=str(root),
        )

    @server.tool()
    def doctor_answer_review(
        action: str = "prepare",
        session_id: str = "",
        answer_text: str = "",
        answer_file: str | None = None,
        command: str = "",
        cwd: str | None = None,
        timeout_seconds: int = 120,
        reason: str = "",
    ) -> dict[str, Any]:
        """Prepare, run, record, approve, or reject a Doctor answer packet."""
        return mcp_doctor_answer_review(
            action=action,
            session_id=session_id,
            answer_text=answer_text,
            answer_file=answer_file,
            command=command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            reason=reason,
            out_root=str(root),
        )

    @server.tool()
    def doctor_execution_review(
        action: str = "prepare",
        session_id: str = "",
        command: str = "",
        cwd: str | None = None,
        timeout_seconds: int = 120,
        artifact_file: str | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        """Prepare, run, record, approve, or reject local execution artifacts."""
        return mcp_doctor_execution_review(
            action=action,
            session_id=session_id,
            command=command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            artifact_file=artifact_file,
            reason=reason,
            out_root=str(root),
        )

    @server.tool()
    def resolve_alternative_context(
        goal: str,
        rejected_sources: list[str],
        reason: str = "",
        limit: int = 12,
        source_scope: str = "all",
    ) -> dict[str, Any]:
        """Record rejected sources and resolve a replacement context pack."""
        return mcp_resolve_alternative_context(
            goal=goal,
            rejected_sources=rejected_sources,
            reason=reason,
            limit=limit,
            source_scope=source_scope,
            out_root=str(root),
        )

    @server.tool()
    def search_context(query: str, limit: int = 12) -> dict[str, Any]:
        """Search the local cold index and write a RAG context pack."""
        return mcp_search_context(query=query, limit=limit, out_root=str(root))

    @server.tool()
    def index_context() -> dict[str, Any]:
        """Rebuild the SQLite cold index from existing manifests."""
        return mcp_index_context(out_root=str(root))

    @server.tool()
    def refresh_providers(
        project_roots: list[str] | None = None,
        sessions_root: str | None = None,
        claude_root: str | None = None,
        workflow_roots: list[str] | None = None,
        max_projects: int = 300,
        max_sessions: int = 300,
        max_workflows: int = 300,
    ) -> dict[str, Any]:
        """Refresh project discovery, agent session, and workflow provider manifests."""
        return mcp_refresh_providers(
            out_root=str(root),
            project_roots=project_roots,
            sessions_root=sessions_root,
            claude_root=claude_root,
            workflow_roots=workflow_roots,
            max_projects=max_projects,
            max_sessions=max_sessions,
            max_workflows=max_workflows,
        )

    @server.tool()
    def index_projects(
        project_roots: list[str] | None = None,
        max_projects: int = 300,
        max_files_per_project: int = 300,
    ) -> dict[str, Any]:
        """Index project README/docs/source files into indexes/projects.sqlite."""
        return mcp_index_projects(
            out_root=str(root),
            project_roots=project_roots,
            max_projects=max_projects,
            max_files_per_project=max_files_per_project,
        )

    @server.tool()
    def codebase_memory_index(
        repo_paths: list[str] | None = None,
        binary: str | None = None,
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        """Build Doctor's Markdown pseudo repo and index it with codebase-memory-mcp when available."""
        return mcp_codebase_memory_index(
            out_root=str(root),
            repo_paths=repo_paths,
            binary=binary,
            timeout_seconds=timeout_seconds,
        )

    @server.tool()
    def codebase_memory_search(
        query: str,
        limit: int = 12,
        binary: str | None = None,
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        """Search the optional codebase-memory-mcp provider and return Doctor-shaped sources."""
        return mcp_codebase_memory_search(
            query=query,
            limit=limit,
            out_root=str(root),
            binary=binary,
            timeout_seconds=timeout_seconds,
        )

    @server.tool()
    def index_sessions(max_sessions: int = 300, max_messages_per_session: int = 1000) -> dict[str, Any]:
        """Index Codex/Claude session transcript previews into indexes/sessions.sqlite."""
        return mcp_index_sessions(
            out_root=str(root),
            max_sessions=max_sessions,
            max_messages_per_session=max_messages_per_session,
        )

    @server.tool()
    def build_hot_pack(scope: str, goal: str, with_index: bool = False) -> dict[str, Any]:
        """Scan a scope and build a Codex-readable hot context pack."""
        return mcp_build_hot_pack(scope=scope, goal=goal, out_root=str(root), with_index=with_index)

    @server.tool()
    def read_source(identifier: str, max_chars: int = 4000) -> dict[str, Any]:
        """Read a source by path, source_id, source_chunk_id, chunk_id, doc_id, or relative path."""
        return mcp_read_source(identifier=identifier, out_root=str(root), max_chars=max_chars)

    @server.tool()
    def record_feedback(query_id: str, selected_source: str, reason: str = "", rating: int | None = None) -> dict[str, Any]:
        """Record user feedback for a returned context source."""
        return mcp_record_feedback(
            query_id=query_id,
            selected_source=selected_source,
            reason=reason,
            rating=rating,
            out_root=str(root),
        )

    @server.tool()
    def context_panel(
        goal: str | None = None,
        source_scope: str = "all",
        mode: str = "fast",
        limit: int = 12,
        auto_context: bool = True,
    ) -> dict[str, Any]:
        """Write Context Panel status JSON/HTML, optionally running resolver first."""
        return mcp_context_panel(
            out_root=str(root),
            goal=goal,
            source_scope=source_scope,
            mode=mode,
            limit=limit,
            auto_context=auto_context,
        )

    @server.tool()
    def record_panel_feedback(source: str, rating: str, reason: str = "", status_path: str | None = None) -> dict[str, Any]:
        """Record Context Panel feedback for a source path or id."""
        return mcp_record_panel_feedback(
            source=source,
            rating=rating,
            reason=reason,
            status_path=status_path,
            out_root=str(root),
        )

    @server.tool()
    def semantic_refresh(source: str = "all", budget: int = 32, backend: str = "fastembed", text_chars: int = 800) -> dict[str, Any]:
        """Run a budgeted background semantic embedding refresh job."""
        return mcp_semantic_refresh(
            out_root=str(root),
            source=source,
            budget=budget,
            backend=backend,
            text_chars=text_chars,
        )

    @server.tool()
    def semantic_index_status() -> dict[str, Any]:
        """Report background semantic index progress."""
        return mcp_semantic_index_status(out_root=str(root))

    @server.tool()
    def access_audit(limit: int = 50) -> dict[str, Any]:
        """Return recent access policy audit events."""
        return mcp_access_audit(out_root=str(root), limit=limit)

    @server.tool()
    def access_policy(
        allow_providers: list[str] | None = None,
        remove_allow_providers: list[str] | None = None,
        deny_providers: list[str] | None = None,
        remove_deny_providers: list[str] | None = None,
        deny_path_patterns: list[str] | None = None,
        remove_deny_path_patterns: list[str] | None = None,
        require_consent_providers: list[str] | None = None,
        remove_require_consent_providers: list[str] | None = None,
        require_consent_path_patterns: list[str] | None = None,
        remove_require_consent_path_patterns: list[str] | None = None,
        audit_max_bytes: int | None = None,
        audit_max_rotated_files: int | None = None,
    ) -> dict[str, Any]:
        """Show or patch access policy allow/deny rules."""
        return mcp_access_policy(
            out_root=str(root),
            allow_providers=allow_providers,
            remove_allow_providers=remove_allow_providers,
            deny_providers=deny_providers,
            remove_deny_providers=remove_deny_providers,
            deny_path_patterns=deny_path_patterns,
            remove_deny_path_patterns=remove_deny_path_patterns,
            require_consent_providers=require_consent_providers,
            remove_require_consent_providers=remove_require_consent_providers,
            require_consent_path_patterns=require_consent_path_patterns,
            remove_require_consent_path_patterns=remove_require_consent_path_patterns,
            audit_max_bytes=audit_max_bytes,
            audit_max_rotated_files=audit_max_rotated_files,
        )

    @server.tool()
    def grant_access_consent(identifier: str, reason: str = "") -> dict[str, Any]:
        """Grant read consent for one indexed/provider/generated source."""
        return mcp_grant_access_consent(identifier=identifier, reason=reason, out_root=str(root))

    @server.tool()
    def semantic_maintain(
        source: str = "all",
        budget: int = 32,
        backend: str = "fastembed",
        text_chars: int = 800,
        max_jobs: int = 1,
        min_interval_minutes: int = 0,
    ) -> dict[str, Any]:
        """Run a scheduler-safe semantic maintenance pass and write reports."""
        return mcp_semantic_maintain(
            out_root=str(root),
            source=source,
            budget=budget,
            backend=backend,
            text_chars=text_chars,
            max_jobs=max_jobs,
            min_interval_minutes=min_interval_minutes,
        )

    @server.tool()
    def semantic_ann_prune(max_entries: int = 32, max_bytes: int = 1_000_000_000, dry_run: bool = False) -> dict[str, Any]:
        """Prune stale or excessive indexes/semantic_ann cache files."""
        return mcp_semantic_ann_prune(
            out_root=str(root),
            max_entries=max_entries,
            max_bytes=max_bytes,
            dry_run=dry_run,
        )

    @server.tool()
    def semantic_launchd_status(
        label: str = "com.gengrf.agent-context.semantic-maintenance",
        tail_lines: int = 20,
        with_launchctl: bool = False,
    ) -> dict[str, Any]:
        """Read semantic LaunchAgent installation, report, and log status."""
        return mcp_semantic_launchd_status(
            out_root=str(root),
            label=label,
            tail_lines=tail_lines,
            with_launchctl=with_launchctl,
        )

    @server.tool()
    def semantic_launchd_monitor(
        label: str = "com.gengrf.agent-context.semantic-maintenance",
        tail_lines: int = 20,
        with_launchctl: bool = True,
        max_history: int = 200,
    ) -> dict[str, Any]:
        """Append a semantic LaunchAgent health snapshot and write monitor reports."""
        return mcp_semantic_launchd_monitor(
            out_root=str(root),
            label=label,
            tail_lines=tail_lines,
            with_launchctl=with_launchctl,
            max_history=max_history,
        )

    @server.tool()
    def semantic_launchd_audit(
        max_history: int = 200,
        min_snapshots: int = 2,
        consecutive_unhealthy_threshold: int = 3,
        max_snapshot_age_seconds: int | None = None,
        notify: bool = False,
        notify_on: str = "alert",
    ) -> dict[str, Any]:
        """Audit semantic LaunchAgent monitor history and write a health report."""
        return mcp_semantic_launchd_audit(
            out_root=str(root),
            max_history=max_history,
            min_snapshots=min_snapshots,
            consecutive_unhealthy_threshold=consecutive_unhealthy_threshold,
            max_snapshot_age_seconds=max_snapshot_age_seconds,
            notify=notify,
            notify_on=notify_on,
        )

    @server.tool()
    def semantic_launchd_recover(
        apply: bool = False,
        verify_after_apply: bool = False,
        label: str = "com.gengrf.agent-context.semantic-maintenance",
        max_history: int = 200,
        agent_context_bin: str = "agent-context",
    ) -> dict[str, Any]:
        """Plan or apply recovery actions for semantic LaunchAgent maintenance. Defaults to dry-run."""
        return mcp_semantic_launchd_recover(
            out_root=str(root),
            apply=apply,
            verify_after_apply=verify_after_apply,
            label=label,
            max_history=max_history,
            agent_context_bin=agent_context_bin,
        )

    @server.tool()
    def semantic_launchd_trend(max_history: int = 1000, min_days: int = 2) -> dict[str, Any]:
        """Summarize semantic LaunchAgent monitor history across days and hours."""
        return mcp_semantic_launchd_trend(
            out_root=str(root),
            max_history=max_history,
            min_days=min_days,
        )

    @server.tool()
    def semantic_readiness(
        min_semantic_chunks: int = 16,
        required_trend_days: int = 2,
        label: str = "com.gengrf.agent-context.semantic-maintenance",
        with_launchctl: bool = False,
    ) -> dict[str, Any]:
        """Write a focused semantic background readiness report."""
        return mcp_semantic_readiness(
            out_root=str(root),
            min_semantic_chunks=min_semantic_chunks,
            required_trend_days=required_trend_days,
            label=label,
            with_launchctl=with_launchctl,
        )

    @server.tool()
    def semantic_benchmark(
        source: str = "projects",
        queries: list[str] | None = None,
        limit: int = 8,
    ) -> dict[str, Any]:
        """Compare hash, rerank, and semantic-fusion retrieval modes and write a report."""
        return mcp_semantic_benchmark(
            out_root=str(root),
            source=source,
            queries=queries or [],
            limit=limit,
        )

    @server.tool()
    def retrieval_eval(
        cases_path: str | None = None,
        inline_cases: list[str] | None = None,
        source: str = "projects",
        limit: int = 8,
    ) -> dict[str, Any]:
        """Evaluate retrieval backends against labeled expected sources."""
        return mcp_retrieval_eval(
            out_root=str(root),
            cases_path=cases_path,
            inline_cases=inline_cases or [],
            source=source,
            limit=limit,
        )

    @server.tool()
    def retrieval_eval_cases(
        cases_path: str | None = None,
        output_cases_path: str | None = None,
        max_age_days: int = 0,
        source: str = "projects",
        bootstrap_runtime: bool = False,
    ) -> dict[str, Any]:
        """Curate raw feedback into deduped retrieval eval cases."""
        return mcp_retrieval_eval_cases(
            out_root=str(root),
            cases_path=cases_path,
            output_cases_path=output_cases_path,
            max_age_days=max_age_days,
            source=source,
            bootstrap_runtime=bootstrap_runtime,
        )

    @server.tool()
    def feedback_replay(
        cases_path: str | None = None,
        case_goals: list[str] | None = None,
        source_scope: str = "all",
        limit: int = 12,
    ) -> dict[str, Any]:
        """Replay fixed goals before/after feedback rerank and write a report."""
        return mcp_feedback_replay(
            out_root=str(root),
            cases_path=cases_path,
            case_goals=case_goals or [],
            source_scope=source_scope,
            limit=limit,
        )

    @server.tool()
    def feedback_replay_cases(
        output_cases_path: str | None = None,
        source_scope: str = "all",
        limit: int = 12,
    ) -> dict[str, Any]:
        """Generate replay cases from feedback logs without editing raw feedback."""
        return mcp_feedback_replay_cases(
            out_root=str(root),
            output_cases_path=output_cases_path,
            source_scope=source_scope,
            limit=limit,
        )

    @server.tool()
    def feedback_replay_trend(max_reports: int = 20, min_reports: int = 2) -> dict[str, Any]:
        """Summarize feedback replay report history and health."""
        return mcp_feedback_replay_trend(
            out_root=str(root),
            max_reports=max_reports,
            min_reports=min_reports,
        )

    @server.tool()
    def route_selector_model(max_reports: int = 50) -> dict[str, Any]:
        """Compile retrieval eval reports into feedback/route_selector_model.json."""
        return mcp_route_selector_model(out_root=str(root), max_reports=max_reports)

    @server.tool()
    def runtime_health(
        codex_plus_root: str | None = None,
        min_documents: int = 1,
        min_projects: int = 1,
        min_sessions: int = 1,
        min_workflows: int = 1,
        min_semantic_chunks: int = 16,
    ) -> dict[str, Any]:
        """Write a v1 runtime health report with acceptance evidence."""
        return mcp_runtime_health(
            out_root=str(root),
            codex_plus_root=codex_plus_root,
            min_documents=min_documents,
            min_projects=min_projects,
            min_sessions=min_sessions,
            min_workflows=min_workflows,
            min_semantic_chunks=min_semantic_chunks,
        )

    @server.tool()
    def v1_acceptance(
        codex_plus_root: str | None = None,
        refresh_health: bool = False,
        refresh_evidence: bool = False,
        min_documents: int = 1,
        min_projects: int = 1,
        min_sessions: int = 1,
        min_workflows: int = 1,
        min_semantic_chunks: int = 16,
        required_trend_days: int = 2,
        mcp_timeout_seconds: int = 60,
        codex_plus_timeout_seconds: int = 120,
        with_manager_feedback_smoke: bool = False,
    ) -> dict[str, Any]:
        """Write one v1 acceptance handoff report from the latest runtime evidence."""
        return mcp_v1_acceptance(
            out_root=str(root),
            codex_plus_root=codex_plus_root,
            refresh_health=refresh_health,
            refresh_evidence=refresh_evidence,
            min_documents=min_documents,
            min_projects=min_projects,
            min_sessions=min_sessions,
            min_workflows=min_workflows,
            min_semantic_chunks=min_semantic_chunks,
            required_trend_days=required_trend_days,
            mcp_timeout_seconds=mcp_timeout_seconds,
            codex_plus_timeout_seconds=codex_plus_timeout_seconds,
            with_manager_feedback_smoke=with_manager_feedback_smoke,
        )

    @server.tool()
    def v1_followup(
        codex_plus_root: str | None = None,
        run_when_ready: bool = False,
        force: bool = False,
        min_documents: int = 1,
        min_projects: int = 1,
        min_sessions: int = 1,
        min_workflows: int = 1,
        min_semantic_chunks: int = 16,
        required_trend_days: int = 2,
        mcp_timeout_seconds: int = 60,
        codex_plus_timeout_seconds: int = 120,
        wait_for_semantic_evidence: bool = False,
        semantic_wait_timeout_seconds: int = 7200,
        semantic_wait_poll_seconds: int = 60,
        with_manager_feedback_smoke: bool = False,
    ) -> dict[str, Any]:
        """Check or safely run the v1 acceptance follow-up gate."""
        return mcp_v1_followup(
            out_root=str(root),
            codex_plus_root=codex_plus_root,
            run_when_ready=run_when_ready,
            force=force,
            min_documents=min_documents,
            min_projects=min_projects,
            min_sessions=min_sessions,
            min_workflows=min_workflows,
            min_semantic_chunks=min_semantic_chunks,
            required_trend_days=required_trend_days,
            mcp_timeout_seconds=mcp_timeout_seconds,
            codex_plus_timeout_seconds=codex_plus_timeout_seconds,
            wait_for_semantic_evidence=wait_for_semantic_evidence,
            semantic_wait_timeout_seconds=semantic_wait_timeout_seconds,
            semantic_wait_poll_seconds=semantic_wait_poll_seconds,
            with_manager_feedback_smoke=with_manager_feedback_smoke,
        )

    @server.tool()
    def v1_refresh(
        codex_plus_root: str | None = None,
        force: bool = False,
        refresh_semantic_evidence: bool = True,
        refresh_mcp_smoke: bool = True,
        refresh_runtime_health: bool = True,
        min_documents: int = 1,
        min_projects: int = 1,
        min_sessions: int = 1,
        min_workflows: int = 1,
        min_semantic_chunks: int = 16,
        required_trend_days: int = 2,
        mcp_timeout_seconds: int = 60,
        codex_plus_timeout_seconds: int = 120,
        with_manager_feedback_smoke: bool = False,
    ) -> dict[str, Any]:
        """Safely refresh v1 follow-up, stage status, and Context Panel status."""
        return mcp_v1_refresh(
            out_root=str(root),
            codex_plus_root=codex_plus_root,
            force=force,
            refresh_semantic_evidence=refresh_semantic_evidence,
            refresh_mcp_smoke=refresh_mcp_smoke,
            refresh_runtime_health=refresh_runtime_health,
            min_documents=min_documents,
            min_projects=min_projects,
            min_sessions=min_sessions,
            min_workflows=min_workflows,
            min_semantic_chunks=min_semantic_chunks,
            required_trend_days=required_trend_days,
            mcp_timeout_seconds=mcp_timeout_seconds,
            codex_plus_timeout_seconds=codex_plus_timeout_seconds,
            with_manager_feedback_smoke=with_manager_feedback_smoke,
        )

    @server.tool()
    def v1_stage_status(codex_plus_root: str | None = None) -> dict[str, Any]:
        """Write a compact v1 stage progress report from latest evidence."""
        return mcp_v1_stage_status(out_root=str(root), codex_plus_root=codex_plus_root)

    @server.tool()
    def codex_plus_smoke(
        codex_plus_root: str | None = None,
        timeout_seconds: int = 120,
        with_manager_feedback: bool = False,
        with_runtime: bool = False,
    ) -> dict[str, Any]:
        """Run Codex++ Agent Context smoke scripts and write a report."""
        return mcp_codex_plus_smoke(
            out_root=str(root),
            codex_plus_root=codex_plus_root,
            timeout_seconds=timeout_seconds,
            with_manager_feedback=with_manager_feedback,
            with_runtime=with_runtime,
        )

    @server.tool()
    def reproducibility_snapshot(codex_plus_root: str | None = None) -> dict[str, Any]:
        """Write a git worktree reproducibility snapshot for dirty local v1 changes."""
        return mcp_reproducibility_snapshot(out_root=str(root), codex_plus_root=codex_plus_root)

    return server


def run_mcp_server(out_root: str | None = None) -> None:
    create_mcp_server(out_root).run("stdio")
