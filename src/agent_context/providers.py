from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

from .io import ensure_dir, read_jsonl, write_jsonl
from .pack import snippet

PROJECT_PROVIDER_VERSION = "0.1"
SESSION_PROVIDER_VERSION = "0.1"
WORKFLOW_PROVIDER_VERSION = "0.1"
MAX_PROVIDER_TEXT_CHARS = 6000
DEFAULT_MAX_PROJECTS = 300
DEFAULT_MAX_SESSIONS = 300
DEFAULT_MAX_WORKFLOWS = 300
DEFAULT_PROJECT_DEPTH = 4
DEFAULT_SESSION_TRANSCRIPT_MESSAGES = 80

PROJECT_MARKER_FILES = (
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "README.md",
    "README.markdown",
    "PROJECT_TASK_README.md",
)
README_NAMES = ("README.md", "README.markdown", "PROJECT_TASK_README.md")
WORKFLOW_DOC_NAMES = (
    "README.md",
    "README.markdown",
    "PROJECT_TASK_README.md",
    "AGENTS.md",
    "CLAUDE.md",
)
IGNORED_DIR_NAMES = {
    ".cache",
    ".bun",
    ".codex",
    ".cargo",
    ".git",
    ".gradle",
    ".npm",
    ".pnpm-store",
    ".Trash",
    ".venv",
    ".vscode",
    "__pycache__",
    "Applications",
    "Library",
    "Movies",
    "Music",
    "node_modules",
    "Pictures",
}


@dataclass(frozen=True)
class ProviderPaths:
    manifests: Path
    projects_jsonl: Path
    sessions_jsonl: Path
    workflows_jsonl: Path

    @classmethod
    def from_root(cls, out_root: Path) -> "ProviderPaths":
        manifests = out_root / "manifests"
        return cls(
            manifests=manifests,
            projects_jsonl=manifests / "projects.jsonl",
            sessions_jsonl=manifests / "sessions.jsonl",
            workflows_jsonl=manifests / "workflows.jsonl",
        )


def refresh_providers(
    out_root: Path,
    *,
    project_roots: Iterable[Path] | None = None,
    sessions_root: Path | None = None,
    claude_root: Path | None = None,
    workflow_roots: Iterable[Path] | None = None,
    max_projects: int = DEFAULT_MAX_PROJECTS,
    max_sessions: int = DEFAULT_MAX_SESSIONS,
    max_workflows: int = DEFAULT_MAX_WORKFLOWS,
) -> dict:
    out_root = out_root.expanduser().resolve()
    paths = ProviderPaths.from_root(out_root)
    ensure_dir(paths.manifests)
    projects = discover_projects(
        project_roots or default_project_roots(),
        max_projects=max_projects,
    )
    codex_sessions = discover_codex_sessions(
        sessions_root or default_codex_sessions_root(),
        max_sessions=max_sessions,
    )
    claude_sessions = discover_claude_sessions(
        claude_root or default_claude_sessions_root(),
        max_sessions=max_sessions,
    )
    sessions = sorted(
        [*codex_sessions, *claude_sessions],
        key=lambda item: item.get("updated_at", ""),
        reverse=True,
    )[:max_sessions]
    included_codex_sessions = sum(1 for record in sessions if record.get("provider") == "codex_session")
    included_claude_sessions = sum(1 for record in sessions if record.get("provider") == "claude_session")
    workflows = discover_workflow_docs(
        workflow_roots or default_workflow_roots(out_root),
        max_workflows=max_workflows,
    )
    write_jsonl(paths.projects_jsonl, projects)
    write_jsonl(paths.sessions_jsonl, sessions)
    write_jsonl(paths.workflows_jsonl, workflows)
    return {
        "provider_version": {
            "projects": PROJECT_PROVIDER_VERSION,
            "sessions": SESSION_PROVIDER_VERSION,
            "workflows": WORKFLOW_PROVIDER_VERSION,
        },
        "projects": len(projects),
        "sessions": len(sessions),
        "codex_sessions": included_codex_sessions,
        "claude_sessions": included_claude_sessions,
        "codex_sessions_discovered": len(codex_sessions),
        "claude_sessions_discovered": len(claude_sessions),
        "workflows": len(workflows),
        "projects_jsonl": str(paths.projects_jsonl),
        "sessions_jsonl": str(paths.sessions_jsonl),
        "workflows_jsonl": str(paths.workflows_jsonl),
    }


