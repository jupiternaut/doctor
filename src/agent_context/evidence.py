from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

EVIDENCE_SCHEMA_VERSION = "0.1"
SOURCE_TYPES = {
    "code",
    "document",
    "image",
    "video",
    "audio",
    "session",
    "profile",
    "workflow",
    "project",
    "artifact",
    "unknown",
}

SOURCE_GROUP_TYPE_MAP = {
    "codebase_memory": "code",
    "git_repositories": "code",
    "downloads_documents": "document",
    "workflow_docs": "workflow",
    "codex_sessions": "session",
}

PROVIDER_TYPE_MAP = {
    "codebase_memory": "code",
    "project_code_index": "code",
    "git_project": "project",
    "semantic_index": "document",
    "session_index": "session",
    "codex_session": "session",
    "claude_session": "session",
    "workflow_doc": "workflow",
    "douyin_video": "video",
    "douyin_asset": "video",
    "douyin_author": "profile",
}

EXTENSION_TYPE_MAP = {
    ".py": "code",
    ".js": "code",
    ".jsx": "code",
    ".ts": "code",
    ".tsx": "code",
    ".rs": "code",
    ".go": "code",
    ".java": "code",
    ".kt": "code",
    ".swift": "code",
    ".c": "code",
    ".cc": "code",
    ".cpp": "code",
    ".h": "code",
    ".hpp": "code",
    ".md": "document",
    ".markdown": "document",
    ".txt": "document",
    ".pdf": "document",
    ".docx": "document",
    ".xlsx": "document",
    ".pptx": "document",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".gif": "image",
    ".mp4": "video",
    ".mov": "video",
    ".m4v": "video",
    ".mp3": "audio",
    ".wav": "audio",
    ".m4a": "audio",
}


def attach_evidence_records(sources: list[dict[str, Any]], *, goal: str = "") -> list[dict[str, Any]]:
    return [attach_evidence_record(source, goal=goal) for source in sources]


def attach_evidence_record(source: dict[str, Any], *, goal: str = "") -> dict[str, Any]:
    record = dict(source)
    record["evidence"] = source_to_evidence_record(record, goal=goal)
    return record


def source_to_evidence_record(source: dict[str, Any], *, goal: str = "") -> dict[str, Any]:
    source_type = infer_source_type(source)
    path = str(source.get("path") or "")
    title = title_for_source(source, path)
    text = evidence_text(source)
    evidence_id = evidence_id_for(source)
    provider = str(source.get("provider") or source.get("parser") or source.get("policy") or "")
    source_group = str(source.get("source_group") or "")
    record = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "evidence_id": evidence_id,
        "source_type": source_type,
        "source_group": source_group,
        "provider": provider,
        "path": path,
        "relative_path": source.get("relative_path"),
        "title": title,
        "text": text,
        "summary": source.get("summary") or source.get("snippet") or "",
        "quote": source.get("snippet") or "",
        "location": evidence_location(source),
        "score": source.get("score"),
        "score_parts": source.get("score_parts") or {},
        "retrieval": {
            "query": source.get("retrieval_query") or goal,
            "matched_queries": source.get("matched_queries") or [],
            "channels": source.get("retrieval_channels") or source.get("retrieval_channel") or [],
        },
        "identifiers": evidence_identifiers(source),
        "entities": source.get("entities") or [],
        "edges": source.get("edges") or [],
        "embedding_refs": embedding_refs_for(source_type, source),
        "permissions": {
            "consent_required": bool(source.get("consent_required", False)),
            "access_policy": source.get("access_policy"),
        },
        "provenance": {
            "doc_id": source.get("doc_id"),
            "source_id": source.get("source_id"),
            "source_chunk_id": source.get("source_chunk_id") or source.get("chunk_id"),
            "parser": source.get("parser"),
            "status": source.get("status"),
        },
    }
    return canonicalize_evidence_record(record)


