from __future__ import annotations

import json
from pathlib import Path

from agent_context.clarify import build_clarification
from agent_context.cli import main


def test_clarify_writes_reviewable_prompt_without_doctor_access(tmp_path: Path) -> None:
    result = build_clarification(
        tmp_path / "out",
        "我想比较我的 Codex 项目和一份 AI 应用实习生简历",
        session_id="session-test",
    )

    assert result["status"] == "ok"
    assert result["doctor_access"] is False
    assert result["resolver_called"] is False
    assert result["index_access"] is False
    assert result["intent"] == "comparison"
    assert result["source_scope_hint"] == "all"
    assert Path(result["clarify_json_path"]).exists()
    assert Path(result["refined_prompt_md_path"]).exists()

    payload = json.loads(Path(result["clarify_json_path"]).read_text(encoding="utf-8"))
    markdown = Path(result["refined_prompt_md_path"]).read_text(encoding="utf-8")

    assert payload["session_id"] == "session-test"
    assert "任务目标：我想比较我的 Codex 项目和一份 AI 应用实习生简历" in markdown
    assert "does not read Doctor indexes" in markdown
    assert "agent-context codex-preflight" in markdown
    assert not (tmp_path / "out" / "packs").exists()
    assert not (tmp_path / "out" / "indexes").exists()


def test_clarify_cli_does_not_call_resolver(tmp_path: Path, monkeypatch, capsys) -> None:
    def fail_if_called(*_args, **_kwargs) -> dict:
        raise AssertionError("clarify must not call resolver")

    monkeypatch.setattr("agent_context.cli.resolve_context", fail_if_called)

    assert main(
        [
            "clarify",
            "--goal",
            "告诉我如何审查 Doctor 准备喂给模型的上下文",
            "--out",
            str(tmp_path / "out"),
            "--session-id",
            "session-cli",
        ]
    ) == 0

    result = json.loads(capsys.readouterr().out)

    assert result["session_id"] == "session-cli"
    assert result["doctor_access"] is False
    assert Path(result["refined_prompt_md_path"]).exists()


def test_clarify_detects_four_stage_runtime_pipeline(tmp_path: Path) -> None:
    result = build_clarification(
        tmp_path / "out",
        "用户先把自然语言问题归一化成好提示词，确认后注入 Doctor 冷热索引生成上下文，再让模型回答，最后产出 artifacts",
        session_id="session-runtime",
    )

    assert result["intent"] == "runtime_pipeline"
    assert result["source_scope_hint"] == "all"
    assert "four-stage runtime packet" in result["expected_output"]
    assert "Doctor runtime docs" in result["evidence_need"]