def refresh_projects(
    out_root: Path,
    *,
    project_roots: Iterable[Path] | None = None,
    max_projects: int = DEFAULT_MAX_PROJECTS,
) -> dict:
    out_root = out_root.expanduser().resolve()
    paths = ProviderPaths.from_root(out_root)
    ensure_dir(paths.manifests)
    projects = discover_projects(project_roots or default_project_roots(), max_projects=max_projects)
    write_jsonl(paths.projects_jsonl, projects)
    return {
        "provider_version": PROJECT_PROVIDER_VERSION,
        "projects": len(projects),
        "projects_jsonl": str(paths.projects_jsonl),
    }


def ensure_provider_manifests(out_root: Path) -> ProviderPaths:
    out_root = out_root.expanduser().resolve()
    paths = ProviderPaths.from_root(out_root)
    ensure_dir(paths.manifests)
    if not paths.projects_jsonl.exists():
        write_jsonl(paths.projects_jsonl, discover_projects(default_project_roots()))
    if not paths.sessions_jsonl.exists():
        sessions = sorted(
            [
                *discover_codex_sessions(default_codex_sessions_root()),
                *discover_claude_sessions(default_claude_sessions_root()),
            ],
            key=lambda item: item.get("updated_at", ""),
            reverse=True,
        )[:DEFAULT_MAX_SESSIONS]
        write_jsonl(paths.sessions_jsonl, sessions)
    if not paths.workflows_jsonl.exists():
        write_jsonl(paths.workflows_jsonl, discover_workflow_docs(default_workflow_roots(out_root)))
    return paths


def load_project_records(out_root: Path) -> list[dict]:
    return read_jsonl(ProviderPaths.from_root(out_root.expanduser().resolve()).projects_jsonl)


def load_session_records(out_root: Path) -> list[dict]:
    return read_jsonl(ProviderPaths.from_root(out_root.expanduser().resolve()).sessions_jsonl)


def load_workflow_records(out_root: Path) -> list[dict]:
    return read_jsonl(ProviderPaths.from_root(out_root.expanduser().resolve()).workflows_jsonl)


def discover_projects(
    roots: Iterable[Path],
    *,
    max_depth: int = DEFAULT_PROJECT_DEPTH,
    max_projects: int = DEFAULT_MAX_PROJECTS,
) -> list[dict]:
    projects: list[dict] = []
    seen: set[Path] = set()
    for root in roots:
        root = root.expanduser().resolve()
        if not root.exists():
            continue
        for project_path in iter_project_paths(root, max_depth=max_depth):
            resolved = project_path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            projects.append(project_record(resolved, root))
            if len(projects) >= max_projects:
                return sorted(projects, key=lambda item: item["path"])
    return sorted(projects, key=lambda item: item["path"])


def iter_project_paths(root: Path, *, max_depth: int) -> Iterator[Path]:
    for current_root, dirs, files in os.walk(root):
        current = Path(current_root)
        depth = len(current.relative_to(root).parts)
        dirs[:] = [
            name
            for name in dirs
            if name not in IGNORED_DIR_NAMES and not name.startswith(".")
        ]
        if depth >= max_depth:
            dirs[:] = []
        has_git = ".git" in dirs
        has_project_marker = any(name in PROJECT_MARKER_FILES for name in files)
        is_home_container = current == root and root == Path.home() and not has_git
        if (has_git or has_project_marker) and not is_home_container:
            yield current
            dirs[:] = []


def project_record(path: Path, root: Path) -> dict:
    readmes = [path / name for name in README_NAMES if (path / name).is_file()]
    marker_paths = [path / name for name in PROJECT_MARKER_FILES if (path / name).is_file()]
    text_parts = [path.name]
    for marker_path in marker_paths[:5]:
        text_parts.append(f"{marker_path.name}: {read_small_text(marker_path, 1600)}")
    text = "\n\n".join(part for part in text_parts if part).strip()
    return {
        "provider": "git_project",
        "provider_version": PROJECT_PROVIDER_VERSION,
        "source_id": f"project:{stable_id(str(path))}",
        "project_id": stable_id(str(path)),
        "name": path.name,
        "path": str(path),
        "relative_path": relative_or_name(path, root),
        "root": str(root),
        "readme_paths": [str(item) for item in readmes],
        "marker_paths": [str(item) for item in marker_paths],
        "has_git": (path / ".git").exists(),
        "mtime": path.stat().st_mtime if path.exists() else 0,
        "text": snippet(text, MAX_PROVIDER_TEXT_CHARS),
    }