def canonicalize_evidence_record(record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(record)
    normalized["schema_version"] = str(normalized.get("schema_version") or EVIDENCE_SCHEMA_VERSION)
    normalized["evidence_id"] = str(normalized.get("evidence_id") or "")
    source_type = str(normalized.get("source_type") or "unknown")
    normalized["source_type"] = source_type if source_type in SOURCE_TYPES else "unknown"
    normalized["source_group"] = str(normalized.get("source_group") or "")
    normalized["provider"] = str(normalized.get("provider") or "")
    normalized["path"] = str(normalized.get("path") or "")
    normalized["relative_path"] = normalized.get("relative_path") or None
    normalized["title"] = str(normalized.get("title") or normalized.get("relative_path") or normalized.get("path") or "")
    normalized["text"] = compact_text(str(normalized.get("text") or ""))
    normalized["summary"] = compact_text(str(normalized.get("summary") or ""))
    normalized["quote"] = compact_text(str(normalized.get("quote") or ""))
    normalized["location"] = normalized.get("location") if isinstance(normalized.get("location"), dict) else {}
    normalized["score_parts"] = normalized.get("score_parts") if isinstance(normalized.get("score_parts"), dict) else {}
    normalized["retrieval"] = normalize_retrieval(normalized.get("retrieval"))
    normalized["identifiers"] = normalize_identifiers(normalized.get("identifiers"))
    normalized["entities"] = normalized.get("entities") if isinstance(normalized.get("entities"), list) else []
    normalized["edges"] = normalized.get("edges") if isinstance(normalized.get("edges"), list) else []
    normalized["embedding_refs"] = normalize_embedding_refs(normalized.get("embedding_refs"))
    normalized["permissions"] = normalized.get("permissions") if isinstance(normalized.get("permissions"), dict) else {}
    normalized["provenance"] = normalized.get("provenance") if isinstance(normalized.get("provenance"), dict) else {}
    return normalized


def validate_evidence_record(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if record.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        errors.append("schema_version")
    if not record.get("evidence_id"):
        errors.append("evidence_id")
    if record.get("source_type") not in SOURCE_TYPES:
        errors.append("source_type")
    if not record.get("path") and not record.get("text"):
        errors.append("path_or_text")
    if not isinstance(record.get("retrieval"), dict):
        errors.append("retrieval")
    if not isinstance(record.get("embedding_refs"), list):
        errors.append("embedding_refs")
    return errors


def infer_source_type(source: dict[str, Any]) -> str:
    source_type = source.get("source_type") or source.get("modality")
    if source_type in SOURCE_TYPES:
        return str(source_type)
    group = str(source.get("source_group") or "")
    if group in SOURCE_GROUP_TYPE_MAP:
        return SOURCE_GROUP_TYPE_MAP[group]
    provider = str(source.get("provider") or source.get("parser") or source.get("policy") or "")
    if provider in PROVIDER_TYPE_MAP:
        return PROVIDER_TYPE_MAP[provider]
    kind = str(source.get("type") or "")
    if kind in {"chunk", "metadata_only"}:
        return "document"
    if kind in {"project_code", "codebase_memory"}:
        return "code"
    if "session" in kind:
        return "session"
    path = str(source.get("path") or source.get("relative_path") or "")
    suffix = Path(path).suffix.lower()
    return EXTENSION_TYPE_MAP.get(suffix, "unknown")


def evidence_id_for(source: dict[str, Any]) -> str:
    for key in ("evidence_id", "source_chunk_id", "chunk_id", "source_id", "doc_id"):
        value = source.get(key)
        if value:
            return str(value)
    payload = "|".join(
        [
            str(source.get("provider") or ""),
            str(source.get("source_group") or ""),
            str(source.get("path") or ""),
            str(source.get("line") or ""),
            str(source.get("snippet") or ""),
        ]
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"evidence:{digest}"


def evidence_text(source: dict[str, Any]) -> str:
    for key in ("text", "snippet", "summary", "title", "relative_path", "path"):
        value = source.get(key)
        if value:
            return compact_text(str(value))
    return ""


def title_for_source(source: dict[str, Any], path: str) -> str:
    for key in ("title", "name", "qualified_name", "thread_name", "project_name"):
        value = source.get(key)
        if value:
            return str(value)
    if source.get("relative_path"):
        return str(source["relative_path"])
    if path:
        return Path(path).name
    return str(source.get("doc_id") or source.get("source_id") or "Untitled evidence")


def evidence_location(source: dict[str, Any]) -> dict[str, Any]:
    location: dict[str, Any] = {}
    for key in ("line", "start_line", "end_line", "chunk_index", "timestamp", "start_time", "end_time"):
        value = source.get(key)
        if value is not None:
            location[key] = value
    return location


def evidence_identifiers(source: dict[str, Any]) -> dict[str, Any]:
    identifiers = {}
    for key in (
        "doc_id",
        "source_id",
        "source_chunk_id",
        "chunk_id",
        "project_id",
        "project_name",
        "project_path",
        "session_id",
        "sha256",
    ):
        value = source.get(key)
        if value:
            identifiers[key] = value
    return identifiers


def embedding_refs_for(source_type: str, source: dict[str, Any]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    if source.get("embedding_ref"):
        refs.append({"kind": source_type, "ref": str(source["embedding_ref"])})
    if source_type in {"code", "document", "workflow", "session", "profile"}:
        refs.append({"kind": "text", "ref": "derived:text"})
    if source_type == "code":
        refs.append({"kind": "code", "ref": "provider:code_graph"})
    if source_type in {"image", "video"}:
        refs.append({"kind": "vision", "ref": "derived:vision"})
    if source_type in {"video", "audio"}:
        refs.append({"kind": "audio", "ref": "derived:asr"})
    return dedupe_embedding_refs(refs)


def normalize_retrieval(value: Any) -> dict[str, Any]:
    retrieval = value if isinstance(value, dict) else {}
    channels = retrieval.get("channels") or []
    if isinstance(channels, str):
        channels = [channels]
    return {
        "query": str(retrieval.get("query") or ""),
        "matched_queries": [str(query) for query in retrieval.get("matched_queries") or [] if query],
        "channels": [str(channel) for channel in channels if channel],
    }


def normalize_identifiers(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def normalize_embedding_refs(value: Any) -> list[dict[str, str]]:
    refs = value if isinstance(value, list) else []
    normalized = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        kind = str(ref.get("kind") or "")
        target = str(ref.get("ref") or "")
        if kind and target:
            normalized.append({"kind": kind, "ref": target})
    return dedupe_embedding_refs(normalized)


def dedupe_embedding_refs(refs: list[dict[str, str]]) -> list[dict[str, str]]:
    seen = set()
    result = []
    for ref in refs:
        key = (ref["kind"], ref["ref"])
        if key in seen:
            continue
        seen.add(key)
        result.append(ref)
    return result


def compact_text(value: str, *, limit: int = 2000) -> str:
    compact = re.sub(r"\s+", " ", value).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."
