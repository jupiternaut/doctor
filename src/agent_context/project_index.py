from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator

from .cold_index import (
    create_schema,
    insert_chunks,
    insert_documents,
    insert_failures,
    read_meta,
    render_sources,
    reset_sqlite_database,
    retrieve_candidates,
    write_meta,
)
from .ingest import chunk_text
from .io import ensure_dir, read_jsonl, write_jsonl
from .providers import (
    ensure_provider_manifests,
    load_project_records,
    refresh_projects,
)
from .retrieval_backends import RetrievalConfig, backend_meta, default_retrieval_config

PROJECT_INDEX_VERSION = "0.1"
PROJECT_PARSER_VERSION = f"agent-context-project-index-v{PROJECT_INDEX_VERSION}"
DEFAULT_MAX_FILES_PER_PROJECT = 300
DEFAULT_MAX_INDEX_PROJECTS = 300
MAX_PROJECT_FILE_BYTES = 1_000_000

PROJECT_TEXT_EXTENSIONS = {
    ".css",
    ".go",
    ".html",
    ".js",
    ".jsx",
    ".json",
    ".md",
    ".markdown",
    ".py",
    ".rs",
    ".sh",
    ".skill",
    ".sql",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
PROJECT_IMPORTANT_FILES = {
    "AGENTS.md",
    "Cargo.toml",
    "go.mod",
    "package.json",
    "PROJECT_TASK_README.md",
    "pyproject.toml",
    "README.markdown",
    "README.md",
}
PROJECT_IGNORED_DIRS = {
    ".cache",
    ".git",
    ".gradle",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".turbo",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "vendor",
}


@dataclass(frozen=True)
class ProjectIndexPaths:
    root: Path
    manifests: Path
    indexes: Path

    @classmethod
    def from_root(cls, root: Path) -> "ProjectIndexPaths":
        return cls(root=root, manifests=root / "manifests", indexes=root / "indexes")

    @property
    def documents_jsonl(self) -> Path:
        return self.manifests / "project_documents.jsonl"

    @property
    def chunks_jsonl(self) -> Path:
        return self.manifests / "project_chunks.jsonl"

    @property
    def failures_jsonl(self) -> Path:
        return self.manifests / "project_failures.jsonl"

    @property
    def symbols_jsonl(self) -> Path:
        return self.manifests / "symbols.jsonl"

    @property
    def sqlite(self) -> Path:
        return self.indexes / "projects.sqlite"


def project_index_path_for(out_root: Path) -> Path:
    return ProjectIndexPaths.from_root(out_root.expanduser().resolve()).sqlite


def build_project_index(
    out_root: Path,
    *,
    project_roots: Iterable[Path] | None = None,
    max_projects: int = DEFAULT_MAX_INDEX_PROJECTS,
    max_files_per_project: int = DEFAULT_MAX_FILES_PER_PROJECT,
    retrieval_config: RetrievalConfig | None = None,
) -> dict:
    out_root = out_root.expanduser().resolve()
    retrieval_config = retrieval_config or default_retrieval_config()
    paths = ProjectIndexPaths.from_root(out_root)
    ensure_dir(paths.manifests)
    ensure_dir(paths.indexes)

    if project_roots:
        refresh_projects(out_root, project_roots=project_roots, max_projects=max_projects)
    else:
        ensure_provider_manifests(out_root)

    projects = load_project_records(out_root)[: max(1, max_projects)]
    documents: list[dict] = []
    chunks: list[dict] = []
    symbols: list[dict] = []
    failures: list[dict] = []

    for project in projects:
        project_path = Path(project["path"])
        for file_path in iter_project_files(project_path, max_files=max_files_per_project):
            try:
                document, document_chunks, document_symbols = index_project_file(file_path, project)
            except Exception as exc:
                failures.append(project_failure_record(file_path, project, exc))
                continue
            documents.append(document)
            chunks.extend(document_chunks)
            symbols.extend(document_symbols)

    documents.sort(key=lambda item: (item.get("project_name") or "", item["relative_path"]))
    chunks.sort(key=lambda item: item["chunk_id"])
    symbols.sort(key=lambda item: (item["project_name"], item["relative_path"], item["line"], item["symbol"]))
    failures.sort(key=lambda item: item["path"])

    write_jsonl(paths.documents_jsonl, documents)
    write_jsonl(paths.chunks_jsonl, chunks)
    write_jsonl(paths.symbols_jsonl, symbols)
    write_jsonl(paths.failures_jsonl, failures)
    build_project_sqlite(paths, documents, chunks, failures, retrieval_config=retrieval_config)

    return {
        "project_index_version": PROJECT_INDEX_VERSION,
        "projects": len(projects),
        "documents": len(documents),
        "chunks": len(chunks),
        "symbols": len(symbols),
        "failures": len(failures),
        "documents_jsonl": str(paths.documents_jsonl),
        "chunks_jsonl": str(paths.chunks_jsonl),
        "symbols_jsonl": str(paths.symbols_jsonl),
        "failures_jsonl": str(paths.failures_jsonl),
        "index_path": str(paths.sqlite),
        **backend_meta(retrieval_config),
    }


def build_project_sqlite(
    paths: ProjectIndexPaths,
    documents: list[dict],
    chunks: list[dict],
    failures: list[dict],
    *,
    retrieval_config: RetrievalConfig | None = None,
) -> None:
    retrieval_config = retrieval_config or default_retrieval_config()
    reset_sqlite_database(paths.sqlite)
    conn = sqlite3.connect(paths.sqlite)
    conn.row_factory = sqlite3.Row
    try:
        fts_enabled = create_schema(conn)
        insert_documents(conn, documents)
        insert_chunks(conn, chunks, documents, fts_enabled, retrieval_config=retrieval_config)
        insert_failures(conn, failures)
        indexed_documents = conn.execute("SELECT count(*) FROM documents").fetchone()[0]
        indexed_chunks = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        indexed_failures = conn.execute("SELECT count(*) FROM failures").fetchone()[0]
        write_meta(
            conn,
            {
                "index_version": PROJECT_INDEX_VERSION,
                "index_kind": "project_code",
                "built_at": datetime.now().astimezone().isoformat(),
                "documents": str(indexed_documents),
                "chunks": str(indexed_chunks),
                "failures": str(indexed_failures),
                "fts_enabled": "true" if fts_enabled else "false",
                **backend_meta(retrieval_config),
            },
        )
        conn.commit()
    finally:
        conn.close()


def search_project_index(
    out_root: Path,
    query: str,
    limit: int = 12,
    *,
    retrieval_config: RetrievalConfig | None = None,
) -> dict:
    out_root = out_root.expanduser().resolve()
    paths = ProjectIndexPaths.from_root(out_root)
    if not paths.sqlite.exists():
        raise FileNotFoundError(f"project index not found: {paths.sqlite}")

    conn = sqlite3.connect(paths.sqlite)
    conn.row_factory = sqlite3.Row
    try:
        meta = read_meta(conn)
        candidates = retrieve_candidates(conn, query, max(1, limit), meta, retrieval_config=retrieval_config)
        sources = annotate_project_sources(out_root, render_sources(candidates[:limit]))
    finally:
        conn.close()

    return {
        "query": query,
        "index_path": str(paths.sqlite),
        "retrieval_mode": "project_hybrid_fts_vector_lite_path",
        "limit": limit,
        "sources_included": len(sources),
        "sources": sources,
        "index_meta": meta,
    }


def annotate_project_sources(out_root: Path, sources: list[dict]) -> list[dict]:
    documents_by_path = {record["path"]: record for record in read_jsonl(ProjectIndexPaths.from_root(out_root).documents_jsonl)}
    for source in sources:
        document = documents_by_path.get(source["path"], {})
        source["source_group"] = "git_repositories"
        source["provider"] = "project_code_index"
        source["project_id"] = document.get("project_id")
        source["project_name"] = document.get("project_name")
        source["project_path"] = document.get("project_path")
    return sources


def iter_project_files(project_path: Path, *, max_files: int) -> Iterator[Path]:
    candidates: list[Path] = []
    for root, dirs, files in os.walk(project_path):
        root_path = Path(root)
        dirs[:] = sorted(
            name
            for name in dirs
            if name not in PROJECT_IGNORED_DIRS and not (name.startswith(".") and name != ".github")
        )
        for name in sorted(files):
            path = root_path / name
            if path.is_symlink() or not is_project_text_file(path):
                continue
            candidates.append(path)
    candidates.sort(key=lambda path: project_file_priority(project_path, path))
    yield from candidates[: max(1, max_files)]


def is_project_text_file(path: Path) -> bool:
    return path.name in PROJECT_IMPORTANT_FILES or path.suffix.lower() in PROJECT_TEXT_EXTENSIONS


def project_file_priority(project_path: Path, path: Path) -> tuple[int, int, str]:
    relative = path.relative_to(project_path)
    parts = relative.parts
    name = path.name
    if name in PROJECT_IMPORTANT_FILES:
        bucket = 0
    elif parts and parts[0] in {"docs", "doc"}:
        bucket = 1
    elif parts and parts[0] in {"src", "app", "apps", "packages", "crates"}:
        bucket = 2
    else:
        bucket = 3
    return (bucket, len(parts), str(relative))


def index_project_file(path: Path, project: dict) -> tuple[dict, list[dict], list[dict]]:
    stat = path.stat()
    if stat.st_size > MAX_PROJECT_FILE_BYTES:
        raise ValueError(f"project file too large for v0.6 index: {stat.st_size} bytes")

    data = path.read_bytes()
    file_hash = hashlib.sha256(data).hexdigest()
    text = data.decode("utf-8", errors="replace")
    project_path = Path(project["path"])
    relative_path = str(path.relative_to(project_path))
    doc_id = f"project-file:{hashlib.sha256(str(path.resolve()).encode('utf-8')).hexdigest()}"
    extension = path.suffix.lower()
    document = {
        "doc_id": doc_id,
        "path": str(path),
        "relative_path": relative_path,
        "scope": str(project_path),
        "size_bytes": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
        "sha256": file_hash,
        "extension": extension,
        "mime": mimetypes.guess_type(path.name)[0] or "text/plain",
        "policy": "project_code_index",
        "parser": "direct_text",
        "parser_version": PROJECT_PARSER_VERSION,
        "status": "ok",
        "extracted_md_path": None,
        "text_chars": len(text),
        "chunk_count": 0,
        "provider": "project_code_index",
        "project_id": project.get("project_id"),
        "project_name": project.get("name"),
        "project_path": project.get("path"),
    }
    chunks = chunk_text(doc_id, path, text)
    for chunk in chunks:
        chunk["project_id"] = project.get("project_id")
        chunk["project_name"] = project.get("name")
        chunk["project_path"] = project.get("path")
        chunk["relative_path"] = relative_path
        chunk["provider"] = "project_code_index"
    document["chunk_count"] = len(chunks)
    symbols = extract_symbols(path, text, project, doc_id, relative_path)
    return document, chunks, symbols


def extract_symbols(path: Path, text: str, project: dict, doc_id: str, relative_path: str) -> list[dict]:
    patterns = symbol_patterns_for(path.suffix.lower())
    if not patterns:
        return []
    symbols = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in patterns:
            match = re.match(pattern, line)
            if not match:
                continue
            symbol = match.group(1)
            symbols.append(
                {
                    "symbol_id": f"symbol:{hashlib.sha256(f'{path}:{line_number}:{symbol}'.encode('utf-8')).hexdigest()}",
                    "symbol": symbol,
                    "kind": kind,
                    "line": line_number,
                    "path": str(path),
                    "relative_path": relative_path,
                    "doc_id": doc_id,
                    "project_id": project.get("project_id"),
                    "project_name": project.get("name"),
                    "project_path": project.get("path"),
                }
            )
            break
    return symbols


def symbol_patterns_for(extension: str) -> list[tuple[str, str]]:
    if extension == ".py":
        return [("class", r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)"), ("function", r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)")]
    if extension in {".js", ".jsx", ".ts", ".tsx"}:
        return [
            ("class", r"^\s*(?:export\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
            ("function", r"^\s*(?:export\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
            ("constant", r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
        ]
    if extension == ".rs":
        return [("function", r"^\s*(?:pub\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)"), ("struct", r"^\s*(?:pub\s+)?struct\s+([A-Za-z_][A-Za-z0-9_]*)")]
    if extension == ".go":
        return [("function", r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_][A-Za-z0-9_]*)")]
    if extension == ".swift":
        return [("class", r"^\s*(?:public\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)"), ("function", r"^\s*(?:public\s+)?func\s+([A-Za-z_][A-Za-z0-9_]*)")]
    return []


def project_failure_record(path: Path, project: dict, exc: Exception) -> dict:
    return {
        "path": str(path),
        "sha256": "",
        "stage": "project_index",
        "parser": "direct_text",
        "error_type": exc.__class__.__name__,
        "error": str(exc)[:500],
        "recoverable": True,
        "project_id": project.get("project_id"),
        "project_name": project.get("name"),
        "project_path": project.get("path"),
    }
