from __future__ import annotations

import hashlib
import json
import shutil
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from agent_context.cli import main
from agent_context.access_policy import ConsentRequiredError, record_access_audit
from agent_context.mirror_ranker import record_pairwise_feedback, train_pairwise_ranker
from agent_context.mcp_server import (
    mcp_access_audit,
    mcp_access_policy,
    mcp_grant_access_consent,
    mcp_index_sessions,
    mcp_read_source,
    mcp_resolve_alternative_context,
    mcp_resolve_context,
)
from agent_context.resolver import build_resolution_plan, fuse_candidates
from agent_context.semantic_index import run_semantic_refresh


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


def build_indexed_fixture(tmp_path: Path) -> tuple[Path, Path]:
    scope = copy_fixture(tmp_path)
    out = tmp_path / "out"

    assert main(["build", "--scope", str(scope), "--goal", GOAL, "--out", str(out), "--with-index"]) == 0
    write_provider_manifests(out)

    return scope, out


def has_explanation(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in ("why", "reason", "rationale", "explanation", "explain", "rule")):
                if child:
                    return True
            if has_explanation(child):
                return True
    if isinstance(value, list):
        return any(has_explanation(child) for child in value)
    return False


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def write_provider_manifests(
    out: Path,
    projects: list[dict] | None = None,
    sessions: list[dict] | None = None,
    workflows: list[dict] | None = None,
) -> None:
    write_jsonl(out / "manifests" / "projects.jsonl", projects or [])
    write_jsonl(out / "manifests" / "sessions.jsonl", sessions or [])
    write_jsonl(out / "manifests" / "workflows.jsonl", workflows or [])


def make_project(root: Path, name: str, readme: str) -> Path:
    project = root / name
    (project / ".git").mkdir(parents=True)
    (project / "README.md").write_text(readme, encoding="utf-8")
    return project


def make_code_project(root: Path, name: str) -> Path:
    project = make_project(root, name, "Personal recommendation system with recall, ranking, feedback, and rerank.")
    (project / "src").mkdir()
    (project / "src" / "recommender.py").write_text(
        "\n".join(
            [
                "class PersonalRecommender:",
                "    def recall_candidates(self, user_profile):",
                "        return ['candidate-a', 'candidate-b']",
                "",
                "    def rank_items(self, candidates):",
                "        return sorted(candidates)",
                "",
                "def build_feedback_loop(events):",
                "    return {'rerank': events}",
            ]
        ),
        encoding="utf-8",
    )
    (project / "node_modules" / "ignored").mkdir(parents=True)
    (project / "node_modules" / "ignored" / "bad.py").write_text("def should_not_index(): pass", encoding="utf-8")
    return project


class KeywordSemanticBackend:
    backend_id = "fastembed"
    dimensions = 3
    model_name = "test-keyword-semantic"
    storage_format = "json_dense_float32"

    def embed_document(self, text: str) -> str:
        return json.dumps([1.0, 0.0, 0.0])

    def embed_documents(self, texts: list[str]) -> list[str]:
        return [self.embed_document(text) for text in texts]

    def score_rows(self, rows: dict[str, dict], query: str, limit: int) -> dict[str, float]:
        scores = {}
        for source_chunk_id, row in rows.items():
            text = row.get("text") or ""
            if "latent semantic candidate" in text:
                scores[source_chunk_id] = 1.0
        return dict(sorted(scores.items())[:limit])


def install_fake_hnswlib(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("hnswlib")

    class FakeIndex:
        def __init__(self, space: str, dim: int) -> None:
            self.space = space
            self.dim = dim
            self.vectors: list[list[float]] = []
            self.labels: list[int] = []

        def init_index(self, max_elements: int, ef_construction: int, M: int) -> None:
            self.max_elements = max_elements
            self.ef_construction = ef_construction
            self.m = M

        def add_items(self, vectors, labels) -> None:
            self.vectors = vectors.tolist() if hasattr(vectors, "tolist") else list(vectors)
            self.labels = labels.tolist() if hasattr(labels, "tolist") else list(labels)

        def set_ef(self, value: int) -> None:
            self.ef = value

        def save_index(self, path: str) -> None:
            Path(path).write_text(
                json.dumps({"vectors": self.vectors, "labels": self.labels}),
                encoding="utf-8",
            )

        def load_index(self, path: str, max_elements: int) -> None:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            self.vectors = payload["vectors"]
            self.labels = payload["labels"]
            self.max_elements = max_elements

        def knn_query(self, vectors, k: int):
            queries = vectors.tolist() if hasattr(vectors, "tolist") else list(vectors)
            query = queries[0]
            ranked = []
            for label, vector in zip(self.labels, self.vectors):
                score = sum(float(a) * float(b) for a, b in zip(query, vector))
                ranked.append((1.0 - score, label))
            ranked.sort(key=lambda item: (item[0], item[1]))
            return [[label for _, label in ranked[:k]]], [[distance for distance, _ in ranked[:k]]]

    module.Index = FakeIndex
    monkeypatch.setitem(sys.modules, "hnswlib", module)


def make_codex_session(codex_root: Path, session_id: str, thread_name: str, user_text: str) -> Path:
    sessions_root = codex_root / "sessions"
    session_path = sessions_root / "2026" / "06" / "15" / f"rollout-{session_id}.jsonl"
    session_path.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(
        codex_root / "session_index.jsonl",
        [{"id": session_id, "thread_name": thread_name, "updated_at": "2026-06-15T10:00:00Z"}],
    )
    write_jsonl(
        session_path,
        [
            {
                "timestamp": "2026-06-15T09:00:00Z",
                "type": "session_meta",
                "payload": {"id": session_id, "cwd": str(codex_root.parent / "workspace")},
            },
            {
                "timestamp": "2026-06-15T09:01:00Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": user_text}],
                },
            },
            {
                "timestamp": "2026-06-15T09:02:00Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "用 resolver 先找项目和长期记忆证据。"}],
                },
            },
        ],
    )
    return sessions_root


def make_claude_session(claude_root: Path, session_id: str, user_text: str) -> Path:
    session_path = claude_root / "projects" / "-tmp-workspace" / f"{session_id}.jsonl"
    session_path.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(
        session_path,
        [
            {
                "type": "user",
                "message": {"role": "user", "content": user_text},
                "timestamp": "2026-06-15T11:00:00Z",
                "cwd": str(claude_root.parent / "workspace"),
                "sessionId": session_id,
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Claude 会话里建议先整理 workflow provider 和 agent memory。"}],
                },
                "timestamp": "2026-06-15T11:02:00Z",
                "cwd": str(claude_root.parent / "workspace"),
                "sessionId": session_id,
            },
        ],
    )
    return claude_root / "projects"


