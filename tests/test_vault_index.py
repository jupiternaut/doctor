from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from agent_context.llm_wiki import ProjectSpec, run_wiki_command
from agent_context.resolver import resolve_context
from agent_context.vault_index import (
    build_vault_index,
    knowledge_edges_path_for,
    knowledge_index_path_for,
    resolve_vault_context,
    run_vault_anytime_step,
    run_vault_check,
    run_wiki_baseline_eval,
    vault_index_path_for,
)


def make_project(root: Path, name: str, readme: str) -> Path:
    project = root / name
    (project / "docs").mkdir(parents=True)
    (project / "README.md").write_text(readme, encoding="utf-8")
    (project / "docs" / "resume.md").write_text(f"# {name} resume evidence\n\n简历 作品集 项目 evidence\n", encoding="utf-8")
    return project


def baseline_specs(source_root: Path) -> list[ProjectSpec]:
    return [
        ProjectSpec(
            "project-plm",
            "PLM / PlotPilot / 墨枢",
            make_project(source_root, "plm", "# PlotPilot\n\n长篇 AI 创作和开源叙事引擎项目。\n"),
            ("PLM", "PlotPilot", "墨枢"),
            ("project", "writing", "resume"),
            "primary writing project",
        ),
        ProjectSpec(
            "project-drama",
            "Drama / Zen Drama",
            make_project(source_root, "drama", "# Drama\n\nAgent runtime and browser orchestration project.\n"),
            ("Drama", "Zen Drama"),
            ("project", "agent-os", "resume"),
            "agent runtime project",
        ),
        ProjectSpec(
            "project-codex-plus-plus",
            "Codex++",
            make_project(source_root, "codex-plus-plus", "# Codex++\n\nCodex agent UI and context panel project.\n"),
            ("Codex++", "CodexPlusPlus"),
            ("project", "codex", "resume"),
            "codex context ui project",
        ),
        ProjectSpec(
            "project-gugu",
            "Gugu / RoomLite",
            make_project(source_root, "gugu", "# Gugu\n\n3D room and visual asset pipeline project.\n"),
            ("Gugu", "RoomLite"),
            ("project", "asset-pipeline", "resume"),
            "visual asset project",
        ),
        ProjectSpec(
            "project-doctor",
            "Doctor / agent-context-system",
            make_project(source_root, "doctor", "# Doctor\n\nLocal context runtime and vault resolver project.\n"),
            ("Doctor", "agent-context"),
            ("project", "context-runtime", "resume"),
            "context runtime project",
        ),
    ]


