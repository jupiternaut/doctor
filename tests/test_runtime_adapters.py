from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent_context.cli import main
from agent_context.runtime_adapters import export_runtime_adapter_package
from agent_context.runtime_vm import start_runtime_session


def test_runtime_adapter_package_exports_target_files(tmp_path: Path) -> None:
    out = tmp_path / "out"
    started = start_runtime_session(out, "给 Codex++ 导出 Doctor runtime adapter", session_id="adapter-test")

    manifest = export_runtime_adapter_package(
        out,
        "adapter-test",
        targets=["codex-plus", "mcp"],
        agent_command="cat",
        review_port=9876,
    )

    assert manifest["status"] == "ready"
    assert manifest["targets"] == ["codex-plus", "mcp"]
    assert manifest["review_server_url"] == "http://127.0.0.1:9876/"
    assert manifest["files"]["doctor_session_md_path"] == started["files"]["doctor_session_md_path"]
    manifest_path = Path(manifest["adapter_files"]["manifest"])
    overview_path = Path(manifest["adapter_files"]["overview"])
    env_path = Path(manifest["adapter_files"]["env"])
    wrapper_path = Path(manifest["adapter_files"]["codex_cli_wrapper"])
    assert manifest_path.exists()
    assert overview_path.exists()
    assert env_path.exists()
    assert wrapper_path.exists()
    assert os.access(env_path, os.X_OK)
    assert os.access(wrapper_path, os.X_OK)
    assert Path(manifest["adapter_files"]["codex-plus_doc"]).exists()
    assert Path(manifest["adapter_files"]["mcp_doc"]).exists()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["entrypoints"]["inspect"].startswith("doctor session")
    assert data["entrypoints"]["agent_preflight_context"].startswith("doctor agent-preflight")
    assert data["mcp_tool_sequence"][0]["tool"] == "doctor_agent_preflight"
    assert "Doctor Runtime Adapter" in overview_path.read_text(encoding="utf-8")


def test_runtime_adapter_rejects_unknown_target(tmp_path: Path) -> None:
    out = tmp_path / "out"
    start_runtime_session(out, "导出未知 adapter", session_id="adapter-test")

    with pytest.raises(ValueError, match="unknown runtime adapter target"):
        export_runtime_adapter_package(out, "adapter-test", targets=["unknown"])


def test_runtime_adapter_cli_exports_manifest(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out"
    start_runtime_session(out, "CLI 导出 runtime adapter", session_id="adapter-cli")

    assert main(
        [
            "runtime-adapter",
            "--out",
            str(out),
            "--session-id",
            "adapter-cli",
            "--target",
            "codex-cli",
            "--agent-command",
            "cat",
        ]
    ) == 0

    result = json.loads(capsys.readouterr().out)

    assert result["status"] == "ready"
    assert result["targets"] == ["codex-cli"]
    assert Path(result["adapter_files"]["manifest"]).exists()
