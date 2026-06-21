from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .io import ensure_dir, read_jsonl, write_jsonl, write_text
from .pack import snippet


CODEBASE_MEMORY_PROVIDER_VERSION = "0.1"
CODEBASE_MEMORY_ENV = "AGENT_CONTEXT_CODEBASE_MEMORY_BIN"
DEFAULT_BINARY = "codebase-memory-mcp"
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_SEARCH_PROJECTS = 12
PSEUDO_REPO_NAME = "markitdown_extracted_repo"


@dataclass(frozen=True)
class CodebaseMemoryPaths:
    root: Path

    @classmethod
    def from_root(cls, root: Path) -> "CodebaseMemoryPaths":
        return cls(root=root.expanduser().resolve())

    @property
    def provider_dir(self) -> Path:
        return self.root / "providers" / "codebase_memory"

    @property
    def pseudo_repo(self) -> Path:
        return self.provider_dir / PSEUDO_REPO_NAME

    @property
    def pseudo_documents_dir(self) -> Path:
        return self.pseudo_repo / "documents"

    @property
    def documents_jsonl(self) -> Path:
        return self.root / "manifests" / "documents.jsonl"

    @property
    def sources_jsonl(self) -> Path:
        return self.root / "manifests" / "codebase_memory_sources.jsonl"

    @property
    def latest_json(self) -> Path:
        return self.root / "reports" / "codebase-memory-latest.json"

    @property
    def latest_md(self) -> Path:
        return self.root / "reports" / "codebase-memory-latest.md"


def codebase_memory_paths_for(out_root: Path) -> CodebaseMemoryPaths:
    return CodebaseMemoryPaths.from_root(out_root)


def find_codebase_memory_binary(binary: str | None = None) -> str | None:
    explicit = binary or os.environ.get(CODEBASE_MEMORY_ENV)
    if explicit:
        path = Path(explicit).expanduser()
        if path.exists():
            return str(path)
        found = shutil.which(explicit)
        return found
    return shutil.which(DEFAULT_BINARY)


