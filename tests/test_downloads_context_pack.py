from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

from agent_context.cli import main
from agent_context.mcp_server import mcp_read_source, mcp_record_feedback, mcp_search_context


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


def test_compare_creates_route_b_pack_and_report(tmp_path: Path) -> None:
    scope = copy_fixture(tmp_path)
    out = tmp_path / "out"

    assert main(["compare", "--scope", str(scope), "--goal", GOAL, "--out", str(out)]) == 0

    report = out / "reports" / "ab_comparison_report.md"
    assert report.exists()
    report_text = report.read_text(encoding="utf-8")
    assert "Route A: Chunk Pack" in report_text
    assert "Route B: Graph Context Map" in report_text

    route_b_packs = sorted((out / "packs").glob("*route-b-graph*"))
    assert route_b_packs
    route_b = route_b_packs[-1]
    context = (route_b / "context.md").read_text(encoding="utf-8")
    graph = json.loads((route_b / "context_graph.json").read_text(encoding="utf-8"))
    manifest = json.loads((route_b / "manifest.json").read_text(encoding="utf-8"))
    sources = read_jsonl(route_b / "sources.jsonl")

    assert "route: b_graph_context_map" in context
    assert "# Graph Summary" in context
    assert graph["kind"] == "agent-context-graph-lite"
    assert graph["nodes"]
    assert graph["edges"]
    assert manifest["route"] == "b_graph_context_map"
    assert sources


def test_arena_creates_three_candidates_and_feedback(tmp_path: Path) -> None:
    scope = copy_fixture(tmp_path)
    out = tmp_path / "out"

    assert main(["arena", "--scope", str(scope), "--goal", GOAL, "--out", str(out)]) == 0

    arena_dirs = sorted((out / "packs").glob("*arena*"))
    assert arena_dirs
    arena = arena_dirs[-1]
    slate_md = arena / "slate.md"
    slate_json = arena / "slate.json"
    slate_key = arena / "slate_key.json"

    assert slate_md.exists()
    assert slate_json.exists()
    assert slate_key.exists()

    slate = json.loads(slate_json.read_text(encoding="utf-8"))
    assert slate["arena_version"] == "0.1"
    assert len(slate["candidates"]) == 3

    routes = {candidate["route"] for candidate in slate["candidates"]}
    assert routes == {"a_chunk_pack", "b_graph_context_map", "c_explore_diversity"}

    for index in range(1, 4):
        candidate = arena / f"candidate-{index}"
        assert (candidate / "context.md").exists()
        assert (candidate / "answer.md").exists()
        assert (candidate / "sources.jsonl").exists()
        assert (candidate / "route.json").exists()

    slate_text = slate_md.read_text(encoding="utf-8")
    assert "Candidate-1" in slate_text
    assert "Candidate-2" in slate_text
    assert "Candidate-3" in slate_text

    assert main(["feedback", "--slate", str(slate_json), "--winner", "candidate-1", "--reason", "test choice"]) == 0

    arena_feedback = read_jsonl(arena / "feedback.jsonl")
    global_feedback = read_jsonl(out / "feedback" / "arena_feedback.jsonl")
    assert arena_feedback
    assert global_feedback
    assert arena_feedback[-1]["winner"] == "candidate-1"
    assert arena_feedback[-1]["reason"] == "test choice"


def test_cold_index_and_query_create_rag_pack(tmp_path: Path) -> None:
    scope = copy_fixture(tmp_path)
    out = tmp_path / "out"

    assert main(["build", "--scope", str(scope), "--goal", GOAL, "--out", str(out), "--with-index"]) == 0

    db_path = out / "indexes" / "context.sqlite"
    assert db_path.exists()

    conn = sqlite3.connect(db_path)
    try:
        document_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    finally:
        conn.close()

    assert document_count >= 8
    assert chunk_count >= 1
    assert meta["index_version"] == "0.2"
    assert meta["embedding"].startswith("local-hash-vector-lite")

    assert main(["query", "--query", "task planner skill workflow", "--out", str(out), "--limit", "5"]) == 0

    query_dirs = sorted((out / "queries").glob("*rag*"))
    assert query_dirs
    query_dir = query_dirs[-1]
    context_md = query_dir / "context.md"
    sources_jsonl = query_dir / "sources.jsonl"
    manifest_json = query_dir / "manifest.json"

    assert context_md.exists()
    assert sources_jsonl.exists()
    assert manifest_json.exists()

    sources = read_jsonl(sources_jsonl)
    manifest = json.loads(manifest_json.read_text(encoding="utf-8"))
    context = context_md.read_text(encoding="utf-8")

    assert sources
    assert manifest["rag_version"] == "0.2"
    assert manifest["retrieval_mode"] == "hybrid_fts_vector_lite_path"
    assert "# RAG Query" in context
    assert "# Top Sources" in context
    assert any("task-planner.skill" in source["path"] or "notes.md" in source["path"] for source in sources)


def test_mcp_tools_search_read_source_and_feedback(tmp_path: Path) -> None:
    scope = copy_fixture(tmp_path)
    out = tmp_path / "out"

    assert main(["build", "--scope", str(scope), "--goal", GOAL, "--out", str(out), "--with-index"]) == 0

    search_result = mcp_search_context("task planner skill workflow", limit=5, out_root=str(out))
    assert search_result["mcp_version"] == "0.1"
    assert search_result["sources_included"] > 0
    assert search_result["top_sources"]
    assert Path(search_result["context_md_path"]).exists()

    top_source = search_result["top_sources"][0]
    source_result = mcp_read_source(top_source["path"], out_root=str(out), max_chars=800)
    assert source_result["mcp_version"] == "0.1"
    assert source_result["text"]
    assert len(source_result["text"]) <= 880

    feedback_result = mcp_record_feedback(
        query_id=search_result["query_id"],
        selected_source=top_source["path"],
        reason="fixture test",
        rating=1,
        out_root=str(out),
    )
    feedback_path = Path(feedback_result["feedback_path"])
    assert feedback_path.exists()
    feedback = read_jsonl(feedback_path)
    assert feedback[-1]["query_id"] == search_result["query_id"]
    assert feedback[-1]["selected_source"] == top_source["path"]
