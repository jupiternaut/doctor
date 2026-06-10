from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from agent_context.cli import main


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_SCOPE = ROOT / "fixtures" / "downloads_sample"
GOAL = "分析 Downloads 里哪些文件适合进入个人助手长期记忆"


def read_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def file_hashes(scope: Path) -> dict[str, str]:
    hashes = {}
    for path in sorted(scope.rglob("*")):
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            hashes[str(path.relative_to(scope))] = digest
    return hashes


def copy_fixture(tmp_path: Path) -> Path:
    scope = tmp_path / "downloads_sample"
    shutil.copytree(FIXTURE_SCOPE, scope, symlinks=True)
    return scope


def test_build_creates_manifests_report_and_context_pack(tmp_path: Path) -> None:
    scope = copy_fixture(tmp_path)
    out = tmp_path / "out"
    before_hashes = file_hashes(scope)

    assert main(["build", "--scope", str(scope), "--goal", GOAL, "--out", str(out)]) == 0

    assert before_hashes == file_hashes(scope)
    assert (out / "manifests" / "documents.jsonl").exists()
    assert (out / "manifests" / "chunks.jsonl").exists()
    assert (out / "manifests" / "failures.jsonl").exists()
    assert (out / "reports" / "downloads_ingestion_report.md").exists()

    documents = read_jsonl(out / "manifests" / "documents.jsonl")
    chunks = read_jsonl(out / "manifests" / "chunks.jsonl")
    failures = read_jsonl(out / "manifests" / "failures.jsonl")

    assert documents
    assert chunks
    assert failures
    assert any(record["relative_path"] == "notes.md" and record["extracted_md_path"] for record in documents)
    assert any(record["relative_path"] == "task-planner.skill" for record in documents)
    assert any(
        record["extension"] in {".pdf", ".docx", ".xlsx", ".pptx"} and record["parser"] in {"markitdown", "docling"}
        for record in documents
    )
    assert any(record["stage"] == "ingest" and record["recoverable"] for record in failures)

    archive = next(record for record in documents if record["relative_path"] == "archive.zip")
    assert archive["policy"] == "metadata_only"
    assert archive["parser"] == "metadata_only"
    assert archive["extracted_md_path"] is None

    extracted_files = list((out / "extracted").glob("*.md"))
    assert extracted_files

    packs = list((out / "packs").glob("*"))
    assert packs
    latest_pack = sorted(packs)[-1]
    context_md = latest_pack / "context.md"
    sources_jsonl = latest_pack / "sources.jsonl"
    manifest_json = latest_pack / "manifest.json"
    assert context_md.exists()
    assert sources_jsonl.exists()
    assert manifest_json.exists()

    context = context_md.read_text(encoding="utf-8")
    assert "# Must Know" in context
    assert "# Relevant Files" in context
    assert "# Source Quotes" in context
    assert "# Limitations" in context
    assert str(scope) in context

    manifest = json.loads(manifest_json.read_text(encoding="utf-8"))
    assert manifest["context_pack_version"] == "0.1"
    assert manifest["goal"] == GOAL


def test_second_run_skips_unchanged_files(tmp_path: Path) -> None:
    scope = copy_fixture(tmp_path)
    out = tmp_path / "out"

    assert main(["build", "--scope", str(scope), "--goal", GOAL, "--out", str(out)]) == 0
    assert main(["build", "--scope", str(scope), "--goal", GOAL, "--out", str(out)]) == 0

    documents = read_jsonl(out / "manifests" / "documents.jsonl")
    assert any(record["status"] == "skipped" for record in documents)
    assert any("unchanged; reused previous extraction" in record.get("warnings", []) for record in documents)
