from __future__ import annotations

from pathlib import Path
import json

from agent_context.codex_hook import build_codex_preflight
from agent_context.cli import main


def test_codex_preflight_calls_resolver_and_returns_markdown_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = {}

    def fake_resolve_context(out_root: Path, goal: str, limit: int, source_scope: str) -> dict:
        calls["out_root"] = out_root
        calls["goal"] = goal
        calls["limit"] = limit
        calls["source_scope"] = source_scope
        pack = out_root / "packs" / "task-resolve-20260615120000"
        pack.mkdir(parents=True, exist_ok=True)
        (pack / "context.md").write_text("# Context Pack\n\nLocal Doctor evidence.", encoding="utf-8")
        return {
            "resolver_version": "0.4",
            "route": "rule_based_v0",
            "task_id": "task-resolve-20260615120000",
            "goal": goal,
            "intent": "project_code",
            "source_scope": source_scope,
            "selected_sources": ["git_repositories"],
            "queries": [goal, "project context"],
            "context_md_path": str(pack / "context.md"),
            "sources_jsonl_path": str(pack / "sources.jsonl"),
            "manifest_json_path": str(pack / "manifest.json"),
            "resolution_plan_json_path": str(pack / "resolution_plan.json"),
            "sources_included": 2,
        }

    monkeypatch.setattr("agent_context.codex_hook.resolve_context", fake_resolve_context)

    result = build_codex_preflight(
        tmp_path / "out",
        "prepare local coding task",
        source_scope="gitProjects",
        limit=5,
        auto_context=True,
        mode="deep",
    )

    assert calls == {
        "out_root": (tmp_path / "out").resolve(),
        "goal": "prepare local coding task",
        "limit": 5,
        "source_scope": "gitProjects",
    }
    assert result["status"] == "ok"
    assert result["mode"] == "deep"
    assert result["paths"]["context_md_path"] == result["context_md_path"]
    assert result["paths"]["sources_jsonl_path"] == result["sources_jsonl_path"]
    assert Path(result["preflight_markdown_path"]).exists()
    assert Path(result["model_input_md_path"]).exists()
    assert result["paths"]["model_input_md_path"] == result["model_input_md_path"]
    assert "status: ok" in result["preflight_markdown"]
    assert "mode: deep" in result["preflight_markdown"]
    assert f"`{result['context_md_path']}`" in result["preflight_markdown"]
    assert f"`{result['model_input_md_path']}`" in result["preflight_markdown"]
    model_input = Path(result["model_input_md_path"]).read_text(encoding="utf-8")
    assert "prepare local coding task" in model_input
    assert "Local Doctor evidence." in model_input
    assert "hidden platform or client system prompts" in model_input


def test_codex_preflight_disabled_skips_resolver(tmp_path: Path, monkeypatch) -> None:
    def fail_if_called(*_args, **_kwargs) -> dict:
        raise AssertionError("resolver should not be called")

    monkeypatch.setattr("agent_context.codex_hook.resolve_context", fail_if_called)

    result = build_codex_preflight(
        tmp_path / "out",
        "prepare local coding task",
        source_scope="all",
        limit=0,
        auto_context=False,
        mode="arena",
    )

    assert result["status"] == "disabled"
    assert result["auto_context"] is False
    assert result["mode"] == "arena"
    assert result["limit"] == 1
    assert result["paths"] == {}
    assert result["context_md_path"] is None
    assert result["model_input_md_path"] is None
    assert Path(result["preflight_markdown_path"]).exists()
    assert "auto_context: false" in result["preflight_markdown"]
    assert "no resolver pack was generated" in result["preflight_markdown"]


def test_codex_preflight_returns_fallback_markdown_on_resolver_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fake_resolve_context(*_args, **_kwargs) -> dict:
        raise RuntimeError("index is missing")

    monkeypatch.setattr("agent_context.codex_hook.resolve_context", fake_resolve_context)

    result = build_codex_preflight(
        tmp_path / "out",
        "prepare local coding task",
        source_scope="codexSessions",
        limit=3,
        auto_context=True,
        mode="unknown",
    )

    assert result["status"] == "resolver_failed"
    assert result["mode"] == "fast"
    assert result["requested_mode"] == "unknown"
    assert result["fallback"] == "continue_without_context"
    assert result["paths"] == {}
    assert result["model_input_md_path"] is None
    assert Path(result["preflight_markdown_path"]).exists()
    assert "status: resolver_failed" in result["preflight_markdown"]
    assert "index is missing" in result["preflight_markdown"]


def test_codex_preflight_cli_can_emit_disabled_preflight(tmp_path: Path, capsys) -> None:
    assert main(
        [
            "codex-preflight",
            "--goal",
            "prepare local coding task",
            "--out",
            str(tmp_path / "out"),
            "--no-auto-context",
        ]
    ) == 0

    result = json.loads(capsys.readouterr().out)

    assert result["status"] == "disabled"
    assert Path(result["preflight_markdown_path"]).exists()
    assert "Codex Preflight" in Path(result["preflight_markdown_path"]).read_text(encoding="utf-8")
