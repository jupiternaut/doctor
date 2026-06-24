from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .io import ensure_dir, write_jsonl, write_text


FILE_CATALOG_VERSION = "0.1"
DEFAULT_BATCH_SIZE = 2_000
USER_HOME = Path.home()


SOURCE_ZONE_WEIGHTS = {
    "user_projects": 1.0,
    "user_home": 0.9,
    "agent_history": 0.85,
    "user_library": 0.55,
    "applications": 0.5,
    "developer_tools": 0.45,
    "volumes": 0.4,
    "system_library": 0.3,
    "unix_system": 0.25,
    "dev_cache": 0.15,
    "trash": 0.05,
    "root": 0.2,
}


@dataclass(frozen=True)
class CatalogPaths:
    db: Path
    failures: Path
    report: Path

    @classmethod
    def from_root(cls, out_root: Path) -> "CatalogPaths":
        root = out_root.expanduser().resolve()
        return cls(
            db=root / "indexes" / "files.sqlite",
            failures=root / "manifests" / "file_catalog_failures.jsonl",
            report=root / "reports" / "file_catalog_report.md",
        )


def build_file_catalog(
    out_root: Path,
    scopes: list[Path],
    *,
    reset: bool = False,
    max_entries: int = 0,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    paths = CatalogPaths.from_root(out_root)
    ensure_dir(paths.db.parent)
    ensure_dir(paths.failures.parent)
    ensure_dir(paths.report.parent)
    if reset and paths.db.exists():
        paths.db.unlink()

    started_at = datetime.now().astimezone()
    conn = sqlite3.connect(paths.db)
    configure_connection(conn)
    ensure_schema(conn)

    failures: list[dict[str, Any]] = []
    stats = {
        "file_catalog_version": FILE_CATALOG_VERSION,
        "started_at": started_at.isoformat(),
        "finished_at": "",
        "out_root": str(out_root),
        "db_path": str(paths.db),
        "failures_path": str(paths.failures),
        "report_path": str(paths.report),
        "scopes": [],
        "entries_indexed": 0,
        "files_indexed": 0,
        "directories_indexed": 0,
        "symlinks_indexed": 0,
        "other_indexed": 0,
        "failure_count": 0,
        "max_entries": max(0, int(max_entries)),
        "truncated": False,
        "content_read": False,
        "content_hashing": "none",
    }

    batch: list[dict[str, Any]] = []
    try:
        for raw_scope in scopes:
            scope = raw_scope.expanduser().resolve()
            stats["scopes"].append(str(scope))
            if not scope.exists():
                failures.append(failure_record(scope, "missing", "scope does not exist"))
                continue
            for row_or_failure in walk_scope(scope, out_root=out_root):
                if "error" in row_or_failure:
                    failures.append(row_or_failure)
                    continue
                batch.append(row_or_failure)
                kind = row_or_failure["kind"]
                stats["entries_indexed"] += 1
                if kind == "file":
                    stats["files_indexed"] += 1
                elif kind == "directory":
                    stats["directories_indexed"] += 1
                elif kind == "symlink":
                    stats["symlinks_indexed"] += 1
                else:
                    stats["other_indexed"] += 1
                if len(batch) >= max(1, batch_size):
                    insert_entries(conn, batch)
                    batch.clear()
                if max_entries and stats["entries_indexed"] >= max_entries:
                    stats["truncated"] = True
                    break
            if stats["truncated"]:
                break
        if batch:
            insert_entries(conn, batch)
        conn.commit()
        optimize(conn)
    finally:
        conn.close()

    stats["failure_count"] = len(failures)
    stats["finished_at"] = datetime.now().astimezone().isoformat()
    write_jsonl(paths.failures, failures)
    write_text(paths.report, render_report(stats, failures))
    return stats


def walk_scope(scope: Path, *, out_root: Path) -> Iterable[dict[str, Any]]:
    stack = [(scope, 0)]
    output_root = out_root.resolve()
    scope_label = str(scope)
    while stack:
        path, depth = stack.pop()
        if should_skip_output_artifact(path, output_root):
            continue
        try:
            stat = path.lstat()
        except OSError as exc:
            yield failure_record(path, type(exc).__name__, str(exc))
            continue
        kind = file_kind(path, stat.st_mode)
        yield entry_record(path, stat, kind, depth, scope=scope_label)
        if kind != "directory":
            continue
        try:
            children = sorted(os.scandir(path), key=lambda item: item.name.lower(), reverse=True)
        except OSError as exc:
            yield failure_record(path, type(exc).__name__, str(exc))
            continue
        for child in children:
            stack.append((Path(child.path), depth + 1))


def configure_connection(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS entries (
          path TEXT PRIMARY KEY,
          path_key TEXT NOT NULL,
          parent TEXT NOT NULL,
          name TEXT NOT NULL,
          suffix TEXT NOT NULL,
          kind TEXT NOT NULL,
          size INTEGER NOT NULL,
          mtime_ns INTEGER NOT NULL,
          ctime_ns INTEGER NOT NULL,
          mode INTEGER NOT NULL,
          uid INTEGER NOT NULL,
          gid INTEGER NOT NULL,
          depth INTEGER NOT NULL,
          hidden INTEGER NOT NULL,
          scope TEXT NOT NULL DEFAULT '',
          source_zone TEXT NOT NULL DEFAULT 'root',
          source_weight REAL NOT NULL DEFAULT 0.2,
          indexed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_entries_kind ON entries(kind);
        CREATE INDEX IF NOT EXISTS idx_entries_suffix ON entries(suffix);
        CREATE INDEX IF NOT EXISTS idx_entries_parent ON entries(parent);
        CREATE INDEX IF NOT EXISTS idx_entries_mtime ON entries(mtime_ns);
        CREATE INDEX IF NOT EXISTS idx_entries_source_zone ON entries(source_zone);
        """
    )
    ensure_entries_columns(conn)
    conn.execute("DROP TABLE IF EXISTS entries_fts")
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts
          USING fts5(path, parent, name, suffix, kind, scope, source_zone, content='')
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        ("file_catalog_version", FILE_CATALOG_VERSION),
    )


def insert_entries(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO entries(
          path, path_key, parent, name, suffix, kind, size, mtime_ns, ctime_ns,
          mode, uid, gid, depth, hidden, scope, source_zone, source_weight, indexed_at
        ) VALUES (
          :path, :path_key, :parent, :name, :suffix, :kind, :size, :mtime_ns, :ctime_ns,
          :mode, :uid, :gid, :depth, :hidden, :scope, :source_zone, :source_weight, :indexed_at
        )
        """,
        rows,
    )
    conn.executemany(
        """
        INSERT OR REPLACE INTO entries_fts(rowid, path, parent, name, suffix, kind, scope, source_zone)
        VALUES (
          (SELECT rowid FROM entries WHERE path = :path),
          :path, :parent, :name, :suffix, :kind, :scope, :source_zone
        )
        """,
        rows,
    )
    conn.commit()


def optimize(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO entries_fts(entries_fts) VALUES ('optimize')")
    conn.execute("PRAGMA optimize")


def search_file_catalog(out_root: Path, query: str, *, limit: int = 20) -> dict[str, Any]:
    paths = CatalogPaths.from_root(out_root)
    if not paths.db.exists():
        raise FileNotFoundError(f"file catalog not found: {paths.db}")
    terms = [term.strip() for term in query.replace("/", " ").split() if term.strip()]
    match = " OR ".join(quote_fts_term(term) for term in terms) if terms else quote_fts_term(query)
    conn = sqlite3.connect(paths.db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT e.path, e.kind, e.size, e.mtime_ns, e.suffix,
                   e.scope, e.source_zone, e.source_weight,
                   bm25(entries_fts) AS rank
            FROM entries_fts
            JOIN entries e ON e.rowid = entries_fts.rowid
            WHERE entries_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (match, max(1, int(limit)) * 8),
        ).fetchall()
    finally:
        conn.close()
    results: list[dict[str, Any]] = []
    seen_logical_paths: set[str] = set()
    for row in rows:
        result = dict(row)
        logical_path = logical_source_path(result["path"])
        if logical_path in seen_logical_paths:
            continue
        seen_logical_paths.add(logical_path)
        result["logical_path"] = logical_path
        results.append(result)
        if len(results) >= max(1, int(limit)):
            break
    return {
        "file_catalog_version": FILE_CATALOG_VERSION,
        "query": query,
        "db_path": str(paths.db),
        "results": results,
    }


def ensure_entries_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(entries)").fetchall()}
    migrations = {
        "scope": "ALTER TABLE entries ADD COLUMN scope TEXT NOT NULL DEFAULT ''",
        "source_zone": "ALTER TABLE entries ADD COLUMN source_zone TEXT NOT NULL DEFAULT 'root'",
        "source_weight": "ALTER TABLE entries ADD COLUMN source_weight REAL NOT NULL DEFAULT 0.2",
    }
    for column, statement in migrations.items():
        if column not in columns:
            conn.execute(statement)


def entry_record(path: Path, stat: os.stat_result, kind: str, depth: int, *, scope: str) -> dict[str, Any]:
    now = datetime.now().astimezone().isoformat()
    suffix = path.suffix.lower()
    source_zone = classify_source_zone(path)
    return {
        "path": str(path),
        "path_key": hashlib.sha256(str(path).encode("utf-8", errors="surrogatepass")).hexdigest(),
        "parent": str(path.parent),
        "name": path.name,
        "suffix": suffix,
        "kind": kind,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "ctime_ns": int(stat.st_ctime_ns),
        "mode": int(stat.st_mode),
        "uid": int(stat.st_uid),
        "gid": int(stat.st_gid),
        "depth": int(depth),
        "hidden": 1 if path.name.startswith(".") else 0,
        "scope": scope,
        "source_zone": source_zone,
        "source_weight": SOURCE_ZONE_WEIGHTS.get(source_zone, SOURCE_ZONE_WEIGHTS["root"]),
        "indexed_at": now,
    }


def classify_source_zone(path: Path) -> str:
    text = logical_source_path(str(path))
    home = str(USER_HOME)
    if text == "/":
        return "root"
    if text.startswith(f"{home}/.Trash") or text == f"{home}/.Trash":
        return "trash"
    if text.startswith(("/.vol/", "/Volumes/")) or text == "/Volumes":
        return "volumes"
    if text.startswith(("/Applications/", "/System/Applications/")) or text in {"/Applications", "/System/Applications"}:
        return "applications"
    if text.startswith(("/System/", "/bin/", "/sbin/", "/usr/bin/", "/usr/sbin/", "/private/", "/var/")) or text in {
        "/System",
        "/bin",
        "/sbin",
        "/private",
        "/var",
    }:
        return "unix_system"
    if text.startswith(("/Library/",)) or text == "/Library":
        return "system_library"
    if text.startswith(("/opt/", "/usr/local/", "/usr/homebrew/")) or text in {"/opt", "/usr/local"}:
        return "developer_tools"
    if text.startswith(f"{home}/Library") or text == f"{home}/Library":
        return "user_library"
    dev_cache_roots = (
        ".cache",
        ".npm",
        ".cargo",
        ".rustup",
        ".gradle",
        ".docker",
        ".bun",
        ".yarn",
        ".venv",
    )
    if any(text.startswith(f"{home}/{name}") or text == f"{home}/{name}" for name in dev_cache_roots):
        return "dev_cache"
    agent_roots = (".codex", ".claude", ".agents", ".understand-anything", ".openclaw", ".hermes")
    if any(text.startswith(f"{home}/{name}") or text == f"{home}/{name}" for name in agent_roots):
        return "agent_history"
    project_roots = (
        "Code",
        "Dev",
        "_codex_repos",
        "apps",
        "plm",
        "drama",
        "mirror",
        "agent-context-system",
        "douyin-doctor",
        "gugu-bingmie-desktop",
        "gugu-roomlite-3d-asset-lab",
    )
    if any(text.startswith(f"{home}/{name}") or text == f"{home}/{name}" for name in project_roots):
        return "user_projects"
    if text.startswith(f"{home}/") or text == home:
        return "user_home"
    return "root"


def logical_source_path(path: str) -> str:
    data_prefix = "/System/Volumes/Data"
    if path == data_prefix:
        return "/"
    if path.startswith(f"{data_prefix}/"):
        return path[len(data_prefix) :]
    return path


def failure_record(path: Path, error: str, detail: str) -> dict[str, Any]:
    return {
        "timestamp": datetime.now().astimezone().isoformat(),
        "path": str(path),
        "error": error,
        "detail": detail,
    }


def file_kind(path: Path, mode: int) -> str:
    if os.path.islink(path):
        return "symlink"
    if os.path.isdir(path):
        return "directory"
    if os.path.isfile(path):
        return "file"
    return "other"


def should_skip_output_artifact(path: Path, out_root: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    skip_roots = (
        out_root / "indexes" / "files.sqlite",
        out_root / "indexes" / "files.sqlite-wal",
        out_root / "indexes" / "files.sqlite-shm",
    )
    return any(resolved == item for item in skip_roots)


def quote_fts_term(term: str) -> str:
    cleaned = term.replace('"', '""')
    return f'"{cleaned}"'


def render_report(stats: dict[str, Any], failures: list[dict[str, Any]]) -> str:
    lines = [
        "# File Catalog Report",
        "",
        f"- Started: `{stats['started_at']}`",
        f"- Finished: `{stats['finished_at']}`",
        f"- DB: `{stats['db_path']}`",
        f"- Failures: `{stats['failures_path']}`",
        f"- Content read: `{str(stats['content_read']).lower()}`",
        f"- Content hashing: `{stats['content_hashing']}`",
        f"- Truncated: `{str(stats['truncated']).lower()}`",
        "- Source labels: `scope`, `source_zone`, `source_weight`",
        "",
        "## Scopes",
        "",
    ]
    for scope in stats["scopes"]:
        lines.append(f"- `{scope}`")
    lines.extend(
        [
            "",
            "## Counts",
            "",
            f"- Entries indexed: `{stats['entries_indexed']}`",
            f"- Files indexed: `{stats['files_indexed']}`",
            f"- Directories indexed: `{stats['directories_indexed']}`",
            f"- Symlinks indexed: `{stats['symlinks_indexed']}`",
            f"- Other indexed: `{stats['other_indexed']}`",
            f"- Failures: `{len(failures)}`",
            "",
            "## Boundary",
            "",
            "- This is a metadata/path catalog, not a full-text document extraction run.",
            "- Source files are not modified.",
            "- File contents are not read, parsed, OCRed, or embedded by this command.",
            "- Use targeted Doctor ingestion or semantic refresh after this catalog identifies useful scopes.",
        ]
    )
    if failures[:10]:
        lines.extend(["", "## First Failures", ""])
        for failure in failures[:10]:
            lines.append(f"- `{failure['path']}`: {failure['error']} {failure['detail']}")
    lines.append("")
    return "\n".join(lines)
