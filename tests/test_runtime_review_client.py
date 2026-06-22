from __future__ import annotations

import json
from pathlib import Path

from agent_context.cli import main
from agent_context.mcp_server import mcp_doctor_runtime_review_client
from agent_context.runtime_review_client import export_runtime_review_client
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
