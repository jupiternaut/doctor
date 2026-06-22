from __future__ import annotations

import json
import sys
import threading
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from agent_context.cli import main
from agent_context.mcp_server import (
    mcp_doctor_answer_review,
    mcp_doctor_context_review,
    mcp_doctor_execution_review,
    mcp_doctor_run,
    mcp_doctor_runtime_acceptance,
    mcp_doctor_runtime_handoff,
    mcp_doctor_session,
)
from agent_context.runtime_review_server import (
    RuntimeReviewRequestHandler,
    handle_runtime_review_action,
    render_runtime_review_html,
)
from agent_context.runtime_vm import export_runtime_handoff, inspect_runtime_session, start_runtime_session


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

    assert result["status"] == "ready_for_agent_handoff"
    assert result["next"]["ready_for_next_stage"] is True
    assert "runtime-handoff" in result["next"]["commands"][0]
    assert result["files"]["model_input_md_path"] == str(model_input)
    session_markdown = Path(result["files"]["doctor_session_md_path"]).read_text(encoding="utf-8")
    assert "ready_for_agent_handoff" in session_markdown
    assert str(model_input) in session_markdown

    handoff = export_runtime_handoff(out, "doctor-vm-context")

    assert handoff["status"] == "ready_for_agent"
    assert Path(handoff["agent_handoff_md_path"]).exists()
    assert "Codex++" in Path(handoff["agent_handoff_md_path"]).read_text(encoding="utf-8")
    assert handoff["runtime_session"]["status"] == "ready_for_answer_prepare"


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

    assert main(["runtime-acceptance", "--out", str(out), "--session-id", "doctor-cli"]) == 0
    acceptance_result = json.loads(capsys.readouterr().out)

    assert acceptance_result["session_id"] == "doctor-cli"
    assert acceptance_result["ready"] is False
    assert acceptance_result["status"] == "awaiting_context_generation"
    assert Path(acceptance_result["latest_md_path"]).exists()


