from __future__ import annotations

import json
from pathlib import Path

from agent_context.cli import main
from agent_context.mcp_server import mcp_doctor_runtime_task
from agent_context.runtime_task import start_runtime_task


def test_runtime_task_starts_clarify_review_without_index_access(tmp_path: Path) -> None:
    out = tmp_path / "out"

    result = start_runtime_task(
        out,
        "我要审查 Doctor 第一次喂给模型前的需求归一化",
        session_id="runtime-task",
        port=9876,
    )

    assert result["status"] == "awaiting_context_generation"
    assert result["stage"] == "clarify_review"
    assert result["doctor_access"] is False
    assert result["resolver_allowed"] is False
    assert result["index_access_allowed"] is False
    assert result["review_file"].endswith("refined_prompt.md")
    assert result["review_server_url"] == "http://127.0.0.1:9876/"
    assert "runtime-review-server" in result["start_server_command"]
    assert result["open_client_command"].startswith("open ")
    assert Path(result["runtime_task_json_path"]).exists()
    assert Path(result["runtime_task_md_path"]).exists()
    assert Path(result["agent_preflight"]["agent_preflight_md_path"]).exists()
    assert Path(result["review_launch"]["review_launch_md_path"]).exists()
    assert Path(result["client_html_path"]).exists()
    assert not (out / "packs").exists()
    assert not (out / "indexes").exists()


def test_runtime_task_cli_and_mcp(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out"

    assert main(
        [
            "runtime-task",
            "--out",
            str(out),
            "--goal",
            "让 Codex++ 先审查提示词再访问 Doctor",
            "--session-id",
            "runtime-task-cli",
            "--port",
            "9877",
        ]
    ) == 0
    cli_result = json.loads(capsys.readouterr().out)

    assert cli_result["session_id"] == "runtime-task-cli"
    assert cli_result["review_server_url"] == "http://127.0.0.1:9877/"
    assert Path(cli_result["runtime_task_md_path"]).exists()

    mcp_result = mcp_doctor_runtime_task(
        goal="让 Warp 先审查提示词再访问 Doctor",
        session_id="runtime-task-mcp",
        port=9878,
        out_root=str(out),
    )

    assert mcp_result["mcp_version"] == "0.1"
    assert mcp_result["session_id"] == "runtime-task-mcp"
    assert mcp_result["review_server_url"] == "http://127.0.0.1:9878/"
    assert Path(mcp_result["runtime_task_json_path"]).exists()
