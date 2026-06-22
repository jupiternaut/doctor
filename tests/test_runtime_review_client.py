from __future__ import annotations

import json
from pathlib import Path

from agent_context.cli import main
from agent_context.mcp_server import mcp_doctor_runtime_review_client, mcp_doctor_runtime_review_launch
from agent_context.runtime_review_client import export_runtime_review_client, export_runtime_review_launch
from agent_context.runtime_vm import start_runtime_session


def test_runtime_review_client_exports_embeddable_files(tmp_path: Path) -> None:
    out = tmp_path / "out"
    start_runtime_session(out, "导出 Codex++ 可嵌入审查客户端", session_id="review-client")

    result = export_runtime_review_client(
        out,
        "review-client",
        review_server_url="http://127.0.0.1:9876",
    )

    assert result["status"] == "ready"
    assert result["review_server_url"] == "http://127.0.0.1:9876/"
    assert result["api"]["session_endpoint"] == "http://127.0.0.1:9876/api/session"
    assert result["runtime_status"] == "awaiting_context_generation"
    manifest_path = Path(result["files"]["manifest"])
    html_path = Path(result["files"]["html"])
    js_path = Path(result["files"]["javascript"])
    contract_path = Path(result["files"]["api_contract"])
    assert manifest_path.exists()
    assert html_path.exists()
    assert js_path.exists()
    assert contract_path.exists()
    assert "Doctor Runtime Review Client" in html_path.read_text(encoding="utf-8")
    assert "DoctorRuntimeReviewClient" in js_path.read_text(encoding="utf-8")
    assert "/api/session" in contract_path.read_text(encoding="utf-8")


def test_runtime_review_launch_exports_commands_and_client(tmp_path: Path) -> None:
    out = tmp_path / "out"
    start_runtime_session(out, "导出 Doctor 审查启动合约", session_id="review-launch")

    result = export_runtime_review_launch(out, "review-launch", host="127.0.0.1", port=9876)

    assert result["status"] == "ready"
    assert result["review_server_url"] == "http://127.0.0.1:9876/"
    assert result["api_session_url"] == "http://127.0.0.1:9876/api/session"
    assert "runtime-review-server" in result["start_server_command"]
    assert "runtime-review-client" in result["export_client_command"]
    assert result["open_client_command"].startswith("open ")
    assert Path(result["files"]["launch_json"]).exists()
    assert Path(result["files"]["launch_md"]).exists()
    assert Path(result["files"]["client_html"]).exists()
    assert "Doctor Runtime Review Launch" in Path(result["files"]["launch_md"]).read_text(encoding="utf-8")


def test_runtime_review_client_cli_and_mcp(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out"
    start_runtime_session(out, "CLI 和 MCP 导出审查客户端", session_id="review-client-cli")

    assert main(
        [
            "runtime-review-client",
            "--out",
            str(out),
            "--session-id",
            "review-client-cli",
            "--review-server-url",
            "http://127.0.0.1:9999/",
        ]
    ) == 0
    cli_result = json.loads(capsys.readouterr().out)

    assert cli_result["review_server_url"] == "http://127.0.0.1:9999/"
    assert Path(cli_result["files"]["html"]).exists()

    mcp_result = mcp_doctor_runtime_review_client(
        "review-client-cli",
        review_server_url="http://127.0.0.1:7777",
        out_root=str(out),
    )

    assert mcp_result["mcp_version"] == "0.1"
    assert mcp_result["review_server_url"] == "http://127.0.0.1:7777/"
    assert Path(mcp_result["files"]["javascript"]).exists()

    assert main(
        [
            "runtime-review-launch",
            "--out",
            str(out),
            "--session-id",
            "review-client-cli",
            "--port",
            "9876",
        ]
    ) == 0
    launch_result = json.loads(capsys.readouterr().out)

    assert launch_result["review_server_url"] == "http://127.0.0.1:9876/"
    assert Path(launch_result["files"]["launch_md"]).exists()

    mcp_launch = mcp_doctor_runtime_review_launch(
        "review-client-cli",
        port=9877,
        out_root=str(out),
    )

    assert mcp_launch["mcp_version"] == "0.1"
    assert mcp_launch["api_action_url"] == "http://127.0.0.1:9877/api/action"
