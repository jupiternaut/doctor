from __future__ import annotations

import hashlib
import mimetypes
import multiprocessing
import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from . import __version__
from .io import ensure_dir, read_jsonl, write_jsonl, write_text
from .policies import ARCHIVE_EXTENSIONS, extension_for, policy_for

PARSER_VERSION = f"agent-context-v{__version__}"
TEXT_READ_LIMIT_BYTES = 2_000_000
EXTRACTED_TEXT_LIMIT_CHARS = 1_000_000
DOCUMENT_TIMEOUT_SECONDS = int(os.environ.get("AGENT_CONTEXT_PARSER_TIMEOUT_SECONDS", "45"))
CHUNK_SIZE = 2400
CHUNK_OVERLAP = 240


@dataclass(frozen=True)
class IngestPaths:
    root: Path
    extracted: Path
    manifests: Path
    reports: Path

    @classmethod
    def from_root(cls, root: Path) -> "IngestPaths":
        return cls(
            root=root,
            extracted=root / "extracted",
            manifests=root / "manifests",
            reports=root / "reports",
        )

    @property
    def documents_jsonl(self) -> Path:
        return self.manifests / "documents.jsonl"

    @property
    def chunks_jsonl(self) -> Path:
        return self.manifests / "chunks.jsonl"

    @property
    def failures_jsonl(self) -> Path:
        return self.manifests / "failures.jsonl"

    @property
    def report_md(self) -> Path:
        return self.reports / "downloads_ingestion_report.md"


def ingest_scope(scope: Path, out_root: Path) -> dict:
    scope = scope.expanduser().resolve()
    out_root = out_root.expanduser().resolve()
    paths = IngestPaths.from_root(out_root)
    for directory in (paths.extracted, paths.manifests, paths.reports):
        ensure_dir(directory)

    previous_docs = {record["path"]: record for record in read_jsonl(paths.documents_jsonl)}
    previous_chunks_by_doc: dict[tuple[str, str], list[dict]] = {}
    seen_previous_chunks: set[tuple[str, str, object, object]] = set()
    for chunk in read_jsonl(paths.chunks_jsonl):
        key = (chunk["doc_id"], chunk.get("path") or "")
        chunk_key = (chunk["doc_id"], chunk.get("path") or "", chunk.get("chunk_id"), chunk.get("chunk_index"))
        if chunk_key in seen_previous_chunks:
            continue
        seen_previous_chunks.add(chunk_key)
        previous_chunks_by_doc.setdefault(key, []).append(chunk)

    documents: list[dict] = []
    chunks: list[dict] = []
    failures: list[dict] = []

    for file_path in iter_scope_files(scope):
        try:
            record, record_chunks, record_failures = ingest_one(file_path, scope, paths, previous_docs, previous_chunks_by_doc)
            documents.append(record)
            chunks.extend(record_chunks)
            failures.extend(record_failures)
        except Exception as exc:  # Keep one bad file from stopping the scan.
            file_hash = best_effort_hash(file_path)
            failures.append(
                failure_record(
                    path=file_path,
                    sha256=file_hash,
                    stage="ingest",
                    parser="none",
                    exc=exc,
                    recoverable=True,
                )
            )

    documents.sort(key=lambda item: item["relative_path"])
    chunks.sort(key=lambda item: item["chunk_id"])
    failures.sort(key=lambda item: (item["path"], item["stage"], item["parser"]))

    write_jsonl(paths.documents_jsonl, documents)
    write_jsonl(paths.chunks_jsonl, chunks)
    write_jsonl(paths.failures_jsonl, failures)
    write_report(paths.report_md, scope, documents, chunks, failures)

    return {
        "scope": str(scope),
        "documents": len(documents),
        "chunks": len(chunks),
        "failures": len(failures),
        "documents_jsonl": str(paths.documents_jsonl),
        "chunks_jsonl": str(paths.chunks_jsonl),
        "failures_jsonl": str(paths.failures_jsonl),
        "report": str(paths.report_md),
    }


def iter_scope_files(scope: Path) -> Iterable[Path]:
    archive_dir_names = {".app", ".pkg"}
    for root, dirs, files in os.walk(scope):
        root_path = Path(root)

        package_dirs = [name for name in dirs if Path(name).suffix.lower() in archive_dir_names]
        for name in package_dirs:
            yield root_path / name
        dirs[:] = [name for name in dirs if name not in package_dirs]

        for name in files:
            yield root_path / name


