from __future__ import annotations

import json
from pathlib import Path

from agent_context.agent_preflight import run_agent_preflight
from agent_context.cli import main
from agent_context.mcp_server import mcp_doctor_agent_preflight
from agent_context.runtime_vm import start_runtime_session


def write_fake_context_review(out: Path, session_id: str) -> dict:
    session_dir = out / "runtime" / "sessions" / session_id
    pack = out / "packs" / "agent-preflight"
    pack.mkdir(parents=True)
    model_input = pack / "model_input.md"
    context_md = pack / "context.md"
    sources = pack / "sources.jsonl"
    manifest = pack / "manifest.json"
    plan = pack / "resolution_plan.json"
    preflight = pack / "codex_preflight.md"
    model_input.write_text("# Doctor Model Input Review\n", encoding="utf-8")
    context_md.write_text("# Context\n", encoding="utf-8")
    sources.write_text("", encoding="utf-8")
    manifest.write_text("{}", encoding="utf-8")
    plan.write_text("{}", encoding="utf-8")
    preflight.write_text("# Preflight\n", encoding="utf-8")
    review = {
        "context_review_version": "0.1",
        "stage": "resolve_review",
        "status": "pending_review",
        "action": "generate",
        "session_id": session_id,
        "reason": "",
        "refined_prompt_md_path": str(session_dir / "refined_prompt.md"),
        "refined_prompt": "任务目标：审查 Doctor 给模型的上下文",
        "source_scope": "all",
        "limit": 3,
        "mode": "fast",
        "preflight": {
            "status": "ok",
            "task_id": "agent-preflight",
            "model_input_md_path": str(model_input),
            "context_md_path": str(context_md),
            "sources_jsonl_path": str(sources),
            "manifest_json_path": str(manifest),
            "resolution_plan_json_path": str(plan),
            "preflight_markdown_path": str(preflight),
        },
        "context_review_json_path": str(session_dir / "context_review.json"),
        "context_review_md_path": str(session_dir / "context_review.md"),
        "events_jsonl_path": str(session_dir / "context_review_events.jsonl"),
        "global_feedback_jsonl_path": str(out / "feedback" / "context_review_feedback.jsonl"),
    }
    (session_dir / "context_review.json").write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    (session_dir / "context_review.md").write_text("# Context Review\n", encoding="utf-8")
    return review


def test_agent_preflight_clarify_is_no_index_client_contract(tmp_path: Path) -> None:
    out = tmp_path / "out"

    result = run_agent_preflight(
        out,
        advance="clarify",
        goal="我想先审查 Doctor 喂给模型的上下文",
        session_id="agent-preflight-clarify",
    )

    assert result["status"] == "awaiting_context_generation"
    assert result["doctor_access"] is False
    assert result["resolver_allowed"] is False
    assert result["client_contract"]["safe_to_send_model"] is False
    assert result["review_file"].endswith("refined_prompt.md")
    assert Path(result["agent_preflight_json_path"]).exists()
    assert Path(result["agent_preflight_md_path"]).exists()
    assert not (out / "packs").exists()
    assert not (out / "indexes").exists()


def test_agent_preflight_context_advances_to_model_input_review(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    run_agent_preflight(out, advance="clarify", goal="审查上下文", session_id="agent-preflight-context")

    def fake_context_review(root: Path, **kwargs) -> dict:
        assert kwargs["action"] == "generate"
        assert kwargs["session_id"] == "agent-preflight-context"
        return write_fake_context_review(root, "agent-preflight-context")

    monkeypatch.setattr("agent_context.agent_preflight.run_context_review", fake_context_review)

    result = run_agent_preflight(
        out,
        advance="context",
        session_id="agent-preflight-context",
        source_scope="all",
        limit=3,
    )

    assert result["status"] == "awaiting_context_review"
    assert result["doctor_access"] is True
    assert result["resolver_allowed"] is True
    assert result["client_contract"]["safe_to_send_model"] is False
    assert result["review_file"].endswith("model_input.md")
    assert "Show model_input.md" in Path(result["agent_preflight_md_path"]).read_text(encoding="utf-8")


def test_agent_preflight_handoff_exports_adapter_after_context_approval(tmp_path: Path) -> None:
    out = tmp_path / "out"
    start_runtime_session(out, "导出给 Codex++ 的上下文", session_id="agent-preflight-handoff")
    review = write_fake_context_review(out, "agent-preflight-handoff")
    review["status"] = "approved"
    Path(review["context_review_json_path"]).write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")

    result = run_agent_preflight(
        out,
        advance="handoff",
        session_id="agent-preflight-handoff",
        agent_command="cat",
    )

    assert result["status"] == "ready_for_answer_prepare"
    assert result["client_contract"]["safe_to_send_model"] is True
    assert Path(result["agent_handoff"]["agent_handoff_md_path"]).exists()
    assert Path(result["runtime_adapter"]["manifest"]).exists()
    assert result["runtime_adapter"]["targets"] == ["codex-plus", "warp", "codex-cli", "mcp"]


def test_agent_preflight_cli_requires_goal_for_clarify(tmp_path: Path, capsys) -> None:
    assert main(["agent-preflight", "--out", str(tmp_path / "out")]) == 1

    result = json.loads(capsys.readouterr().out)

    assert result["status"] == "error"
    assert result["command"] == "agent-preflight"
    assert "goal is required" in result["error"]


def test_agent_preflight_cli_and_mcp_clarify(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out"

    assert main(
        [
            "agent-preflight",
            "--out",
            str(out),
            "--goal",
            "让 Codex++ 默认走 Doctor 审查流",
            "--session-id",
            "agent-preflight-cli",
        ]
    ) == 0
    cli_result = json.loads(capsys.readouterr().out)

    assert cli_result["session_id"] == "agent-preflight-cli"
    assert cli_result["agent_preflight_md_path"].endswith("agent_preflight.md")

    mcp_result = mcp_doctor_agent_preflight(
        advance="clarify",
        goal="让 Warp 默认走 Doctor 审查流",
        session_id="agent-preflight-mcp",
        out_root=str(out),
    )

    assert mcp_result["mcp_version"] == "0.1"
    assert mcp_result["session_id"] == "agent-preflight-mcp"
    assert Path(mcp_result["agent_preflight_md_path"]).exists()
