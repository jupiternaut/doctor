from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from .io import ensure_dir, write_text


REPRODUCIBILITY_SNAPSHOT_VERSION = "0.1"
MAX_FILE_HASH_BYTES = 2_000_000


def run_reproducibility_snapshot(
    out_root: Path,
    *,
    roots: list[Path] | None = None,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    resolved_roots = [root.expanduser().resolve() for root in (roots or [out_root])]
    created_at = datetime.now().astimezone()
    report_id = created_at.strftime("%Y%m%d%H%M%S%f")
    reports_dir = ensure_dir(out_root / "reports")
    json_path = reports_dir / f"reproducibility-snapshot-{report_id}.json"
    md_path = reports_dir / f"reproducibility-snapshot-{report_id}.md"
    latest_json_path = reports_dir / "reproducibility-snapshot-latest.json"
    latest_md_path = reports_dir / "reproducibility-snapshot-latest.md"
    root_reports = [git_reproducibility_record(root) for root in resolved_roots]
    snapshot = {
        "reproducibility_snapshot_version": REPRODUCIBILITY_SNAPSHOT_VERSION,
        "created_at": created_at.isoformat(),
        "out_root": str(out_root),
        "roots": root_reports,
        "summary": summarize_snapshot_roots(root_reports),
        "json_path": str(json_path),
        "md_path": str(md_path),
        "latest_json_path": str(latest_json_path),
        "latest_md_path": str(latest_md_path),
        "policy": (
            "This snapshot records git metadata, status lines, diff stats, and small-file hashes. "
            "It is a reproducibility checkpoint for local dirty worktrees, not a commit."
        ),
    }
    text = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    write_text(json_path, text)
    write_text(latest_json_path, text)
    markdown = render_reproducibility_snapshot(snapshot)
    write_text(md_path, markdown)
    write_text(latest_md_path, markdown)
    return snapshot


def latest_reproducibility_snapshot_status(out_root: Path, roots: list[Path]) -> dict[str, Any]:
    path = out_root.expanduser().resolve() / "reports" / "reproducibility-snapshot-latest.json"
    if not path.exists():
        return {"exists": False, "path": str(path), "status": "missing", "covered_roots": []}
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"exists": True, "path": str(path), "status": "failed", "error": str(exc), "covered_roots": []}
    current_by_root = {str(root.expanduser().resolve()): git_status_identity(root.expanduser().resolve()) for root in roots}
    covered = []
    stale = []
    snapshot_roots = {}
    for record in snapshot.get("roots") or []:
        root = str(record.get("path") or "")
        snapshot_roots[root] = record
        current = current_by_root.get(root)
        if not current:
            continue
        if current.get("status_hash") == record.get("status_hash") and current.get("head") == record.get("head"):
            covered.append(root)
        else:
            stale.append(
                {
                    "path": root,
                    "snapshot_status_hash": record.get("status_hash"),
                    "current_status_hash": current.get("status_hash"),
                    "snapshot_head": record.get("head"),
                    "current_head": current.get("head"),
                }
            )
    missing = [root for root in current_by_root if root not in snapshot_roots]
    status = "ok" if len(covered) == len(current_by_root) and not stale and not missing else "stale"
    return {
        "exists": True,
        "path": str(path),
        "status": status,
        "created_at": snapshot.get("created_at") or "",
        "latest_md_path": snapshot.get("latest_md_path") or "",
        "covered_roots": covered,
        "stale_roots": stale,
        "missing_roots": missing,
        "roots_total": len(current_by_root),
    }


def git_reproducibility_record(root: Path) -> dict[str, Any]:
    root = root.expanduser().resolve()
    identity = git_status_identity(root)
    files = changed_file_records(root, identity.get("status_short", ""))
    return {
        **identity,
        "files": files,
        "file_records": len(files),
        "diff_stat": git_output(root, ["git", "diff", "--stat", "--"]),
        "cached_diff_stat": git_output(root, ["git", "diff", "--cached", "--stat", "--"]),
    }


def git_status_identity(root: Path) -> dict[str, Any]:
    if not root.exists():
        return {
            "path": str(root),
            "exists": False,
            "is_repo": False,
            "branch": "",
            "head": "",
            "dirty": False,
            "dirty_count": 0,
            "status_short": "",
            "status_hash": "",
        }
    is_repo = git_ok(root, ["git", "rev-parse", "--is-inside-work-tree"])
    branch = git_output(root, ["git", "rev-parse", "--abbrev-ref", "HEAD"]) if is_repo else ""
    head = git_output(root, ["git", "rev-parse", "HEAD"]) if is_repo else ""
    status_short = git_output(root, ["git", "status", "--short"]) if is_repo else ""
    status_hash = stable_hash({"path": str(root), "branch": branch, "head": head, "status_short": status_short})
    return {
        "path": str(root),
        "exists": True,
        "is_repo": is_repo,
        "branch": branch,
        "head": head,
        "dirty": bool(status_short.strip()),
        "dirty_count": len([line for line in status_short.splitlines() if line.strip()]),
        "status_short": status_short,
        "status_hash": status_hash,
    }


