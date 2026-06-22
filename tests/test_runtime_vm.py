from __future__ import annotations

import json
from pathlib import Path

from agent_context.cli import main
from agent_context.mcp_server import mcp_doctor_run, mcp_doctor_session
from agent_context.runtime_vm import inspect_runtime_session, start_runtime_session


def test_runtime_vm_starts_no_index_session(tmp_path: Path) -> None:
    out = tmp_path / "out"

    result = start_runtime_session(
        out,
        "我想比较我的 Codex 项目和一份 AI 应用实习生简历",
        session_id="doctor-vm-test",
    )

    assert result["status"] == "awaiting_context_generation"
    assert result["ready_for_next_stage"] is True
    assert result["started_stage"] == "clarify"
    assert result["stages"][0]["doctor_access"] is False
    assert result["stages"][0]["resolver_called"] is False
    assert result["stages"][0]["index_access"] is False
    assert Path(result["files"]["doctor_session_md_path"]).exists()
    assert Path(result["files"]["runtime_session_json_path"]).exists()
    assert Path(result["files"]["refined_prompt_md_path"]).exists()
    assert "context-review" in result["next"]["commands"][0]
    assert not (out / "packs").exists()
    assert not (out / "indexes").exists()


def test_runtime_vm_inspects_approved_context_gate(tmp_path: Path) -> None:
    out = tmp_path / "out"
    start_runtime_session(out, "审查 Doctor 要喂给模型的上下文", session_id="doctor-vm-context")
    session_dir = out / "runtime" / "sessions" / "doctor-vm-context"
    model_input = out / "packs" / "task" / "model_input.md"
    context_md = out / "packs" / "task" / "context.md"
    sources = out / "packs" / "task" / "sources.jsonl"
    model_input.parent.mkdir(parents=True)
    model_input.write_text("# Model Input\n", encoding="utf-8")
    context_md.write_text("# Context\n", encoding="utf-8")
    sources.write_text("", encoding="utf-8")
    (session_dir / "context_review.json").write_text(
        json.dumps(
            {
                "context_review_version": "0.1",
                "stage": "resolve_review",
                "status": "approved",
                "session_id": "doctor-vm-context",
                "preflight": {
                    "model_input_md_path": str(model_input),
                    "context_md_path": str(context_md),
                    "sources_jsonl_path": str(sources),
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = inspect_runtime_session(out, "doctor-vm-context")

    assert result["status"] == "ready_for_answer_prepare"
    assert result["next"]["ready_for_next_stage"] is True
    assert "answer-review" in result["next"]["commands"][0]
    assert result["files"]["model_input_md_path"] == str(model_input)
    session_markdown = Path(result["files"]["doctor_session_md_path"]).read_text(encoding="utf-8")
    assert "ready_for_answer_prepare" in session_markdown
    assert str(model_input) in session_markdown


def test_runtime_vm_cli_run_and_session(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out"

    assert main(
        [
            "run",
            "--out",
            str(out),
            "--session-id",
            "doctor-cli",
            "--goal",
            "把 Doctor 四阶段运行时封装成可审查会话",
        ]
    ) == 0
    run_result = json.loads(capsys.readouterr().out)

    assert run_result["session_id"] == "doctor-cli"
    assert run_result["status"] == "awaiting_context_generation"

    assert main(["session", "--out", str(out), "--session-id", "doctor-cli"]) == 0
    session_result = json.loads(capsys.readouterr().out)

    assert session_result["session_id"] == "doctor-cli"
    assert session_result["files"]["doctor_session_md_path"].endswith("DOCTOR_SESSION.md")


def test_runtime_vm_mcp_tools(tmp_path: Path) -> None:
    out = tmp_path / "out"

    started = mcp_doctor_run(
        "先归一化用户问题，不要访问 Doctor 索引",
        session_id="doctor-mcp",
        out_root=str(out),
    )
    inspected = mcp_doctor_session("doctor-mcp", out_root=str(out))

    assert started["mcp_version"] == "0.1"
    assert inspected["mcp_version"] == "0.1"
    assert inspected["status"] == "awaiting_context_generation"
    assert Path(inspected["files"]["doctor_session_md_path"]).exists()