def test_vault_index_builds_sqlite_fts_graph_and_aliases(tmp_path: Path) -> None:
    out = tmp_path / "out"
    run_wiki_command(
        out,
        action="baseline",
        diff_id="baseline-projects",
        approve=True,
        project_specs=baseline_specs(tmp_path / "sources"),
    )

    result = build_vault_index(out)

    assert result["status"] == "ok"
    assert result["concepts_indexed"] == 5
    assert result["fts_enabled"] is True
    db_path = vault_index_path_for(out)
    assert db_path.exists()

    conn = sqlite3.connect(db_path)
    try:
        concept_count = conn.execute("SELECT count(*) FROM concepts").fetchone()[0]
        alias_count = conn.execute("SELECT count(*) FROM aliases").fetchone()[0]
        edge_count = conn.execute("SELECT count(*) FROM graph_edges").fetchone()[0]
        fts_count = conn.execute("SELECT count(*) FROM concepts_fts").fetchone()[0]
    finally:
        conn.close()

    assert concept_count == 5
    assert alias_count >= 10
    assert edge_count >= 10
    assert fts_count == 5
    assert knowledge_index_path_for(out).exists()
    assert knowledge_edges_path_for(out).exists()

    check = run_vault_check(out)
    assert check["status"] == "ok"
    assert check["okf_version"] == "0.1"
    assert check["okf_required_frontmatter_fields"] == ["type"]
    assert "id" in check["doctor_required_frontmatter_fields"]
    assert check["markdown_concepts"] == 5
    assert check["indexed_concepts"] == 5
    assert Path(check["report_path"]).exists()

    knowledge = sqlite3.connect(knowledge_index_path_for(out))
    try:
        tables = {
            row[0]
            for row in knowledge.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        citation_count = knowledge.execute("SELECT count(*) FROM citations").fetchone()[0]
        freshness_count = knowledge.execute("SELECT count(*) FROM freshness").fetchone()[0]
        score_feature_count = knowledge.execute("SELECT count(*) FROM score_features").fetchone()[0]
    finally:
        knowledge.close()

    assert {"concepts", "aliases", "graph_edges", "citations", "freshness", "claims", "score_features"} <= tables
    assert citation_count >= 5
    assert freshness_count == 5
    assert score_feature_count >= 5


def test_vault_check_enforces_strict_okf_reserved_and_concept_rules(tmp_path: Path) -> None:
    out = tmp_path / "out"
    vault = out / "vault"
    (vault / "projects").mkdir(parents=True)
    (vault / "index.md").write_text(
        '---\ntype: "index"\nokf_version: "0.1"\n---\n\n# Bad Index\n',
        encoding="utf-8",
    )
    (vault / "log.md").write_text("# Vault Log\n\n| Time | Event |\n|---|---|\n", encoding="utf-8")
    (vault / "projects" / "bad.md").write_text("# Missing frontmatter\n", encoding="utf-8")

    check = run_vault_check(out)

    assert check["status"] == "error"
    messages = [issue["message"] for issue in check["issues"]]
    assert "root OKF index.md frontmatter may only declare okf_version" in messages
    assert "OKF log.md must use ISO date headings in YYYY-MM-DD form" in messages
    assert "OKF concept document must start with parseable YAML frontmatter" in messages


def test_vault_resolve_prioritizes_resume_projects_and_writes_bounded_context(tmp_path: Path) -> None:
    out = tmp_path / "out"
    run_wiki_command(
        out,
        action="baseline",
        diff_id="baseline-projects",
        approve=True,
        project_specs=baseline_specs(tmp_path / "sources"),
    )
    build_vault_index(out)

    result = resolve_vault_context(out, "我要找适合简历包装的项目", limit=5, mode="fast")

    assert result["status"] == "ok"
    top_ids = [item["concept_id"] for item in result["top_concepts"]]
    assert top_ids[:4] == ["project-plm", "project-drama", "project-codex-plus-plus", "project-gugu"]
    assert result["token_report"]["canonical_concepts"] == 5
    assert "why_not_feed_all" in result["token_report"]

    context_path = Path(result["context_md_path"])
    sources_path = Path(result["sources_jsonl_path"])
    manifest_path = Path(result["manifest_json_path"])
    state_path = Path(result["anytime_state_json_path"])
    assert context_path.exists()
    assert sources_path.exists()
    assert manifest_path.exists()
    assert state_path.exists()

    context = context_path.read_text(encoding="utf-8")
    assert "Fast Answer Context" in context
    assert "Token Boundary" in context
    assert "PLM / PlotPilot" in context

    sources = [json.loads(line) for line in sources_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(sources) == 5
    assert all(source["provider"] == "vault_index" for source in sources)
    assert all("evidence" in source for source in sources)

    stopped = resolve_vault_context(out, "我要找适合简历包装的项目", continue_from=state_path, feedback="satisfied")
    assert stopped["status"] == "stopped_satisfied"


def test_vault_anytime_step_expands_into_source_files_and_updates_state(tmp_path: Path) -> None:
    out = tmp_path / "out"
    run_wiki_command(
        out,
        action="baseline",
        diff_id="baseline-projects",
        approve=True,
        project_specs=baseline_specs(tmp_path / "sources"),
    )
    build_vault_index(out)
    first = resolve_vault_context(out, "我要找适合简历包装的项目", limit=3, mode="fast")

    step = run_vault_anytime_step(
        out,
        Path(first["anytime_state_json_path"]),
        feedback="not_right",
        limit=6,
        max_files_per_root=4,
    )

    assert step["status"] == "expanded_step_ready"
    assert step["sources_included"] > 0
    assert Path(step["context_md_path"]).exists()
    assert Path(step["sources_jsonl_path"]).exists()
    assert Path(step["vault_anytime_step_json_path"]).exists()
    assert all(source["path"] for source in step["top_sources"])

    state = json.loads(Path(first["anytime_state_json_path"]).read_text(encoding="utf-8"))
    assert state["last_feedback"] == "not_right"
    assert state["expansion_round"] == 1
    assert state["latest_expansion"]["context_md_path"] == step["context_md_path"]
    assert state["expanded_sources"]

    context = Path(step["context_md_path"]).read_text(encoding="utf-8")
    assert "Slow Answer Expansion" in context
    assert "README.md" in context or "resume.md" in context

    stopped = run_vault_anytime_step(out, Path(first["anytime_state_json_path"]), feedback="satisfied")
    assert stopped["status"] == "stopped_satisfied"


def test_generic_resolver_can_use_vault_as_explicit_optional_provider(tmp_path: Path) -> None:
    out = tmp_path / "out"
    run_wiki_command(
        out,
        action="baseline",
        diff_id="baseline-projects",
        approve=True,
        project_specs=baseline_specs(tmp_path / "sources"),
    )
    build_vault_index(out)

    result = resolve_context(out, "我要去求职，请帮我看下我的电脑里适合简历包装的项目有哪些", source_scope="vault", limit=4)

    assert result["source_scope"] == "vault"
    assert result["selected_sources"] == ["vault"]
    assert result["sources_included"] == 4
    context = Path(result["context_md_path"]).read_text(encoding="utf-8")
    sources = [json.loads(line) for line in Path(result["sources_jsonl_path"]).read_text(encoding="utf-8").splitlines() if line.strip()]
    assert "vault" in context
    assert all(source["source_group"] == "vault" for source in sources)
    assert sources[0]["concept_id"] == "project-plm"


def test_vault_feedback_failure_and_baseline_eval_paths(tmp_path: Path) -> None:
    out = tmp_path / "out"
    specs = baseline_specs(tmp_path / "sources")
    run_wiki_command(out, action="baseline", diff_id="baseline-projects", approve=True, project_specs=specs)
    run_wiki_command(out, action="compile-baseline", diff_id="reject-plm", project_specs=[specs[0]])
    run_wiki_command(
        out,
        action="reject",
        diff_id="reject-plm",
        reason="简历 PLM route was rejected for this replay case.",
        failure=True,
    )
    feedback_dir = out / "feedback"
    feedback_dir.mkdir(parents=True)
    (feedback_dir / "mirror_feedback.jsonl").write_text(
        json.dumps(
            {
                "goal": "我要找适合简历包装的项目",
                "target": {"project_id": "project-gugu", "source_group": "vault"},
                "label": "positive",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    build_vault_index(out)

    result = resolve_vault_context(out, "我要找适合简历包装的项目", limit=8, mode="fast")
    concepts = {item["concept_id"]: item for item in result["top_concepts"]}

    assert concepts["project-plm"]["score_parts"]["failure"] < 0
    assert concepts["project-gugu"]["score_parts"]["feedback"] > 0

    report = run_wiki_baseline_eval(out)
    assert Path(report["json_path"]).exists()
    assert Path(report["latest_markdown_path"]).exists()
    markdown = Path(report["latest_markdown_path"]).read_text(encoding="utf-8")
    assert "开源往事如何在番茄爆火" in markdown
    assert "Raw File Search" in markdown
    assert "Vault Retrieval" in markdown