def changed_file_records(root: Path, status_short: str) -> list[dict[str, Any]]:
    records = []
    for line in status_short.splitlines():
        if not line.strip():
            continue
        status, raw_path = parse_status_line(line)
        if " -> " in raw_path:
            raw_path = raw_path.split(" -> ", 1)[1].strip()
        relative_path = raw_path.strip('"')
        path = root / relative_path
        record: dict[str, Any] = {
            "status": status,
            "relative_path": relative_path,
            "path": str(path),
        }
        if path.is_dir():
            record["kind"] = "directory"
            record["sha256"] = ""
            record["size_bytes"] = 0
        elif path.exists() and path.is_file():
            size = path.stat().st_size
            record["kind"] = "file"
            record["size_bytes"] = size
            record["sha256"] = sha256_file(path) if size <= MAX_FILE_HASH_BYTES else ""
            record["hash_skipped_reason"] = "" if record["sha256"] else f"file larger than {MAX_FILE_HASH_BYTES} bytes"
        else:
            record["kind"] = "missing"
            record["sha256"] = ""
            record["size_bytes"] = 0
        records.append(record)
    return records


def parse_status_line(line: str) -> tuple[str, str]:
    if line.startswith("?? "):
        return "??", line[3:].strip()
    if len(line) >= 3 and line[2] == " ":
        return line[:2], line[3:].strip()
    parts = line.split(maxsplit=1)
    if len(parts) == 2:
        return parts[0], parts[1].strip()
    return line.strip(), ""


def summarize_snapshot_roots(root_reports: list[dict[str, Any]]) -> dict[str, Any]:
    dirty = [root for root in root_reports if root.get("dirty")]
    return {
        "roots": len(root_reports),
        "repos": sum(1 for root in root_reports if root.get("is_repo")),
        "dirty_roots": len(dirty),
        "dirty_files": sum(int(root.get("dirty_count") or 0) for root in root_reports),
        "status": "ok",
    }


def render_reproducibility_snapshot(snapshot: dict[str, Any]) -> str:
    summary = snapshot.get("summary") or {}
    lines = [
        "# Reproducibility Snapshot",
        "",
        f"- Created at: `{snapshot.get('created_at')}`",
        f"- Out root: `{snapshot.get('out_root')}`",
        f"- Roots: `{summary.get('roots', 0)}`",
        f"- Dirty roots: `{summary.get('dirty_roots', 0)}`",
        f"- Dirty files: `{summary.get('dirty_files', 0)}`",
        f"- Policy: {snapshot.get('policy')}",
        "",
        "## Roots",
        "",
        "| Root | Branch | HEAD | Dirty | Files | Status Hash |",
        "| --- | --- | --- | ---: | ---: | --- |",
    ]
    for root in snapshot.get("roots") or []:
        head = str(root.get("head") or "")
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{root.get('path')}`",
                    f"`{root.get('branch')}`",
                    f"`{head[:12]}`",
                    str(root.get("dirty")),
                    str(root.get("dirty_count", 0)),
                    f"`{root.get('status_hash')}`",
                ]
            )
            + " |"
        )
    lines.extend(["", "## Changed Files", ""])
    for root in snapshot.get("roots") or []:
        lines.append(f"### `{root.get('path')}`")
        files = root.get("files") or []
        if not files:
            lines.append("- clean")
            continue
        for item in files[:200]:
            digest = item.get("sha256") or item.get("hash_skipped_reason") or item.get("kind")
            lines.append(f"- `{item.get('status')}` `{item.get('relative_path')}` `{digest}`")
        if len(files) > 200:
            lines.append(f"- ... {len(files) - 200} more file(s)")
    lines.append("")
    return "\n".join(lines)


def git_ok(root: Path, cmd: list[str]) -> bool:
    return subprocess.run(cmd, cwd=root, text=True, capture_output=True, check=False).returncode == 0


def git_output(root: Path, cmd: list[str]) -> str:
    result = subprocess.run(cmd, cwd=root, text=True, capture_output=True, check=False)
    return result.stdout.strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
