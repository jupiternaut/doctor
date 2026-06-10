from __future__ import annotations

import argparse
import json
from pathlib import Path

from .arena import build_arena, record_feedback
from .compare import compare_routes
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

    compare = subparsers.add_parser("compare", help="Run Route A and Route B context pack experiments.")
    compare.add_argument("--scope", required=True, help="File or directory scope to scan.")
    compare.add_argument("--goal", required=True, help="Task goal for ranking context.")
    compare.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    compare.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Reuse existing manifests instead of scanning the scope before comparing routes.",
    )

    arena = subparsers.add_parser("arena", help="Generate a three-candidate arena slate for user selection.")
    arena.add_argument("--scope", required=True, help="File or directory scope to scan.")
    arena.add_argument("--goal", required=True, help="Task goal for generating candidate answers.")
    arena.add_argument("--out", default=None, help="Output root. Overrides global --out.")
    arena.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Reuse existing manifests instead of scanning the scope before generating the arena slate.",
    )

    feedback = subparsers.add_parser("feedback", help="Record the user's arena candidate choice.")
    feedback.add_argument("--slate", required=True, help="Path to an arena slate.json file.")
    feedback.add_argument("--winner", required=True, help="Winning candidate id, for example candidate-2.")
    feedback.add_argument("--reason", default="", help="Optional free-text reason for the choice.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    out_root = Path(getattr(args, "out", None) or args.global_out).expanduser().resolve()

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
    elif args.command == "compare":
        result = compare_routes(Path(args.scope), out_root, args.goal, skip_ingest=args.skip_ingest)
    elif args.command == "arena":
        result = build_arena(Path(args.scope), out_root, args.goal, skip_ingest=args.skip_ingest)
    elif args.command == "feedback":
        result = record_feedback(Path(args.slate), args.winner, args.reason)
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