def build_codebase_memory_index(
    out_root: Path,
    *,
    repo_paths: Iterable[Path] | None = None,
    binary: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    paths = CodebaseMemoryPaths.from_root(out_root)
    pseudo = build_markdown_pseudo_repo(out_root)
    resolved_binary = find_codebase_memory_binary(binary)
    repo_targets = [paths.pseudo_repo]
    for repo_path in repo_paths or []:
        resolved = repo_path.expanduser().resolve()
        if resolved not in repo_targets:
            repo_targets.append(resolved)

    index_results: list[dict[str, Any]] = []
    if resolved_binary:
        for repo_target in repo_targets:
            index_results.append(
                call_codebase_memory_tool(
                    resolved_binary,
                    "index_repository",
                    {"repo_path": str(repo_target), "mode": "fast"},
                    timeout_seconds=timeout_seconds,
                )
            )

    projects = list_codebase_memory_projects(resolved_binary, timeout_seconds=timeout_seconds) if resolved_binary else []
    report = {
        "codebase_memory_provider_version": CODEBASE_MEMORY_PROVIDER_VERSION,
        "created_at": datetime.now().astimezone().isoformat(),
        "status": status_for_index_report(resolved_binary, index_results),
        "binary": resolved_binary or "",
        "pseudo_repo_path": str(paths.pseudo_repo),
        "sources_jsonl": str(paths.sources_jsonl),
        "pseudo_repo": pseudo,
        "indexed_repositories": [str(path) for path in repo_targets],
        "index_results": index_results,
        "projects": projects,
        "latest_json_path": str(paths.latest_json),
        "latest_md_path": str(paths.latest_md),
    }
    write_text(paths.latest_json, json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(paths.latest_md, render_codebase_memory_report(report))
    return report


def status_for_index_report(binary: str | None, index_results: list[dict[str, Any]]) -> str:
    if not binary:
        return "pseudo_repo_ready_binary_missing"
    if not index_results:
        return "binary_available_no_index_targets"
    if any(result.get("ok") for result in index_results):
        return "indexed"
    return "index_failed"


def build_markdown_pseudo_repo(out_root: Path) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    paths = CodebaseMemoryPaths.from_root(out_root)
    ensure_dir(paths.provider_dir)
    if paths.pseudo_repo.exists():
        if not is_relative_to(paths.pseudo_repo, paths.provider_dir):
            raise ValueError(f"refusing to replace pseudo repo outside provider dir: {paths.pseudo_repo}")
        shutil.rmtree(paths.pseudo_repo)
    ensure_dir(paths.pseudo_documents_dir)

    records = []
    for document in read_jsonl(paths.documents_jsonl):
        extracted_path_value = document.get("extracted_md_path")
        if not extracted_path_value:
            continue
        extracted_path = Path(str(extracted_path_value)).expanduser()
        if not extracted_path.exists() or not extracted_path.is_file():
            continue
        try:
            text = extracted_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        target = pseudo_target_for_document(paths.pseudo_documents_dir, document)
        pseudo_text = render_pseudo_markdown(document, extracted_path, text)
        write_text(target, pseudo_text)
        records.append(source_mapping_record(document, extracted_path, target, text))

    write_text(paths.pseudo_repo / "README.md", render_pseudo_repo_readme(records))
    write_text(paths.pseudo_repo / ".gitignore", ".codebase-memory/\n*.db\n")
    write_jsonl(paths.sources_jsonl, records)
    return {
        "status": "ready",
        "documents": len(records),
        "pseudo_repo_path": str(paths.pseudo_repo),
        "sources_jsonl": str(paths.sources_jsonl),
    }


def pseudo_target_for_document(documents_dir: Path, document: dict[str, Any]) -> Path:
    digest = str(document.get("sha256") or document.get("doc_id") or "")
    digest = digest.replace("sha256:", "")
    if not re.fullmatch(r"[a-fA-F0-9]{16,64}", digest):
        digest = hashlib.sha256(str(document.get("path") or document).encode("utf-8")).hexdigest()
    stem = safe_slug(Path(str(document.get("relative_path") or document.get("path") or digest)).stem)
    filename = f"{digest[:16]}-{stem or 'document'}.md"
    return documents_dir / digest[:2] / filename


def safe_slug(value: str, max_chars: int = 80) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.\-\u4e00-\u9fff]+", "-", value).strip("-.")
    return cleaned[:max_chars]


def render_pseudo_markdown(document: dict[str, Any], extracted_path: Path, text: str) -> str:
    title = document.get("relative_path") or Path(str(document.get("path") or "document")).name
    header = [
        "---",
        "doctor_generated: true",
        f"doctor_doc_id: {json.dumps(str(document.get('doc_id') or ''), ensure_ascii=False)}",
        f"doctor_sha256: {json.dumps(str(document.get('sha256') or ''), ensure_ascii=False)}",
        f"doctor_source_path: {json.dumps(str(document.get('path') or ''), ensure_ascii=False)}",
        f"doctor_relative_path: {json.dumps(str(document.get('relative_path') or ''), ensure_ascii=False)}",
        f"doctor_extracted_md_path: {json.dumps(str(extracted_path), ensure_ascii=False)}",
        f"doctor_parser: {json.dumps(str(document.get('parser') or ''), ensure_ascii=False)}",
        f"doctor_policy: {json.dumps(str(document.get('policy') or ''), ensure_ascii=False)}",
        f"doctor_status: {json.dumps(str(document.get('status') or ''), ensure_ascii=False)}",
        "---",
        "",
        f"# {title}",
        "",
        f"Source path: `{document.get('path') or ''}`",
        "",
    ]
    return "\n".join(header) + text.rstrip() + "\n"


def source_mapping_record(document: dict[str, Any], extracted_path: Path, pseudo_path: Path, text: str) -> dict[str, Any]:
    doc_id = str(document.get("doc_id") or document.get("sha256") or pseudo_path.stem)
    return {
        "provider": "codebase_memory",
        "source_id": f"codebase-memory:{doc_id}",
        "doc_id": doc_id,
        "path": str(document.get("path") or ""),
        "relative_path": str(document.get("relative_path") or ""),
        "pseudo_path": str(pseudo_path),
        "pseudo_relative_path": str(pseudo_path.relative_to(pseudo_path.parents[2])),
        "extracted_md_path": str(extracted_path),
        "sha256": str(document.get("sha256") or ""),
        "parser": str(document.get("parser") or ""),
        "policy": str(document.get("policy") or ""),
        "status": str(document.get("status") or ""),
        "text_chars": len(text),
    }


def render_pseudo_repo_readme(records: list[dict[str, Any]]) -> str:
    return "\n".join(
        [
            "# Doctor MarkItDown Extracted Pseudo Repo",
            "",
            "This directory is generated by Doctor from `extracted/*.md` files.",
            "It exists so unmodified `codebase-memory-mcp` can index document-derived Markdown as a repository.",
            "",
            f"- Generated documents: `{len(records)}`",
            "- Source mappings: `../../manifests/codebase_memory_sources.jsonl` from the Doctor output root.",
            "- Do not edit these files by hand; rebuild with `agent-context codebase-memory-index`.",
            "",
        ]
    )


def render_codebase_memory_report(report: dict[str, Any]) -> str:
    lines = [
        "# Codebase Memory Provider",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Binary: `{report.get('binary') or 'missing'}`",
        f"- Pseudo repo: `{report.get('pseudo_repo_path')}`",
        f"- Pseudo documents: `{report.get('pseudo_repo', {}).get('documents', 0)}`",
        f"- Indexed repositories: `{len(report.get('indexed_repositories') or [])}`",
        f"- Projects visible to codebase-memory: `{len(report.get('projects') or [])}`",
        "",
        "## Notes",
        "",
    ]
    if not report.get("binary"):
        lines.append("- `codebase-memory-mcp` is not installed or not on PATH. The pseudo repo was still generated.")
    elif report.get("status") != "indexed":
        lines.append("- The external index command did not complete cleanly. Inspect `index_results` in the JSON report.")
    else:
        lines.append("- Resolver can use this provider as an optional graph/text-search side channel.")
    lines.append("")
    return "\n".join(lines)


def search_codebase_memory(
    out_root: Path,
    query: str,
    *,
    limit: int = 12,
    binary: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    paths = CodebaseMemoryPaths.from_root(out_root)
    resolved_binary = find_codebase_memory_binary(binary)
    if not resolved_binary:
        return {
            "status": "missing_binary",
            "query": query,
            "sources": [],
            "error": f"{DEFAULT_BINARY} is not installed or not on PATH",
        }

    projects = preferred_projects(out_root, resolved_binary, timeout_seconds=timeout_seconds)
    if not projects:
        return {"status": "no_indexed_projects", "query": query, "sources": [], "projects": []}

    pattern, regex = pattern_for_query(query)
    raw_results = []
    errors = []
    per_project_limit = max(1, min(limit, 8))
    for project in projects[:DEFAULT_SEARCH_PROJECTS]:
        project_name = str(project.get("name") or "")
        if not project_name:
            continue
        payload = {
            "project": project_name,
            "pattern": pattern,
            "regex": regex,
            "mode": "full",
            "limit": per_project_limit,
            "context": 1,
        }
        result = call_codebase_memory_tool(
            resolved_binary,
            "search_code",
            payload,
            timeout_seconds=timeout_seconds,
        )
        if not result.get("ok"):
            errors.append({"project": project_name, "error": result.get("error") or result.get("stderr")})
            continue
        raw_results.append({"project": project, "result": result.get("data") or {}})

    sources = sources_from_search_results(out_root, raw_results, query, limit)
    return {
        "status": "ok" if sources else "no_matches",
        "query": query,
        "pattern": pattern,
        "regex": regex,
        "projects": projects[:DEFAULT_SEARCH_PROJECTS],
        "sources": sources,
        "errors": errors,
    }


def preferred_projects(out_root: Path, binary: str, *, timeout_seconds: int) -> list[dict[str, Any]]:
    paths = CodebaseMemoryPaths.from_root(out_root)
    indexed_targets = []
    if paths.latest_json.exists():
        try:
            report = json.loads(paths.latest_json.read_text(encoding="utf-8"))
            indexed_targets = [str(value) for value in report.get("indexed_repositories") or []]
        except (OSError, json.JSONDecodeError):
            indexed_targets = []
    projects = list_codebase_memory_projects(binary, timeout_seconds=timeout_seconds)
    if not indexed_targets:
        return projects
    by_path = {str(project.get("root_path") or ""): project for project in projects}
    preferred = [by_path[target] for target in indexed_targets if target in by_path]
    rest = [project for project in projects if project not in preferred]
    return preferred + rest


def list_codebase_memory_projects(binary: str | None, *, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> list[dict[str, Any]]:
    if not binary:
        return []
    result = call_codebase_memory_tool(binary, "list_projects", {}, timeout_seconds=timeout_seconds)
    data = result.get("data") if result.get("ok") else {}
    projects = data.get("projects") if isinstance(data, dict) else []
    if not isinstance(projects, list):
        return []
    return [project for project in projects if isinstance(project, dict)]


def call_codebase_memory_tool(
    binary: str,
    tool: str,
    args: dict[str, Any],
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    command = [binary, "cli", tool, json.dumps(args, ensure_ascii=False)]
    started_at = datetime.now().astimezone()
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(1, timeout_seconds),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "ok": False,
            "tool": tool,
            "args": args,
            "started_at": started_at.isoformat(),
            "error": f"{type(exc).__name__}: {exc}",
        }

    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    data = parse_tool_output(stdout)
    return {
        "ok": completed.returncode == 0,
        "tool": tool,
        "args": args,
        "returncode": completed.returncode,
        "started_at": started_at.isoformat(),
        "completed_at": datetime.now().astimezone().isoformat(),
        "stdout": stdout[:20_000],
        "stderr": stderr[:20_000],
        "data": data,
    }


def parse_tool_output(output: str) -> Any:
    if not output:
        return {}
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return {"text": output}
    if isinstance(data, dict) and isinstance(data.get("content"), list):
        content = data["content"]
        if content and isinstance(content[0], dict):
            text = content[0].get("text")
            if isinstance(text, str):
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"text": text}
    return data


def pattern_for_query(query: str) -> tuple[str, bool]:
    terms = search_terms_for_query(query)
    if not terms:
        return query, False
    if len(terms) == 1:
        return terms[0], False
    return "|".join(re.escape(term) for term in terms[:12]), True


def search_terms_for_query(query: str) -> list[str]:
    lower = query.lower()
    terms = [
        term
        for term in re.findall(r"[a-zA-Z0-9_\-]{3,}|[\u4e00-\u9fff]{2,}", lower)
        if term not in {"the", "and", "for", "with", "from", "this", "that"}
    ]
    if "推荐" in query or "recommend" in lower:
        terms.extend(["推荐", "recommendation", "recommender", "ranking", "rank", "feedback", "rerank"])
    if "context" in lower or "上下文" in query:
        terms.extend(["context", "resolver", "router", "上下文"])
    if "agent" in lower or "助手" in query:
        terms.extend(["agent", "assistant", "助手"])
    return list(dict.fromkeys(term for term in terms if term.strip()))


def sources_from_search_results(
    out_root: Path,
    raw_results: list[dict[str, Any]],
    query: str,
    limit: int,
) -> list[dict[str, Any]]:
    sources = []
    for project_result in raw_results:
        project = project_result["project"]
        data = project_result["result"]
        for item in result_items(data):
            source = source_from_codebase_memory_item(out_root, project, item, query)
            if source:
                sources.append(source)
    sources.sort(key=lambda source: (-float(source.get("score") or 0.0), str(source.get("path") or "")))
    return dedupe_sources(sources)[:limit]


def result_items(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    items = []
    for key in ("results", "raw_matches"):
        values = data.get(key)
        if isinstance(values, list):
            items.extend(value for value in values if isinstance(value, dict))
    return items


def source_from_codebase_memory_item(
    out_root: Path,
    project: dict[str, Any],
    item: dict[str, Any],
    query: str,
) -> dict[str, Any] | None:
    root_path = Path(str(project.get("root_path") or "")).expanduser()
    relative = str(item.get("file") or item.get("file_path") or "")
    if not relative:
        return None
    path = root_path / relative if root_path else Path(relative)
    line = int(item.get("start_line") or item.get("line") or 0)
    context = str(item.get("context") or item.get("source") or item.get("content") or "")
    text = context or f"{item.get('label', '')} {item.get('qualified_name', '')}".strip()
    project_name = str(project.get("name") or path.parent.name)
    source_hash = hashlib.sha256(f"{project_name}:{relative}:{line}:{query}".encode("utf-8")).hexdigest()[:16]
    source_id = f"codebase-memory:{project_name}:{relative}"
    return {
        "type": "codebase_memory",
        "source_id": source_id,
        "source_chunk_id": f"{source_id}:{line or source_hash}",
        "doc_id": source_id,
        "path": str(path),
        "relative_path": relative,
        "provider": "codebase_memory",
        "project_name": project_name,
        "project_path": str(root_path),
        "score": score_for_codebase_memory_item(item),
        "score_parts": {
            "codebase_memory": score_for_codebase_memory_item(item),
            "in_degree": int(item.get("in_degree") or 0),
            "out_degree": int(item.get("out_degree") or 0),
            "match_lines": len(item.get("match_lines") or []),
        },
        "snippet": snippet(text or relative, 520),
        "line": line,
        "source_group": "codebase_memory",
        "matched_queries": [query],
        "retrieval_query": query,
        "retrieval_channel": "codebase_memory_search_code",
    }


def score_for_codebase_memory_item(item: dict[str, Any]) -> float:
    graph_bonus = min(0.18, (int(item.get("in_degree") or 0) + int(item.get("out_degree") or 0)) * 0.015)
    match_bonus = min(0.14, len(item.get("match_lines") or []) * 0.02)
    raw_line = 0.05 if item.get("content") else 0.0
    return round(min(0.78, 0.42 + graph_bonus + match_bonus + raw_line), 6)


def dedupe_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    deduped = []
    for source in sources:
        key = (source.get("path"), source.get("line"), source.get("snippet"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(source)
    return deduped


def codebase_memory_status(out_root: Path) -> dict[str, Any]:
    paths = CodebaseMemoryPaths.from_root(out_root)
    binary = find_codebase_memory_binary()
    report = {}
    if paths.latest_json.exists():
        try:
            report = json.loads(paths.latest_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            report = {}
    if report.get("status") == "indexed" and binary:
        status = "indexed"
    elif paths.pseudo_repo.exists():
        status = "pseudo_repo_ready" if binary else "pseudo_repo_ready_binary_missing"
    elif binary:
        status = "binary_available"
    else:
        status = "missing"
    return {
        "status": status,
        "binary": binary or "",
        "pseudo_repo_path": str(paths.pseudo_repo),
        "sources_jsonl": str(paths.sources_jsonl),
        "report_json_path": str(paths.latest_json),
        "report_md_path": str(paths.latest_md),
        "projects": report.get("projects") or [],
    }


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