def ingest_one(
    file_path: Path,
    scope: Path,
    paths: IngestPaths,
    previous_docs: dict[str, dict],
    previous_chunks_by_doc: dict[tuple[str, str], list[dict]],
) -> tuple[dict, list[dict], list[dict]]:
    stat = file_path.stat()
    extension = extension_for(file_path)
    policy = policy_for(file_path)
    file_hash = hash_path(file_path)
    doc_id = f"sha256:{file_hash}"
    relative_path = str(file_path.relative_to(scope))
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    mtime = datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat()

    base_record = {
        "doc_id": doc_id,
        "path": str(file_path),
        "relative_path": relative_path,
        "scope": str(scope),
        "size_bytes": stat.st_size,
        "mtime": mtime,
        "sha256": file_hash,
        "extension": extension,
        "mime": mime,
        "policy": policy.policy,
        "parser": policy.parser,
        "parser_version": PARSER_VERSION,
        "status": "ok",
        "extracted_md_path": None,
        "text_chars": 0,
        "chunk_count": 0,
        "warnings": [],
    }

    previous = previous_docs.get(str(file_path))
    if is_unchanged(previous, base_record):
        record = dict(previous)
        record["status"] = "skipped"
        record["warnings"] = sorted(set(record.get("warnings", []) + ["unchanged; reused previous extraction"]))
        return record, previous_chunks_by_doc.get((doc_id, str(file_path)), []), []

    if policy.policy == "metadata_only":
        record = dict(base_record)
        record["warnings"] = [policy.reason]
        return record, [], []

    try:
        text, parser, warnings = extract_text(file_path, policy.parser)
        extracted_path = paths.extracted / f"{file_hash}.md"
        extracted_text = format_extracted_markdown(file_path, file_hash, parser, text)
        write_text(extracted_path, extracted_text)
        record_chunks = chunk_text(doc_id, file_path, text)
        record = dict(base_record)
        record.update(
            {
                "parser": parser,
                "extracted_md_path": str(extracted_path),
                "text_chars": len(text),
                "chunk_count": len(record_chunks),
                "warnings": warnings,
            }
        )
        return record, record_chunks, []
    except Exception as exc:
        record = dict(base_record)
        record["status"] = "failed"
        record["warnings"] = [str(exc)[:500]]
        return record, [], [failure_record(file_path, file_hash, "extract", policy.parser, exc, True)]


def is_unchanged(previous: dict | None, current: dict) -> bool:
    if not previous:
        return False
    keys = ("path", "size_bytes", "mtime", "sha256", "parser_version")
    return all(previous.get(key) == current.get(key) for key in keys)


def hash_path(path: Path) -> str:
    if path.is_dir():
        stat = path.stat()
        payload = f"dir:{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def best_effort_hash(path: Path) -> str:
    try:
        return hash_path(path)
    except Exception:
        return ""


def extract_text(path: Path, parser: str) -> tuple[str, str, list[str]]:
    if parser == "direct_text":
        return read_text_file(path), "direct_text", []
    if parser == "markitdown":
        return convert_document_with_timeout(path)
    raise ValueError(f"unsupported parser: {parser}")


def read_text_file(path: Path) -> str:
    data = path.read_bytes()[: TEXT_READ_LIMIT_BYTES + 1]
    truncated = len(data) > TEXT_READ_LIMIT_BYTES
    if truncated:
        data = data[:TEXT_READ_LIMIT_BYTES]
    text = data.decode("utf-8", errors="replace")
    if truncated:
        text += "\n\n[agent-context: file truncated at 2000000 bytes]\n"
    return text


def convert_with_markitdown(path: Path) -> str:
    from markitdown import MarkItDown

    converter = MarkItDown(enable_plugins=False)
    result = converter.convert(str(path))
    return getattr(result, "text_content", "") or ""


def convert_with_docling(path: Path) -> str:
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    result = converter.convert(str(path))
    return result.document.export_to_markdown()


