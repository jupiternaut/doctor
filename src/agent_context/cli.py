from __future__ import annotations

import argparse
import json
from pathlib import Path

from .ingest import ingest_scope, write_report, IngestPaths
from .io import read_jsonl
from .pack import build_context_pack


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-context")
    parser.add_argument("--out", dest="global_out", default=".", help="Output root. Defaults to the current directory.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest", help="Scan a scope and build extracted Markdown plus manifests.")
    ingest.add_argument("--scope", required=True, help="File or directory scope to scan.")
    ingest.add_argument("--out", default=None, help="Output root. Overrides global --out.")

    pack = subparsers.add_parser("pack", help="Build a hot context pack from existing manifests.")
    pack.add_argument("--scope", required=True, help="Original scan scope.")
    pack.add_argument("--goal", required=True, help="Task goal for ranking context.")
    pack.add_argument("--out", default=None, help="Output root. Overrides global --out.")

    report = subparsers.add_parser("report", help="Regenerate the ingestion report from manifests.")
    report.add_argument("--scope", required=True, help="Original scan scope.")
    report.add_argument("--out", default=None, help="Output root. Overrides global --out.")

    build = subparsers.add_parser("build", help="Run ingest, then generate a hot context pack.")
    build.add_argument("--scope", required=True, help="File or directory scope to scan.")
    build.add_argument("--goal", required=True, help="Task goal for ranking context.")
    build.add_argument("--out", default=None, help="Output root. Overrides global --out.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    out_root = Path(args.out or args.global_out).expanduser().resolve()

    if args.command == "ingest":
        result = ingest_scope(Path(args.scope), out_root)
    elif args.command == "pack":
        result = build_context_pack(Path(args.scope), out_root, args.goal)
    elif args.command == "report":
        result = regenerate_report(Path(args.scope), out_root)
    elif args.command == "build":
        ingest_result = ingest_scope(Path(args.scope), out_root)
        pack_result = build_context_pack(Path(args.scope), out_root, args.goal)
        result = {"ingest": ingest_result, "pack": pack_result}
    else:
        parser.error(f"unknown command: {args.command}")

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def regenerate_report(scope: Path, out_root: Path) -> dict:
    paths = IngestPaths.from_root(out_root)
    documents = read_jsonl(paths.documents_jsonl)
    chunks = read_jsonl(paths.chunks_jsonl)
    failures = read_jsonl(paths.failures_jsonl)
    write_report(paths.report_md, scope.expanduser().resolve(), documents, chunks, failures)
    return {
        "scope": str(scope.expanduser().resolve()),
        "documents": len(documents),
        "chunks": len(chunks),
        "failures": len(failures),
        "report": str(paths.report_md),
    }