def test_runtime_vm_cli_handoff_requires_approved_context(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out"

    assert main(
        [
            "run",
            "--out",
            str(out),
            "--session-id",
            "doctor-cli-handoff",
            "--goal",
            "审查上下文后导出给 Codex++",
        ]
    ) == 0
    capsys.readouterr()

    assert main(["runtime-handoff", "--out", str(out), "--session-id", "doctor-cli-handoff"]) == 1
    result = json.loads(capsys.readouterr().out)

    assert result["status"] == "error"
    assert result["command"] == "runtime-handoff"
    assert "context_review.json" in result["error"]


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


def test_runtime_vm_mcp_review_tools_advance_four_stage_session(tmp_path: Path) -> None:
    out = tmp_path / "out"
    started = mcp_doctor_run("审查 Doctor runtime VM 输入", session_id="doctor-mcp-flow", out_root=str(out))
    session_dir = Path(started["session_dir"])
    pack = out / "packs" / "runtime-flow"
    pack.mkdir(parents=True)
    model_input = pack / "model_input.md"
    context_md = pack / "context.md"
    sources = pack / "sources.jsonl"
    manifest = pack / "manifest.json"
    plan = pack / "resolution_plan.json"
    model_input.write_text("# Model Input\n", encoding="utf-8")
    context_md.write_text("# Context\n", encoding="utf-8")
    sources.write_text("", encoding="utf-8")
    manifest.write_text("{}", encoding="utf-8")
    plan.write_text("{}", encoding="utf-8")
    context_review_path = session_dir / "context_review.json"
    context_review_path.write_text(
        json.dumps(
            {
                "context_review_version": "0.1",
                "stage": "resolve_review",
                "status": "pending_review",
                "action": "generate",
                "session_id": "doctor-mcp-flow",
                "reason": "",
                "refined_prompt_md_path": str(session_dir / "refined_prompt.md"),
                "refined_prompt": "任务目标：审查 Doctor runtime VM 输入",
                "source_scope": "all",
                "limit": 1,
                "mode": "fast",
                "preflight": {
                    "status": "ok",
                    "task_id": "runtime-flow",
                    "model_input_md_path": str(model_input),
                    "context_md_path": str(context_md),
                    "sources_jsonl_path": str(sources),
                    "manifest_json_path": str(manifest),
                    "resolution_plan_json_path": str(plan),
                    "preflight_markdown_path": str(pack / "codex_preflight.md"),
                },
                "context_review_json_path": str(context_review_path),
                "context_review_md_path": str(session_dir / "context_review.md"),
                "events_jsonl_path": str(session_dir / "context_review_events.jsonl"),
                "global_feedback_jsonl_path": str(out / "feedback" / "context_review_feedback.jsonl"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    context_approved = mcp_doctor_context_review(action="approve", session_id="doctor-mcp-flow", out_root=str(out))
    assert context_approved["status"] == "approved"
    assert context_approved["runtime_session"]["status"] == "ready_for_agent_handoff"

    handoff = mcp_doctor_runtime_handoff("doctor-mcp-flow", out_root=str(out))
    assert handoff["status"] == "ready_for_agent"
    assert Path(handoff["agent_handoff_md_path"]).exists()
    assert handoff["runtime_session"]["status"] == "ready_for_answer_prepare"

    answer_prepared = mcp_doctor_answer_review(action="prepare", session_id="doctor-mcp-flow", out_root=str(out))
    assert answer_prepared["status"] == "awaiting_answer"
    assert answer_prepared["runtime_session"]["status"] == "awaiting_answer_output"

    answer_command = f"{sys.executable} -c \"import sys; sys.stdin.read(); print('The approved model input is ready for execution planning.')\""
    answer_recorded = mcp_doctor_answer_review(
        action="run",
        session_id="doctor-mcp-flow",
        command=answer_command,
        cwd=str(tmp_path),
        timeout_seconds=10,
        out_root=str(out),
    )
    assert answer_recorded["status"] == "pending_review"
    assert answer_recorded["answer_runs"][-1]["returncode"] == 0
    answer_approved = mcp_doctor_answer_review(action="approve", session_id="doctor-mcp-flow", out_root=str(out))
    assert answer_approved["status"] == "approved"
    assert answer_approved["runtime_session"]["status"] == "ready_for_execution_prepare"

    execution_prepared = mcp_doctor_execution_review(action="prepare", session_id="doctor-mcp-flow", out_root=str(out))
    assert execution_prepared["status"] == "awaiting_execution"
    command = f"{sys.executable} -c \"print('runtime artifact')\""
    execution_ran = mcp_doctor_execution_review(
        action="run",
        session_id="doctor-mcp-flow",
        command=command,
        cwd=str(tmp_path),
        out_root=str(out),
    )
    assert execution_ran["status"] == "executed"
    assert execution_ran["runtime_session"]["status"] == "awaiting_execution_review"
    assert Path(execution_ran["commands"][-1]["stdout_path"]).read_text(encoding="utf-8").strip() == "runtime artifact"

    execution_approved = mcp_doctor_execution_review(action="approve", session_id="doctor-mcp-flow", out_root=str(out))
    assert execution_approved["status"] == "approved"
    assert execution_approved["runtime_session"]["status"] == "complete"

    acceptance = mcp_doctor_runtime_acceptance("doctor-mcp-flow", out_root=str(out))
    assert acceptance["ready"] is True
    assert acceptance["status"] == "complete"
    assert Path(acceptance["latest_md_path"]).exists()


def test_runtime_review_html_and_action_handler_advance_context_gate(tmp_path: Path) -> None:
    out = tmp_path / "out"
    started = start_runtime_session(out, "审查 Doctor runtime review UI", session_id="doctor-review-ui")
    session_dir = Path(started["session_dir"])
    pack = out / "packs" / "runtime-ui"
    pack.mkdir(parents=True)
    model_input = pack / "model_input.md"
    context_md = pack / "context.md"
    sources = pack / "sources.jsonl"
    model_input.write_text("# Model Input\n\nReview this payload.", encoding="utf-8")
    context_md.write_text("# Context\n", encoding="utf-8")
    sources.write_text("", encoding="utf-8")
    context_review_path = session_dir / "context_review.json"
    context_review_path.write_text(
        json.dumps(
            {
                "context_review_version": "0.1",
                "stage": "resolve_review",
                "status": "pending_review",
                "action": "generate",
                "session_id": "doctor-review-ui",
                "reason": "",
                "refined_prompt_md_path": str(session_dir / "refined_prompt.md"),
                "refined_prompt": "任务目标：审查 Doctor runtime review UI",
                "source_scope": "all",
                "limit": 1,
                "mode": "fast",
                "preflight": {
                    "status": "ok",
                    "task_id": "runtime-ui",
                    "model_input_md_path": str(model_input),
                    "context_md_path": str(context_md),
                    "sources_jsonl_path": str(sources),
                    "manifest_json_path": str(pack / "manifest.json"),
                    "resolution_plan_json_path": str(pack / "resolution_plan.json"),
                    "preflight_markdown_path": str(pack / "codex_preflight.md"),
                },
                "context_review_json_path": str(context_review_path),
                "context_review_md_path": str(session_dir / "context_review.md"),
                "events_jsonl_path": str(session_dir / "context_review_events.jsonl"),
                "global_feedback_jsonl_path": str(out / "feedback" / "context_review_feedback.jsonl"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    html = render_runtime_review_html(out, "doctor-review-ui")
    assert "Doctor Runtime Review" in html
    assert "awaiting_context_review" in html
    assert "Approve Context" in html
    assert "Review this payload." in html

    result = handle_runtime_review_action(
        out,
        "doctor-review-ui",
        action="approve_context",
        reason="context matches intent",
    )

    assert result["status"] == "ok"
    assert result["runtime_session"]["status"] == "ready_for_agent_handoff"

    handoff = handle_runtime_review_action(
        out,
        "doctor-review-ui",
        action="export_handoff",
        reason="approved context handoff",
    )

    assert handoff["runtime_session"]["status"] == "ready_for_answer_prepare"

    prepared = handle_runtime_review_action(
        out,
        "doctor-review-ui",
        action="prepare_answer",
        reason="approved context",
    )

    assert prepared["runtime_session"]["status"] == "awaiting_answer_output"
    html_after = render_runtime_review_html(out, "doctor-review-ui")
    assert "Record Answer" in html_after


def test_runtime_review_http_server_handles_clickable_context_approval(tmp_path: Path) -> None:
    out = tmp_path / "out"
    started = start_runtime_session(out, "审查 Doctor runtime HTTP UI", session_id="doctor-http-ui")
    session_dir = Path(started["session_dir"])
    pack = out / "packs" / "runtime-http"
    pack.mkdir(parents=True)
    model_input = pack / "model_input.md"
    context_md = pack / "context.md"
    sources = pack / "sources.jsonl"
    model_input.write_text("# Model Input\n\nHTTP preview.", encoding="utf-8")
    context_md.write_text("# Context\n", encoding="utf-8")
    sources.write_text("", encoding="utf-8")
    context_review_path = session_dir / "context_review.json"
    context_review_path.write_text(
        json.dumps(
            {
                "context_review_version": "0.1",
                "stage": "resolve_review",
                "status": "pending_review",
                "action": "generate",
                "session_id": "doctor-http-ui",
                "reason": "",
                "refined_prompt_md_path": str(session_dir / "refined_prompt.md"),
                "refined_prompt": "任务目标：审查 Doctor runtime HTTP UI",
                "source_scope": "all",
                "limit": 1,
                "mode": "fast",
                "preflight": {
                    "status": "ok",
                    "task_id": "runtime-http",
                    "model_input_md_path": str(model_input),
                    "context_md_path": str(context_md),
                    "sources_jsonl_path": str(sources),
                    "manifest_json_path": str(pack / "manifest.json"),
                    "resolution_plan_json_path": str(pack / "resolution_plan.json"),
                    "preflight_markdown_path": str(pack / "codex_preflight.md"),
                },
                "context_review_json_path": str(context_review_path),
                "context_review_md_path": str(session_dir / "context_review.md"),
                "events_jsonl_path": str(session_dir / "context_review_events.jsonl"),
                "global_feedback_jsonl_path": str(out / "feedback" / "context_review_feedback.jsonl"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class Handler(RuntimeReviewRequestHandler):
        out_root = out
        session_id = "doctor-http-ui"

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page = urllib.request.urlopen(base_url, timeout=5).read().decode("utf-8")
        assert "Approve Context" in page
        assert "HTTP preview." in page

        data = urllib.parse.urlencode({"action": "approve_context", "reason": "context ok"}).encode("utf-8")
        request = urllib.request.Request(f"{base_url}/action", data=data, method="POST")
        after = urllib.request.urlopen(request, timeout=5).read().decode("utf-8")
        assert "Action applied." in after
        assert "Export Agent Handoff" in after
        assert inspect_runtime_session(out, "doctor-http-ui")["status"] == "ready_for_agent_handoff"

        data = urllib.parse.urlencode({"action": "export_handoff", "reason": "context handoff ok"}).encode("utf-8")
        request = urllib.request.Request(f"{base_url}/action", data=data, method="POST")
        after_handoff = urllib.request.urlopen(request, timeout=5).read().decode("utf-8")
        assert "Prepare Answer Packet" in after_handoff
        assert inspect_runtime_session(out, "doctor-http-ui")["status"] == "ready_for_answer_prepare"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