def make_workflow_doc(root: Path, name: str, text: str) -> Path:
    workflow = root / "docs" / name
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(text, encoding="utf-8")
    return workflow


def assert_resolution_pack(result: dict) -> tuple[Path, Path, Path, Path]:
    context_md = Path(result["context_md_path"])
    sources_jsonl = Path(result["sources_jsonl_path"])
    manifest_json = Path(result["manifest_json_path"])
    resolution_plan_json = Path(result["resolution_plan_json_path"])

    assert context_md.exists()
    assert sources_jsonl.exists()
    assert manifest_json.exists()
    assert resolution_plan_json.exists()
    assert context_md.parent == sources_jsonl.parent == manifest_json.parent == resolution_plan_json.parent

    return context_md, sources_jsonl, manifest_json, resolution_plan_json


def test_resolve_cli_creates_resolution_pack(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    scope, out = build_indexed_fixture(tmp_path)
    capsys.readouterr()
    before_hashes = file_hashes(scope)

    assert main(["resolve", "--goal", GOAL, "--out", str(out), "--limit", "5"]) == 0

    result = json.loads(capsys.readouterr().out)
    context_md, sources_jsonl, manifest_json, resolution_plan_json = assert_resolution_pack(result)

    assert before_hashes == file_hashes(scope)
    assert context_md.name == "context.md"
    assert sources_jsonl.name == "sources.jsonl"
    assert manifest_json.name == "manifest.json"
    assert resolution_plan_json.name == "resolution_plan.json"

    manifest = json.loads(manifest_json.read_text(encoding="utf-8"))
    plan = json.loads(resolution_plan_json.read_text(encoding="utf-8"))
    sources = read_jsonl(sources_jsonl)
    context = context_md.read_text(encoding="utf-8")

    assert manifest["route"] == "rule_based_v0"
    assert manifest["goal"] == GOAL
    assert manifest["resolution_plan_json_path"] == str(resolution_plan_json)
    assert plan["route"] == "rule_based_v0"
    assert sources
    assert "# Task" in context
    assert "# Top Sources" in context
    assert all({"source_id", "path", "score", "score_parts", "why_selected"} <= set(source) for source in sources)
    assert all(source.get("evidence", {}).get("schema_version") == "0.1" for source in sources)
    assert {source["evidence"]["source_type"] for source in sources} <= {
        "code",
        "document",
        "session",
        "workflow",
        "project",
        "unknown",
    }


def test_resolve_task_ids_are_unique_for_fast_repeated_calls(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _scope, out = build_indexed_fixture(tmp_path)
    capsys.readouterr()

    assert main(["resolve", "--goal", GOAL, "--out", str(out), "--limit", "3"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(["resolve", "--goal", GOAL, "--out", str(out), "--limit", "3"]) == 0
    second = json.loads(capsys.readouterr().out)

    assert first["task_id"] != second["task_id"]
    assert Path(first["context_md_path"]).parent != Path(second["context_md_path"]).parent


def test_resolution_plan_is_rule_based_and_explainable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _scope, out = build_indexed_fixture(tmp_path)
    capsys.readouterr()

    plan = build_resolution_plan(out_root=out, goal=GOAL, limit=5)

    assert plan["route"] == "rule_based_v0"
    assert plan["intent"]
    assert plan["query_family"]
    assert isinstance(plan["entities"], list)
    assert isinstance(plan["selected_sources"], list)
    assert plan["selected_sources"]
    assert isinstance(plan["queries"], list)
    assert len(plan["queries"]) >= 2
    assert "constraints" in plan
    assert plan["refresh_plan"]
    assert has_explanation(plan)


def test_resolution_plan_respects_source_scope(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _scope, out = build_indexed_fixture(tmp_path)
    capsys.readouterr()

    plan = build_resolution_plan(out_root=out, goal=GOAL, limit=5, source_scope="downloads")

    assert plan["source_scope"] == "downloads"
    assert plan["selected_sources"] == ["downloads_documents"]


def test_resolver_fuses_multiple_queries_without_query_pack_spam(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _scope, out = build_indexed_fixture(tmp_path)
    capsys.readouterr()
    queries_dir = out / "queries"
    before_rag_dirs = sorted(queries_dir.glob("*rag*")) if queries_dir.exists() else []

    assert main(["resolve", "--goal", GOAL, "--out", str(out), "--limit", "8"]) == 0

    result = json.loads(capsys.readouterr().out)
    _context_md, sources_jsonl, _manifest_json, resolution_plan_json = assert_resolution_pack(result)
    plan = json.loads(resolution_plan_json.read_text(encoding="utf-8"))
    sources = read_jsonl(sources_jsonl)
    source_keys = [
        source.get("source_chunk_id") or source.get("source_id") or source["path"]
        for source in sources
    ]
    after_rag_dirs = sorted(queries_dir.glob("*rag*")) if queries_dir.exists() else []

    assert len(plan["queries"]) >= 2
    assert len(source_keys) == len(set(source_keys))
    assert all(source.get("why_selected") for source in sources)
    assert after_rag_dirs == before_rag_dirs


def test_resolution_plan_does_not_inject_recommendation_query_for_unrelated_project_goal(
    tmp_path: Path,
) -> None:
    plan = build_resolution_plan(
        out_root=tmp_path / "out",
        goal="我codex的项目和这个人的简历比起来有什么区别",
        limit=5,
    )

    joined_queries = "\n".join(plan["queries"]).lower()
    assert plan["intent"] == "project_code"
    assert "recommendation system local project architecture" not in joined_queries
    assert "codex 会话 历史" not in joined_queries
    assert "doctor codex++ warp" in joined_queries


def test_resolution_plan_keeps_codex_session_route_when_history_is_explicit(
    tmp_path: Path,
) -> None:
    plan = build_resolution_plan(
        out_root=tmp_path / "out",
        goal="查看 Codex 会话历史里关于 Doctor 的讨论",
        limit=5,
    )

    joined_queries = "\n".join(plan["queries"]).lower()
    assert plan["intent"] in {"agent_history", "mixed"}
    assert "codex 会话 历史" in joined_queries


def test_resolution_plan_keeps_recommendation_query_for_recommendation_goal(
    tmp_path: Path,
) -> None:
    plan = build_resolution_plan(
        out_root=tmp_path / "out",
        goal="告诉我本地所有项目里如何构建个人推荐系统",
        limit=5,
    )

    joined_queries = "\n".join(plan["queries"]).lower()
    assert "recommendation system local project architecture" in joined_queries


def test_mcp_resolve_context_returns_top_sources(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _scope, out = build_indexed_fixture(tmp_path)
    capsys.readouterr()

    result = mcp_resolve_context(GOAL, limit=5, out_root=str(out))

    context_md, sources_jsonl, manifest_json, resolution_plan_json = assert_resolution_pack(result)
    top_sources = result["top_sources"]
    source_paths = {source["path"] for source in read_jsonl(sources_jsonl)}

    assert result["mcp_version"] == "0.1"
    assert result["route"] == "rule_based_v0"
    assert result["sources_included"] == len(read_jsonl(sources_jsonl))
    assert top_sources
    assert len(top_sources) <= 5
    assert all({"score", "path", "source_id", "why_selected", "snippet"} <= set(source) for source in top_sources)
    assert all(source["path"] in source_paths for source in top_sources)
    assert json.loads(manifest_json.read_text(encoding="utf-8"))["resolution_plan_json_path"] == str(resolution_plan_json)
    assert context_md.read_text(encoding="utf-8")


def test_providers_cli_writes_project_and_session_cards(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    projects_root = tmp_path / "projects"
    make_project(projects_root, "local-recommender", "个人推荐系统 architecture with ranking and recall.")
    make_project(projects_root / ".bun" / "install" / "cache", "cached-package", "dependency cache should not be a user project")
    sessions_root = make_codex_session(
        tmp_path / "codex",
        "session-1",
        "个人推荐系统讨论",
        "之前聊过如何构建个人推荐系统和本地 agent context resolver。",
    )
    claude_root = make_claude_session(
        tmp_path / "claude",
        "claude-session-1",
        "Claude 会话里讨论 workflow provider 和 agent memory。",
    )
    workflow_doc = make_workflow_doc(
        tmp_path / "workflows",
        "AGENT_CONTEXT_WORKFLOW.md",
        "# Agent Context Workflow\n\nworkflow provider for local agent memory and handoff.",
    )
    out = tmp_path / "out"

    assert main(
        [
            "providers",
            "--out",
            str(out),
            "--project-root",
            str(projects_root),
            "--sessions-root",
            str(sessions_root),
            "--claude-root",
            str(claude_root),
            "--workflow-root",
            str(tmp_path / "workflows"),
        ]
    ) == 0

    result = json.loads(capsys.readouterr().out)
    projects = read_jsonl(out / "manifests" / "projects.jsonl")
    sessions = read_jsonl(out / "manifests" / "sessions.jsonl")
    workflows = read_jsonl(out / "manifests" / "workflows.jsonl")

    assert result["projects"] == 1
    assert result["sessions"] == 2
    assert result["codex_sessions"] == 1
    assert result["claude_sessions"] == 1
    assert result["workflows"] == 1
    assert projects[0]["provider"] == "git_project"
    assert projects[0]["has_git"] is True
    assert "个人推荐系统" in projects[0]["text"]
    assert all(".bun" not in record["path"] for record in projects)
    assert {record["provider"] for record in sessions} == {"codex_session", "claude_session"}
    assert any(record["thread_name"] == "个人推荐系统讨论" for record in sessions)
    assert any("workflow provider" in record["text"] for record in sessions)
    assert workflows[0]["provider"] == "workflow_doc"
    assert workflows[0]["path"] == str(workflow_doc)
    assert workflows[0]["title"] == "Agent Context Workflow"


def test_resolver_source_scopes_use_provider_cards(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    projects_root = tmp_path / "projects"
    project = make_project(projects_root, "local-recommender", "个人推荐系统 recall rank rerank architecture.")
    sessions_root = make_codex_session(
        tmp_path / "codex",
        "session-2",
        "推荐系统会话",
        "Codex 会话里讨论了个人推荐系统的召回、排序和反馈闭环。",
    )
    claude_root = make_claude_session(
        tmp_path / "claude",
        "claude-session-2",
        "Claude 之前讨论过 agent workflow provider 和长期记忆。",
    )
    workflow_doc = make_workflow_doc(
        tmp_path / "workflows",
        "RECOMMENDER_WORKFLOW.md",
        "# Recommender Workflow\n\n个人推荐系统 workflow handoff with recall ranking feedback.",
    )
    out = tmp_path / "out"
    assert main(
        [
            "providers",
            "--out",
            str(out),
            "--project-root",
            str(projects_root),
            "--sessions-root",
            str(sessions_root),
            "--claude-root",
            str(claude_root),
            "--workflow-root",
            str(tmp_path / "workflows"),
        ]
    ) == 0
    capsys.readouterr()

    assert main(
        [
            "resolve",
            "--goal",
            "告诉我本地所有项目里如何构建个人推荐系统",
            "--out",
            str(out),
            "--limit",
            "5",
            "--source-scope",
            "gitProjects",
        ]
    ) == 0
    project_result = json.loads(capsys.readouterr().out)
    project_sources = read_jsonl(Path(project_result["sources_jsonl_path"]))

    assert any(source.get("source_group") == "git_repositories" for source in project_sources)
    assert any(source.get("path") == str(project) for source in project_sources)

    assert main(
        [
            "resolve",
            "--goal",
            "查一下之前 Codex 会话里个人推荐系统怎么做",
            "--out",
            str(out),
            "--limit",
            "5",
            "--source-scope",
            "codexSessions",
        ]
    ) == 0
    session_result = json.loads(capsys.readouterr().out)
    session_sources = read_jsonl(Path(session_result["sources_jsonl_path"]))

    assert any(source.get("source_group") == "codex_sessions" for source in session_sources)
    assert any(source.get("thread_name") == "推荐系统会话" for source in session_sources)

    assert main(
        [
            "resolve",
            "--goal",
            "查一下之前 agent workflow provider 和长期记忆怎么做",
            "--out",
            str(out),
            "--limit",
            "5",
            "--source-scope",
            "agentSessions",
        ]
    ) == 0
    agent_session_result = json.loads(capsys.readouterr().out)
    agent_session_sources = read_jsonl(Path(agent_session_result["sources_jsonl_path"]))

    assert any(source.get("provider") == "claude_session" for source in agent_session_sources)

    assert main(
        [
            "resolve",
            "--goal",
            "推荐系统 workflow handoff 怎么做",
            "--out",
            str(out),
            "--limit",
            "5",
            "--source-scope",
            "workflowDocs",
        ]
    ) == 0
    workflow_result = json.loads(capsys.readouterr().out)
    workflow_sources = read_jsonl(Path(workflow_result["sources_jsonl_path"]))

    assert workflow_result["source_scope"] == "workflowDocs"
    assert any(source.get("source_group") == "workflow_docs" for source in workflow_sources)
    assert any(source.get("path") == str(workflow_doc) for source in workflow_sources)


def test_mcp_resolve_context_accepts_source_scope_and_reads_provider_card(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    projects_root = tmp_path / "projects"
    project = make_project(projects_root, "local-recommender", "个人推荐系统 MCP resolver provider card.")
    sessions_root = make_codex_session(
        tmp_path / "codex",
        "session-mcp",
        "MCP session read",
        "<environment_context>hidden machine context</environment_context>Codex session transcript evidence for agent memory.",
    )
    claude_root = make_claude_session(
        tmp_path / "claude",
        "claude-session-mcp",
        "Claude session transcript evidence for workflow memory.",
    )
    workflow_doc = make_workflow_doc(
        tmp_path / "workflows",
        "MCP_WORKFLOW.md",
        "# MCP Workflow\n\nworkflowDocs provider card for MCP read_source.",
    )
    out = tmp_path / "out"
    assert main(
        [
            "providers",
            "--out",
            str(out),
            "--project-root",
            str(projects_root),
            "--sessions-root",
            str(sessions_root),
            "--claude-root",
            str(claude_root),
            "--workflow-root",
            str(tmp_path / "workflows"),
        ]
    ) == 0
    capsys.readouterr()

    result = mcp_resolve_context(
        "告诉我本地项目里如何构建个人推荐系统",
        limit=5,
        out_root=str(out),
        source_scope="gitProjects",
    )
    assert result["status"] == "ok"
    assert result["source_scope"] == "gitProjects"
    assert result["top_sources"]
    top_project = next(source for source in result["top_sources"] if source["path"] == str(project))

    source = mcp_read_source(top_project["source_id"], out_root=str(out), max_chars=1200)

    assert source["type"] == "git_project"
    assert "个人推荐系统" in source["text"]

    workflow_result = mcp_resolve_context(
        "workflowDocs provider card",
        limit=5,
        out_root=str(out),
        source_scope="workflowDocs",
    )
    top_workflow = next(source for source in workflow_result["top_sources"] if source["path"] == str(workflow_doc))
    workflow_source = mcp_read_source(top_workflow["source_id"], out_root=str(out), max_chars=1200)

    assert workflow_source["type"] == "workflow_doc"
    assert "workflowDocs provider card" in workflow_source["text"]

    sessions = read_jsonl(out / "manifests" / "sessions.jsonl")
    codex_session = next(record for record in sessions if record["provider"] == "codex_session")
    claude_session = next(record for record in sessions if record["provider"] == "claude_session")
    codex_source = mcp_read_source(codex_session["source_id"], out_root=str(out), max_chars=4000)
    claude_source = mcp_read_source(claude_session["source_id"], out_root=str(out), max_chars=4000)

    assert codex_source["type"] == "codex_session"
    assert codex_source["read_mode"] == "session_transcript_preview"
    assert "Session Transcript Preview" in codex_source["text"]
    assert "Codex session transcript evidence" in codex_source["text"]
    assert "hidden machine context" not in codex_source["text"]
    assert claude_source["type"] == "claude_session"
    assert claude_source["read_mode"] == "session_transcript_preview"
    assert "Claude session transcript evidence" in claude_source["text"]
    assert "workflow provider" in claude_source["text"]


def test_mcp_read_source_only_allows_indexed_sources_or_generated_artifacts(tmp_path: Path) -> None:
    out = tmp_path / "out"
    generated = out / "packs" / "sample" / "context.md"
    generated.parent.mkdir(parents=True)
    generated.write_text("# Generated Context\n", encoding="utf-8")
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("do not read by raw path", encoding="utf-8")

    generated_source = mcp_read_source(str(generated), out_root=str(out), max_chars=800)

    assert generated_source["type"] == "generated_artifact"
    assert generated_source["read_mode"] == "generated_artifact"
    assert "Generated Context" in generated_source["text"]

    with pytest.raises(FileNotFoundError, match="raw path reads are limited"):
        mcp_read_source(str(outside), out_root=str(out), max_chars=800)


def test_access_policy_filters_resolver_and_mcp_reads(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    allowed = tmp_path / "allowed-project"
    denied = tmp_path / "denied-project"
    allowed.mkdir()
    denied.mkdir()
    out = tmp_path / "out"
    write_provider_manifests(
        out,
        projects=[
            {
                "provider": "git_project",
                "source_id": "project:allowed",
                "project_id": "allowed",
                "name": "allowed-project",
                "path": str(allowed),
                "relative_path": "allowed-project",
                "text": "policy test recommender architecture",
                "has_git": False,
            },
            {
                "provider": "git_project",
                "source_id": "project:denied",
                "project_id": "denied",
                "name": "denied-project",
                "path": str(denied),
                "relative_path": "denied-project",
                "text": "policy test recommender architecture",
                "has_git": False,
            },
        ],
    )
    policy_path = out / "config" / "access_policy.json"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(
        json.dumps(
            {
                "allow_providers": ["git_project"],
                "deny_providers": [],
                "deny_path_patterns": [str(denied)],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert main(["access-policy", "--out", str(out)]) == 0
    policy_result = json.loads(capsys.readouterr().out)
    assert str(denied) in policy_result["policy"]["deny_path_patterns"]

    assert main(
        [
            "resolve",
            "--out",
            str(out),
            "--source-scope",
            "gitProjects",
            "--goal",
            "policy test recommender architecture",
            "--limit",
            "5",
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    sources = read_jsonl(Path(result["sources_jsonl_path"]))

    assert any(source.get("source_id") == "project:allowed" for source in sources)
    assert all(source.get("source_id") != "project:denied" for source in sources)
    with pytest.raises(PermissionError, match="blocked by access policy"):
        mcp_read_source("project:denied", out_root=str(out), max_chars=800)

    assert main(["access-audit", "--out", str(out), "--limit", "20"]) == 0
    audit = json.loads(capsys.readouterr().out)
    events = audit["events"]

    assert audit["events_total"] >= 1
    assert any(
        event["action"] == "resolver_filter_project_providers"
        and event["details"]["denied"] == 1
        for event in events
    )
    assert any(
        event["action"] == "mcp_read_provider"
        and event["decision"] == "denied"
        and event["identifier"] == "project:denied"
        for event in events
    )

    mcp_audit = mcp_access_audit(out_root=str(out), limit=5)
    assert mcp_audit["mcp_version"] == "0.1"
    assert mcp_audit["events"]


def test_access_policy_cli_and_mcp_patch_rules(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out"

    assert main(
        [
            "access-policy",
            "--out",
            str(out),
            "--deny-path",
            "*/Secrets/*",
            "--deny-provider",
            "claude_session",
            "--audit-max-bytes",
            "1234",
            "--audit-max-rotated-files",
            "2",
        ]
    ) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["updated"] is True
    assert "*/Secrets/*" in first["policy"]["deny_path_patterns"]
    assert "claude_session" in first["policy"]["deny_providers"]
    assert first["policy"]["audit_max_bytes"] == 1234
    assert first["policy"]["audit_max_rotated_files"] == 2

    patched = mcp_access_policy(
        out_root=str(out),
        allow_providers=["custom_provider"],
        deny_path_patterns=["*.secret"],
        remove_deny_providers=["claude_session"],
        audit_max_rotated_files=4,
    )
    assert patched["mcp_version"] == "0.1"
    assert patched["updated"] is True
    assert "custom_provider" in patched["policy"]["allow_providers"]
    assert "*.secret" in patched["policy"]["deny_path_patterns"]
    assert "claude_session" not in patched["policy"]["deny_providers"]
    assert patched["policy"]["audit_max_rotated_files"] == 4

    shown = mcp_access_policy(out_root=str(out))
    assert shown["policy"]["audit_max_bytes"] == 1234
    assert "custom_provider" in shown["policy"]["allow_providers"]


def test_access_consent_blocks_first_sensitive_read_then_grants(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "sensitive-project"
    project.mkdir()
    out = tmp_path / "out"
    write_provider_manifests(
        out,
        projects=[
            {
                "provider": "git_project",
                "source_id": "project:sensitive",
                "project_id": "sensitive",
                "name": "sensitive-project",
                "path": str(project),
                "relative_path": "sensitive-project",
                "text": "sensitive recommendation architecture evidence",
                "has_git": False,
            }
        ],
    )

    assert main(["access-policy", "--out", str(out), "--require-consent-provider", "git_project"]) == 0
    policy_result = json.loads(capsys.readouterr().out)
    assert "git_project" in policy_result["policy"]["require_consent_providers"]

    assert main(
        [
            "resolve",
            "--out",
            str(out),
            "--source-scope",
            "gitProjects",
            "--goal",
            "sensitive recommendation architecture",
            "--limit",
            "3",
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    sources = read_jsonl(Path(result["sources_jsonl_path"]))
    assert any(source.get("source_id") == "project:sensitive" for source in sources)

    with pytest.raises(ConsentRequiredError, match="requires consent"):
        mcp_read_source("project:sensitive", out_root=str(out), max_chars=800)

    grant = mcp_grant_access_consent("project:sensitive", out_root=str(out), reason="test approved")
    assert grant["mcp_version"] == "0.1"
    assert grant["grant"]["source_id"] == "project:sensitive"

    read_result = mcp_read_source("project:sensitive", out_root=str(out), max_chars=800)
    assert "sensitive recommendation architecture evidence" in read_result["text"]

    assert main(["access-audit", "--out", str(out), "--limit", "20"]) == 0
    audit = json.loads(capsys.readouterr().out)
    assert any(event["decision"] == "consent_required" for event in audit["events"])
    assert any(event["action"] == "grant_consent" for event in audit["events"])


def test_access_audit_rotates_and_reads_gzip_history(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out"
    policy_path = out / "config" / "access_policy.json"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(
        json.dumps(
            {
                "audit_max_bytes": 350,
                "audit_max_rotated_files": 20,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    for index in range(8):
        record_access_audit(
            out,
            action="rotation_test",
            decision="allowed",
            identifier=f"event-{index}",
            reason="test",
            record={
                "provider": "git_project",
                "source_id": f"project:{index}",
                "path": f"/tmp/project-{index}",
            },
            details={"payload": "x" * 120},
        )

    audit_path = out / "reports" / "access_audit.jsonl"
    assert audit_path.exists()
    assert Path(f"{audit_path}.1.gz").exists()

    assert main(["access-audit", "--out", str(out), "--limit", "20"]) == 0
    audit = json.loads(capsys.readouterr().out)
    identifiers = [event["identifier"] for event in audit["events"]]

    assert audit["events_total"] == 8
    assert audit["rotated_paths"]
    assert identifiers == [f"event-{index}" for index in range(8)]


def test_session_index_cli_and_resolver_return_session_chunks(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions_root = make_codex_session(
        tmp_path / "codex",
        "session-index-codex",
        "Agent memory session",
        "Codex session transcript evidence for agent memory retrieval chunk and latent semantic candidate.",
    )
    claude_root = make_claude_session(
        tmp_path / "claude",
        "session-index-claude",
        "Claude transcript evidence for workflow memory retrieval chunk.",
    )
    out = tmp_path / "out"
    assert main(
        [
            "providers",
            "--out",
            str(out),
            "--project-root",
            str(tmp_path / "empty-projects"),
            "--sessions-root",
            str(sessions_root),
            "--claude-root",
            str(claude_root),
        ]
    ) == 0
    capsys.readouterr()

    assert main(["index-sessions", "--out", str(out), "--max-sessions", "10"]) == 0
    index_result = json.loads(capsys.readouterr().out)
    assert index_result["documents"] == 2
    assert index_result["chunks"] >= 2
    assert Path(index_result["index_path"]).name == "sessions.sqlite"
    assert read_jsonl(out / "manifests" / "session_documents.jsonl")
    assert read_jsonl(out / "manifests" / "session_chunks.jsonl")

    assert main(
        [
            "resolve",
            "--goal",
            "之前会话里有没有 agent memory retrieval chunk 证据",
            "--out",
            str(out),
            "--source-scope",
            "agentSessions",
            "--limit",
            "5",
        ]
    ) == 0
    resolve_result = json.loads(capsys.readouterr().out)
    sources = read_jsonl(Path(resolve_result["sources_jsonl_path"]))
    session_chunk = next(source for source in sources if source.get("provider") == "session_index")

    assert session_chunk["type"] == "session_chunk"
    assert session_chunk["source_group"] == "codex_sessions"
    assert session_chunk["source_chunk_id"]
    assert "agent memory retrieval chunk" in session_chunk["snippet"]

    read_result = mcp_read_source(session_chunk["source_chunk_id"], out_root=str(out), max_chars=4000)
    assert read_result["read_mode"] == "session_index_chunk"
    assert "agent memory retrieval chunk" in read_result["text"]

    mcp_result = mcp_index_sessions(out_root=str(out), max_sessions=10)
    assert mcp_result["mcp_version"] == "0.1"
    assert mcp_result["documents"] == 2

    monkeypatch.setenv("AGENT_CONTEXT_MIN_SEMANTIC_ROWS", "1")
    monkeypatch.setattr("agent_context.semantic_index.get_embedding_backend", lambda _config: KeywordSemanticBackend())
    semantic = run_semantic_refresh(out, source="sessions", budget=10, backend="fastembed")
    assert semantic["processed"] >= 1
    assert main(["semantic-refresh", "--out", str(out), "--source", "sessions", "--budget", "1"]) == 0
    capsys.readouterr()

    assert main(
        [
            "resolve",
            "--goal",
            "之前会话里有没有 latent semantic candidate agent memory 证据",
            "--out",
            str(out),
            "--source-scope",
            "agentSessions",
            "--limit",
            "5",
        ]
    ) == 0
    semantic_result = json.loads(capsys.readouterr().out)
    semantic_sources = read_jsonl(Path(semantic_result["sources_jsonl_path"]))
    session_semantic = [
        source
        for source in semantic_sources
        if source.get("source_group") == "codex_sessions" and source.get("score_parts", {}).get("semantic", 0) > 0
    ]
    assert session_semantic
    assert session_semantic[0]["source_group"] == "codex_sessions"
    assert "semantic_index" in session_semantic[0]["retrieval_channels"]


def test_feedback_rerank_boosts_selected_provider_source(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    projects_root = tmp_path / "projects"
    _alpha = make_project(projects_root, "alpha-recommender", "个人推荐系统 recall rank architecture.")
    beta = make_project(projects_root, "beta-recommender", "个人推荐系统 recall rank architecture.")
    out = tmp_path / "out"
    assert main(
        [
            "providers",
            "--out",
            str(out),
            "--project-root",
            str(projects_root),
            "--sessions-root",
            str(tmp_path / "empty"),
            "--claude-root",
            str(tmp_path / "empty-claude"),
        ]
    ) == 0
    capsys.readouterr()
    write_jsonl(
        out / "feedback" / "mcp_feedback.jsonl",
        [
            {
                "goal": "告诉我本地所有项目里如何构建个人推荐系统",
                "query_id": "manual",
                "selected_source": str(beta),
                "rating": 5,
                "reason": "better",
            }
        ],
    )

    assert main(
        [
            "resolve",
            "--goal",
            "告诉我本地所有项目里如何构建个人推荐系统",
            "--out",
            str(out),
            "--limit",
            "5",
            "--source-scope",
            "gitProjects",
        ]
    ) == 0

    result = json.loads(capsys.readouterr().out)
    sources = read_jsonl(Path(result["sources_jsonl_path"]))
    beta_source = next(source for source in sources if source["path"] == str(beta))

    assert sources[0]["path"] == str(beta)
    assert beta_source["resolver_score_parts"]["feedback"] > 0
    assert beta_source["resolver_score_parts"]["feedback_query_family_source"] > 0


def test_mirror_ranker_feedback_affects_resolver_fusion(tmp_path: Path) -> None:
    out = tmp_path / "out"
    goal = "我要去求职，请帮我看下我的电脑里适合简历包装的项目有哪些"
    winner = {
        "source_id": "project-plm",
        "path": "/tmp/plm/README.md",
        "source_group": "vault",
        "score": 0.4,
        "profile_prior": 1.0,
        "bm25": 0.4,
    }
    loser = {
        "source_id": "random-doc",
        "path": "/tmp/random.md",
        "source_group": "downloads",
        "score": 0.4,
        "profile_prior": 0.0,
        "bm25": 0.4,
    }

    record_pairwise_feedback(out, goal=goal, winner=winner, loser=loser, reason="PLM is the user's real project")
    train_pairwise_ranker(out)

    sources = fuse_candidates(
        [loser, winner],
        2,
        out_root=out,
        goal=goal,
        feedback_model={},
        route_selector_model={},
    )

    assert sources[0]["source_id"] == "project-plm"
    assert sources[0]["resolver_score_parts"]["mirror_ranker"] > 0
    assert sources[1]["resolver_score_parts"]["mirror_ranker"] < 0


def test_resolve_alternative_records_negative_feedback_and_avoids_rejected_source(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    projects_root = tmp_path / "projects"
    make_code_project(projects_root, "alpha-recommender")
    make_code_project(projects_root, "beta-recommender")
    out = tmp_path / "out"
    goal = "告诉我本地所有项目里如何构建个人推荐系统"

    assert main(
        [
            "index-projects",
            "--out",
            str(out),
            "--project-root",
            str(projects_root),
            "--max-files-per-project",
            "20",
        ]
    ) == 0
    capsys.readouterr()
    assert main(
        [
            "resolve",
            "--goal",
            goal,
            "--out",
            str(out),
            "--source-scope",
            "gitProjects",
            "--limit",
            "4",
        ]
    ) == 0
    initial = json.loads(capsys.readouterr().out)
    initial_sources = read_jsonl(Path(initial["sources_jsonl_path"]))
    rejected_path = initial_sources[0]["path"]

    assert main(
        [
            "resolve-alternative",
            "--goal",
            goal,
            "--out",
            str(out),
            "--source-scope",
            "gitProjects",
            "--limit",
            "4",
            "--reject-source",
            rejected_path,
            "--reason",
            "not the route I wanted",
        ]
    ) == 0

    alternative = json.loads(capsys.readouterr().out)
    alternative_sources = read_jsonl(Path(alternative["sources_jsonl_path"]))
    plan = json.loads(Path(alternative["resolution_plan_json_path"]).read_text(encoding="utf-8"))
    feedback = read_jsonl(out / "feedback" / "alternative_feedback.jsonl")
    model = json.loads((out / "feedback" / "model.json").read_text(encoding="utf-8"))

    assert rejected_path in alternative["rejected_sources"]
    assert all(source["path"] != rejected_path for source in alternative_sources)
    assert plan["avoid_sources"] == [rejected_path]
    assert plan["avoid_stats"]["filtered_candidates"] > 0
    assert feedback[-1]["rejected_sources"] == [rejected_path]
    assert model["source_scores"][rejected_path] < 0


def test_mcp_resolve_alternative_context_returns_replacement_pack(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    projects_root = tmp_path / "projects"
    make_code_project(projects_root, "alpha-recommender")
    make_code_project(projects_root, "beta-recommender")
    out = tmp_path / "out"
    goal = "告诉我本地所有项目里如何构建个人推荐系统"

    assert main(
        [
            "index-projects",
            "--out",
            str(out),
            "--project-root",
            str(projects_root),
            "--max-files-per-project",
            "20",
        ]
    ) == 0
    capsys.readouterr()
    first = mcp_resolve_context(goal, limit=4, out_root=str(out), source_scope="gitProjects")
    rejected_path = first["top_sources"][0]["path"]

    result = mcp_resolve_alternative_context(
        goal,
        rejected_sources=[rejected_path],
        reason="wrong",
        limit=4,
        out_root=str(out),
        source_scope="gitProjects",
    )

    assert result["mcp_version"] == "0.1"
    assert result["status"] == "ok"
    assert result["top_sources"]
    assert all(source["path"] != rejected_path for source in result["top_sources"])
    assert Path(result["context_md_path"]).exists()


def test_feedback_model_expands_file_feedback_to_project_keys(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    projects_root = tmp_path / "projects"
    project = make_code_project(projects_root, "beta-recommender")
    selected_file = project / "src" / "recommender.py"
    out = tmp_path / "out"

    assert main(
        [
            "index-projects",
            "--out",
            str(out),
            "--project-root",
            str(projects_root),
            "--max-files-per-project",
            "20",
        ]
    ) == 0
    capsys.readouterr()
    write_jsonl(
        out / "feedback" / "mcp_feedback.jsonl",
        [{"query_id": "manual", "selected_source": str(selected_file), "rating": 5, "reason": "better project"}],
    )

    assert main(["feedback-model", "--out", str(out)]) == 0

    result = json.loads(capsys.readouterr().out)
    model_path = Path(result["feedback_model_path"])
    model = json.loads(model_path.read_text(encoding="utf-8"))
    scores = model["source_scores"]

    assert model_path.exists()
    assert scores[str(selected_file)] > 0
    assert scores[f"project_name:{project.name}"] > 0
    assert scores["group:git_repositories"] > 0


def test_index_projects_cli_creates_project_file_index(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    projects_root = tmp_path / "projects"
    project = make_code_project(projects_root, "local-recommender")
    out = tmp_path / "out"

    assert main(
        [
            "index-projects",
            "--out",
            str(out),
            "--project-root",
            str(projects_root),
            "--max-files-per-project",
            "20",
        ]
    ) == 0

    result = json.loads(capsys.readouterr().out)
    documents = read_jsonl(out / "manifests" / "project_documents.jsonl")
    chunks = read_jsonl(out / "manifests" / "project_chunks.jsonl")
    symbols = read_jsonl(out / "manifests" / "symbols.jsonl")
    paths = {record["path"] for record in documents}

    assert result["documents"] >= 2
    assert result["chunks"] >= 2
    assert result["symbols"] >= 3
    assert result["embedding_backend"] == "hash-vector-lite"
    assert result["ann_backend"] == "exact-json-scan"
    assert (out / "indexes" / "projects.sqlite").exists()
    assert str(project / "README.md") in paths
    assert str(project / "src" / "recommender.py") in paths
    assert str(project / "node_modules" / "ignored" / "bad.py") not in paths
    assert any(symbol["symbol"] == "PersonalRecommender" for symbol in symbols)
    assert any(symbol["symbol"] == "rank_items" for symbol in symbols)


def test_resolver_uses_project_code_index_for_git_projects(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    projects_root = tmp_path / "projects"
    project = make_code_project(projects_root, "local-recommender")
    out = tmp_path / "out"

    assert main(
        [
            "index-projects",
            "--out",
            str(out),
            "--project-root",
            str(projects_root),
            "--max-files-per-project",
            "20",
        ]
    ) == 0
    capsys.readouterr()

    assert main(
        [
            "resolve",
            "--goal",
            "告诉我本地项目里 personal recommender 如何实现 ranking feedback loop",
            "--out",
            str(out),
            "--source-scope",
            "gitProjects",
            "--limit",
            "8",
        ]
    ) == 0

    result = json.loads(capsys.readouterr().out)
    sources = read_jsonl(Path(result["sources_jsonl_path"]))
    recommender_sources = [
        source for source in sources if source["path"] == str(project / "src" / "recommender.py")
    ]

    assert recommender_sources
    assert recommender_sources[0]["source_group"] == "git_repositories"
    assert recommender_sources[0]["type"] == "project_code"
    assert "rank_items" in recommender_sources[0]["snippet"] or "feedback" in recommender_sources[0]["snippet"]

    source = mcp_read_source(recommender_sources[0]["source_chunk_id"], out_root=str(out), max_chars=1200)
    assert "PersonalRecommender" in source["text"]


def test_resolver_uses_background_semantic_index_for_git_projects(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects_root = tmp_path / "projects"
    project = make_code_project(projects_root, "semantic-recommender")
    semantic_file = project / "src" / "semantic_signal.py"
    semantic_file.write_text(
        "def hidden_route():\n    return 'latent semantic candidate for cold start discovery'\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"

    assert main(
        [
            "index-projects",
            "--out",
            str(out),
            "--project-root",
            str(projects_root),
            "--max-files-per-project",
            "20",
        ]
    ) == 0
    capsys.readouterr()
    monkeypatch.setenv("AGENT_CONTEXT_MIN_SEMANTIC_ROWS", "1")
    monkeypatch.setenv("AGENT_CONTEXT_ANN_BACKEND", "hnswlib")
    install_fake_hnswlib(monkeypatch)
    monkeypatch.setattr("agent_context.semantic_index.get_embedding_backend", lambda _config: KeywordSemanticBackend())
    refresh = run_semantic_refresh(out, source="projects", budget=20, backend="fastembed")
    assert refresh["processed"] > 0

    assert main(
        [
            "resolve",
            "--goal",
            "告诉我本地项目里如何做推荐系统冷启动 discovery",
            "--out",
            str(out),
            "--source-scope",
            "gitProjects",
            "--limit",
            "8",
        ]
    ) == 0

    result = json.loads(capsys.readouterr().out)
    sources = read_jsonl(Path(result["sources_jsonl_path"]))
    plan = json.loads(Path(result["resolution_plan_json_path"]).read_text(encoding="utf-8"))
    semantic_sources = [
        source
        for source in sources
        if source["path"] == str(semantic_file) and source["score_parts"].get("semantic", 0) > 0
    ]

    assert semantic_sources
    assert semantic_sources[0]["source_group"] == "git_repositories"
    assert "semantic_index" in semantic_sources[0]["retrieval_channels"]
    assert plan["retrieval_stats"]["candidate_count_by_channel"]["semantic_index"] > 0
    assert any("semantic_hnswlib_ann" in mode for mode in plan["semantic_retrieval_modes"])
    assert any("rebuilt" in status for status in plan["semantic_ann_cache_statuses"])
    assert (out / "indexes" / "semantic_ann").exists()


def test_unsupported_semantic_only_candidates_do_not_gain_query_coverage() -> None:
    semantic_only = {
        "type": "semantic_chunk",
        "source_id": "semantic-noise",
        "source_chunk_id": "semantic-noise:0001",
        "path": "/tmp/projects/noise/README.md",
        "source_group": "git_repositories",
        "retrieval_channels": ["semantic_index"],
        "semantic_lexical_support": False,
        "matched_queries": ["q1", "q2", "q3"],
        "score": 0.35,
        "score_parts": {"semantic": 0.35, "semantic_raw": 1.0},
    }
    project_code = {
        "type": "project_code",
        "source_id": "project-code",
        "source_chunk_id": "project-code:0001",
        "path": "/tmp/projects/recommender/src/recommender.py",
        "source_group": "git_repositories",
        "retrieval_channel": "project_code_index",
        "matched_queries": ["q1"],
        "score": 0.36,
        "score_parts": {"vector": 0.36},
    }

    sources = fuse_candidates([semantic_only, project_code], limit=2)

    assert sources[0]["path"] == project_code["path"]
    assert sources[1]["resolver_score_parts"]["query_coverage"] == 0.0


def test_project_code_results_are_diversified_across_projects(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    projects_root = tmp_path / "projects"
    noisy = make_project(
        projects_root,
        "agent-context-system",
        "告诉我本地所有项目里如何构建个人推荐系统 " * 8,
    )
    (noisy / "docs").mkdir()
    for index in range(4):
        (noisy / "docs" / f"context-{index}.md").write_text(
            "告诉我本地所有项目里如何构建个人推荐系统 project architecture resolver " * 8,
            encoding="utf-8",
        )
    useful = make_code_project(projects_root, "recommendation-system-mvp")
    out = tmp_path / "out"

    assert main(
        [
            "index-projects",
            "--out",
            str(out),
            "--project-root",
            str(projects_root),
            "--max-files-per-project",
            "20",
        ]
    ) == 0
    capsys.readouterr()

    assert main(
        [
            "resolve",
            "--goal",
            "告诉我本地所有项目里如何构建个人推荐系统",
            "--out",
            str(out),
            "--source-scope",
            "gitProjects",
            "--limit",
            "4",
        ]
    ) == 0

    result = json.loads(capsys.readouterr().out)
    sources = read_jsonl(Path(result["sources_jsonl_path"]))
    project_names = [source.get("project_name") for source in sources if source.get("type") == "project_code"]

    assert "agent-context-system" in project_names
    assert "recommendation-system-mvp" in project_names
    assert project_names.count("agent-context-system") <= 2


def test_project_diversity_cap_expands_for_larger_replay_limits() -> None:
    candidates = [
        {
            "path": f"/repo/file-{index}.md",
            "relative_path": f"file-{index}.md",
            "project_id": "project-1",
            "project_path": "/repo",
            "project_name": "repo",
            "source_group": "git_repositories",
            "score": 0.9 - index * 0.01,
            "score_parts": {},
            "matched_queries": ["recommendation system"],
        }
        for index in range(4)
    ]
    for index in range(2):
        candidates.append(
            {
                "path": f"/other-{index}/README.md",
                "relative_path": "README.md",
                "project_id": f"project-other-{index}",
                "project_path": f"/other-{index}",
                "project_name": f"other-{index}",
                "source_group": "git_repositories",
                "score": 0.5 - index * 0.01,
                "score_parts": {},
                "matched_queries": ["recommendation system"],
            }
        )

    small = fuse_candidates(candidates, limit=4)
    larger = fuse_candidates(candidates, limit=8)

    assert sum(1 for source in small if source.get("project_id") == "project-1") == 2
    assert sum(1 for source in larger[:4] if source.get("project_id") == "project-1") == 3


def test_resolver_uses_root_file_catalog_as_filesystem_provider(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    machine = tmp_path / "machine"
    target = machine / "Applications" / "CodexPlusPlus.app" / "Contents" / "MacOS"
    target.mkdir(parents=True)
    (target / "CodexPlusPlus").write_text("binary placeholder", encoding="utf-8")
    project = machine / "Users" / "gengrf" / "plm" / "scripts"
    project.mkdir(parents=True)
    (project / "plm_homelander_100k_writer.py").write_text("print('plm')", encoding="utf-8")
    out = tmp_path / "out"

    assert main(
        [
            "file-catalog",
            "--scope",
            str(machine),
            "--out",
            str(out / "catalog-shards" / "root-full"),
            "--reset",
        ]
    ) == 0
    capsys.readouterr()

    assert main(
        [
            "resolve",
            "--goal",
            "我的电脑里 CodexPlusPlus 安装在哪里",
            "--out",
            str(out),
            "--source-scope",
            "filesystem",
            "--limit",
            "4",
        ]
    ) == 0

    result = json.loads(capsys.readouterr().out)
    sources = read_jsonl(Path(result["sources_jsonl_path"]))

    assert result["selected_sources"] == ["root_file_catalog"]
    assert any(source.get("source_group") == "root_file_catalog" for source in sources)
    top = sources[0]
    assert top["logical_path"].endswith("CodexPlusPlus")
    assert top["source_zone"]
    assert "source_weight" in top
    assert "source_zone=" in top["why_selected"]