def discover_codex_sessions(
    sessions_root: Path,
    *,
    max_sessions: int = DEFAULT_MAX_SESSIONS,
) -> list[dict]:
    sessions_root = sessions_root.expanduser().resolve()
    if not sessions_root.exists():
        return []
    session_index = load_session_index(codex_session_index_path(sessions_root))
    files = sorted(
        (path for path in sessions_root.rglob("*.jsonl") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    records = []
    for path in files[:max_sessions]:
        record = session_record(path, sessions_root, session_index=session_index)
        if record:
            records.append(record)
    return sorted(records, key=lambda item: item.get("updated_at", ""), reverse=True)


def discover_claude_sessions(
    claude_root: Path,
    *,
    max_sessions: int = DEFAULT_MAX_SESSIONS,
) -> list[dict]:
    claude_root = claude_root.expanduser().resolve()
    if not claude_root.exists():
        return []
    files = sorted(
        (path for path in claude_root.rglob("*.jsonl") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    records = []
    for path in files[:max_sessions]:
        record = claude_session_record(path, claude_root)
        if record:
            records.append(record)
    return sorted(records, key=lambda item: item.get("updated_at", ""), reverse=True)


def discover_workflow_docs(
    roots: Iterable[Path],
    *,
    max_workflows: int = DEFAULT_MAX_WORKFLOWS,
) -> list[dict]:
    records: list[dict] = []
    seen: set[Path] = set()
    for root in roots:
        root = root.expanduser().resolve()
        if not root.exists():
            continue
        for path in iter_workflow_doc_paths(root):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            records.append(workflow_record(resolved, root))
            if len(records) >= max_workflows:
                return sorted(records, key=lambda item: item["path"])
    return sorted(records, key=lambda item: item["path"])


def iter_workflow_doc_paths(root: Path) -> Iterator[Path]:
    for name in WORKFLOW_DOC_NAMES:
        candidate = root / name
        if candidate.is_file():
            yield candidate
    docs = root / "docs"
    if docs.is_dir():
        for path in sorted(docs.rglob("*.md")):
            if path.is_file():
                yield path


def session_record(path: Path, sessions_root: Path, *, session_index: dict[str, dict[str, Any]] | None = None) -> dict | None:
    session_id = ""
    cwd = ""
    started_at = ""
    user_messages: list[str] = []
    assistant_messages: list[str] = []
    updated_at = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat()

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "session_meta":
                payload = event.get("payload") or {}
                session_id = str(payload.get("id") or session_id)
                cwd = str(payload.get("cwd") or cwd)
                started_at = str(payload.get("timestamp") or started_at)
            if event.get("timestamp"):
                updated_at = str(event["timestamp"])
            payload = event.get("payload")
            if isinstance(payload, dict) and payload.get("type") == "message":
                role = payload.get("role")
                text = clean_session_message(message_text(payload))
                if role == "user" and text:
                    user_messages.append(text)
                elif role == "assistant" and text:
                    assistant_messages.append(text)

    if not session_id:
        session_id = stable_id(str(path))
    index_record = (session_index or {}).get(session_id, {})
    thread_name = str(index_record.get("thread_name") or "")
    if index_record.get("updated_at"):
        updated_at = str(index_record["updated_at"])
    if not user_messages and not assistant_messages:
        return None
    text = "\n\n".join(
        [
            f"thread_name: {thread_name}",
            f"cwd: {cwd}",
            *[f"user: {message}" for message in user_messages[:6]],
            *[f"assistant: {message}" for message in assistant_messages[-3:]],
        ]
    )
    return {
        "provider": "codex_session",
        "provider_version": SESSION_PROVIDER_VERSION,
        "source_id": f"codex-session:{session_id}",
        "session_id": session_id,
        "thread_name": thread_name,
        "path": str(path),
        "relative_path": relative_or_name(path, sessions_root),
        "cwd": cwd,
        "started_at": started_at,
        "updated_at": updated_at,
        "first_user_message": snippet(user_messages[0], 1200) if user_messages else "",
        "last_user_message": snippet(user_messages[-1], 1200) if user_messages else "",
        "message_count": len(user_messages) + len(assistant_messages),
        "text": snippet(text, MAX_PROVIDER_TEXT_CHARS),
    }


def claude_session_record(path: Path, claude_root: Path) -> dict | None:
    session_id = ""
    cwd = ""
    started_at = ""
    thread_name = ""
    user_messages: list[str] = []
    assistant_messages: list[str] = []
    updated_at = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat()

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            session_id = str(event.get("sessionId") or session_id)
            cwd = str(event.get("cwd") or cwd)
            if event.get("timestamp"):
                updated_at = str(event["timestamp"])
                if not started_at:
                    started_at = str(event["timestamp"])
            if event.get("type") == "summary" and event.get("summary"):
                thread_name = str(event["summary"])
            role = str(event.get("type") or event.get("message", {}).get("role") or "")
            text = clean_session_message(claude_message_text(event))
            if role == "user" and text:
                user_messages.append(text)
            elif role == "assistant" and text:
                assistant_messages.append(text)

    if not session_id:
        session_id = path.stem
    if not thread_name and user_messages:
        thread_name = snippet(user_messages[0], 80)
    if not user_messages and not assistant_messages:
        return None
    text = "\n\n".join(
        [
            f"thread_name: {thread_name}",
            f"cwd: {cwd}",
            *[f"user: {message}" for message in user_messages[:6]],
            *[f"assistant: {message}" for message in assistant_messages[-3:]],
        ]
    )
    return {
        "provider": "claude_session",
        "provider_version": SESSION_PROVIDER_VERSION,
        "source_id": f"claude-session:{session_id}",
        "session_id": session_id,
        "thread_name": thread_name,
        "path": str(path),
        "relative_path": relative_or_name(path, claude_root),
        "cwd": cwd,
        "started_at": started_at,
        "updated_at": updated_at,
        "first_user_message": snippet(user_messages[0], 1200) if user_messages else "",
        "last_user_message": snippet(user_messages[-1], 1200) if user_messages else "",
        "message_count": len(user_messages) + len(assistant_messages),
        "text": snippet(text, MAX_PROVIDER_TEXT_CHARS),
    }


def workflow_record(path: Path, root: Path) -> dict:
    text = read_small_text(path, MAX_PROVIDER_TEXT_CHARS)
    title = workflow_title_for(path, text)
    return {
        "provider": "workflow_doc",
        "provider_version": WORKFLOW_PROVIDER_VERSION,
        "source_id": f"workflow:{stable_id(str(path))}",
        "workflow_id": stable_id(str(path)),
        "title": title,
        "path": str(path),
        "relative_path": relative_or_name(path, root),
        "root": str(root),
        "mtime": path.stat().st_mtime if path.exists() else 0,
        "text": snippet(text, MAX_PROVIDER_TEXT_CHARS),
    }


def message_text(payload: dict) -> str:
    content = payload.get("content")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if isinstance(item, dict):
            text = item.get("text") or item.get("input_text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts).strip()


def claude_message_text(event: dict) -> str:
    message = event.get("message")
    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = event.get("content")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and item.get("type") not in {"thinking", "tool_use", "tool_result"}:
            parts.append(text)
    return "\n".join(parts).strip()


def session_transcript_text(record: dict[str, Any], *, max_messages: int = DEFAULT_SESSION_TRANSCRIPT_MESSAGES) -> str:
    provider = str(record.get("provider") or "")
    path = Path(str(record.get("path") or ""))
    header = [
        "# Session Transcript Preview",
        "",
        f"provider: {provider}",
        f"source_id: {record.get('source_id') or ''}",
        f"session_id: {record.get('session_id') or ''}",
        f"thread_name: {record.get('thread_name') or ''}",
        f"path: {record.get('path') or ''}",
        f"relative_path: {record.get('relative_path') or ''}",
        f"cwd: {record.get('cwd') or ''}",
        f"updated_at: {record.get('updated_at') or ''}",
        f"message_count: {record.get('message_count') or ''}",
        "",
        "limits: tool calls, tool outputs, environment blocks, and AGENTS instructions are omitted; read_source may trim this preview.",
        "",
    ]
    if not path.exists() or not path.is_file():
        return "\n".join([*header, "session file is missing; provider card text follows.", "", str(record.get("text") or "")])

    if provider == "codex_session":
        messages = codex_session_messages(path)
    elif provider == "claude_session":
        messages = claude_session_messages(path)
    else:
        messages = []
    if not messages:
        return "\n".join([*header, "no readable user/assistant messages found; provider card text follows.", "", str(record.get("text") or "")])

    selected = messages[: max(1, max_messages)]
    lines = [*header, "## Messages"]
    for index, message in enumerate(selected, start=1):
        role = message.get("role") or "message"
        timestamp = message.get("timestamp") or ""
        lines.extend(["", f"### {index}. {role} {timestamp}".rstrip(), "", str(message.get("text") or "")])
    omitted = len(messages) - len(selected)
    if omitted > 0:
        lines.extend(["", f"omitted_messages: {omitted}"])
    return "\n".join(lines).strip()


def codex_session_messages(path: Path) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict) or payload.get("type") != "message":
                continue
            role = str(payload.get("role") or "")
            if role not in {"user", "assistant"}:
                continue
            text = clean_session_message(message_text(payload))
            if not text:
                continue
            messages.append({"role": role, "timestamp": str(event.get("timestamp") or ""), "text": text})
    return messages


def claude_session_messages(path: Path) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            role = str(event.get("type") or event.get("message", {}).get("role") or "")
            if role not in {"user", "assistant"}:
                continue
            text = clean_session_message(claude_message_text(event))
            if not text:
                continue
            messages.append({"role": role, "timestamp": str(event.get("timestamp") or ""), "text": text})
    return messages


def clean_session_message(text: str) -> str:
    text = re.sub(r"<environment_context>.*?</environment_context>", "", text, flags=re.DOTALL)
    text = re.sub(r"# AGENTS\.md instructions.*?</INSTRUCTIONS>", "", text, flags=re.DOTALL)
    text = re.sub(r"# Selected text:\s*", "", text)
    text = re.sub(r"## Selection \d+\s*", "", text)
    text = re.sub(r"## My request for Codex:\s*", "", text)
    return text.strip()


def workflow_title_for(path: Path, text: str) -> str:
    for line in text.splitlines()[:20]:
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or path.stem
    return path.stem


def default_project_roots() -> list[Path]:
    env_value = os.environ.get("AGENT_CONTEXT_PROJECT_ROOTS", "").strip()
    if env_value:
        return [Path(value) for value in env_value.split(os.pathsep) if value.strip()]
    return [Path.home()]


def default_codex_sessions_root() -> Path:
    env_value = os.environ.get("AGENT_CONTEXT_SESSIONS_ROOT", "").strip()
    if env_value:
        return Path(env_value)
    return Path.home() / ".codex" / "sessions"


def default_sessions_root() -> Path:
    return default_codex_sessions_root()


def default_claude_sessions_root() -> Path:
    env_value = os.environ.get("AGENT_CONTEXT_CLAUDE_ROOT", "").strip()
    if env_value:
        return Path(env_value)
    return Path.home() / ".claude" / "projects"


def default_workflow_roots(out_root: Path) -> list[Path]:
    env_value = os.environ.get("AGENT_CONTEXT_WORKFLOW_ROOTS", "").strip()
    if env_value:
        return [Path(value) for value in env_value.split(os.pathsep) if value.strip()]
    return [out_root]


def codex_session_index_path(sessions_root: Path) -> Path:
    local_index = sessions_root / "session_index.jsonl"
    if local_index.exists():
        return local_index
    return sessions_root.parent / "session_index.jsonl"


def load_session_index(path: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for record in read_jsonl(path):
        session_id = str(record.get("id") or "")
        if session_id:
            records[session_id] = record
    return records


def read_small_text(path: Path, max_chars: int) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except OSError:
        return ""


def relative_or_name(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


def stable_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