def convert_document_with_timeout(path: Path) -> tuple[str, str, list[str]]:
    context = multiprocessing.get_context("spawn")
    queue = context.Queue(maxsize=1)
    process = context.Process(target=_document_conversion_worker, args=(str(path), queue))
    process.start()
    process.join(DOCUMENT_TIMEOUT_SECONDS)
    if process.is_alive():
        process.terminate()
        process.join(5)
        raise TimeoutError(f"document conversion exceeded {DOCUMENT_TIMEOUT_SECONDS}s")
    if queue.empty():
        raise RuntimeError(f"document conversion exited with code {process.exitcode} and no result")

    result = queue.get()
    if not result["ok"]:
        raise RuntimeError(f"{result['error_type']}: {result['error']}")
    return result["text"], result["parser"], result["warnings"]


def _document_conversion_worker(path_text: str, queue: multiprocessing.Queue) -> None:
    path = Path(path_text)
    try:
        warnings: list[str] = []
        try:
            text = convert_with_markitdown(path)
            parser = "markitdown"
        except Exception as markitdown_error:
            try:
                text = convert_with_docling(path)
                parser = "docling"
                warnings.append(f"markitdown failed: {markitdown_error}")
            except Exception:
                raise markitdown_error

        if len(text) > EXTRACTED_TEXT_LIMIT_CHARS:
            text = text[:EXTRACTED_TEXT_LIMIT_CHARS]
            warnings.append(f"extracted text truncated at {EXTRACTED_TEXT_LIMIT_CHARS} chars")
        queue.put({"ok": True, "text": text, "parser": parser, "warnings": warnings})
    except BaseException as exc:
        queue.put({"ok": False, "error_type": exc.__class__.__name__, "error": str(exc)[:500]})


def format_extracted_markdown(path: Path, file_hash: str, parser: str, text: str) -> str:
    extracted_at = datetime.now().astimezone().isoformat()
    return "\n".join(
        [
            "---",
            f"source_path: {path}",
            f"sha256: {file_hash}",
            f"parser: {parser}",
            f"extracted_at: {extracted_at}",
            "---",
            "",
            text.strip(),
            "",
        ]
    )


def chunk_text(doc_id: str, path: Path, text: str) -> list[dict]:
    normalized = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if not normalized:
        return []

    chunks: list[dict] = []
    start = 0
    index = 1
    while start < len(normalized):
        end = min(start + CHUNK_SIZE, len(normalized))
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(
                {
                    "chunk_id": f"{doc_id}:{index:04d}",
                    "doc_id": doc_id,
                    "path": str(path),
                    "chunk_index": index,
                    "text": chunk,
                    "char_start": start,
                    "char_end": end,
                    "token_estimate": max(1, len(chunk) // 4),
                }
            )
            index += 1
        if end == len(normalized):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return chunks


def failure_record(path: Path, sha256: str, stage: str, parser: str, exc: Exception, recoverable: bool) -> dict:
    return {
        "path": str(path),
        "sha256": sha256,
        "stage": stage,
        "parser": parser,
        "error_type": exc.__class__.__name__,
        "error": str(exc)[:500],
        "recoverable": recoverable,
    }


def write_report(path: Path, scope: Path, documents: list[dict], chunks: list[dict], failures: list[dict]) -> None:
    status_counts = Counter(record["status"] for record in documents)
    policy_counts = Counter(record["policy"] for record in documents)
    extension_counts = Counter(record["extension"] or "[none]" for record in documents)
    lines = [
        "# Downloads Ingestion Report",
        "",
        f"- Scope: `{scope}`",
        f"- Generated at: `{datetime.now().astimezone().isoformat()}`",
        f"- Parser version: `{PARSER_VERSION}`",
        "",
        "## Summary",
        "",
        f"- Documents: {len(documents)}",
        f"- Chunks: {len(chunks)}",
        f"- Failures: {len(failures)}",
        f"- Status counts: {dict(sorted(status_counts.items()))}",
        f"- Policy counts: {dict(sorted(policy_counts.items()))}",
        "",
        "## Top Extensions",
        "",
    ]
    for extension, count in extension_counts.most_common(20):
        lines.append(f"- `{extension}`: {count}")

    lines.extend(["", "## Failures", ""])
    if failures:
        for failure in failures[:100]:
            lines.append(f"- `{failure['path']}`: {failure['parser']} {failure['error_type']} - {failure['error']}")
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "## Output Files",
            "",
            "- `manifests/documents.jsonl`",
            "- `manifests/chunks.jsonl`",
            "- `manifests/failures.jsonl`",
            "- `extracted/<file_hash>.md`",
            "",
        ]
    )
    write_text(path, "\n".join(lines))
