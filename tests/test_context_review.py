from __future__ import annotations

import json
from pathlib import Path

from agent_context.cli import main
from agent_context.context_review import run_context_review
from agent_context.io import read_jsonl


def write_refined_prompt(out: Path, session_id: str = "session-review") -> Path:
    session_dir = out / "runtime" / "sessions" / session_id
    session_dir.mkdir(parents=True)
    path = session_dir / "refined_prompt.md"
    path.write_text(
        "\n".join(
            [
                "# Doctor Clarify Review",
                "",
                "## Refined Prompt",
                "",
                "任务目标：审查 Doctor 准备喂给模型的上下文",
                "",
                "请保留原目标。",
                "",
                "## Review Questions",
                "",
                "- 是否接受？",
            ]
        ),
        encoding="utf-8",
    )
    return path


def fake_preflight(out_root: Path, goal: str, source_scope: str, limit: int, mode: str, **_kwargs) -> dict:
    pack = out_root / "packs" / "task-resolve-test"
    pack.mkdir(parents=True, exist_ok=True)
    model_input = pack / "model_input.md"
    model_input.write_text("# Doctor Model Input Review\n", encoding="utf-8")
    return {
        "status": "ok",
        "task_id": "task-resolve-test",
        "intent": "runtime_pipeline",
        "sources_included": limit,
        "context_md_path": str(pack / "context.md"),
        "sources_jsonl_path": str(pack / "sources.jsonl"),
        "manifest_json_path": str(pack / "manifest.json"),
        "resolution_plan_json_path": str(pack / "resolution_plan.json"),
        "preflight_markdown_path": str(pack / "codex_preflight.md"),
        "model_input_md_path": str(model_input),
        "source_scope": source_scope,
        "mode": mode,
        "goal": goal,
    }


def test_context_review_generates_model_input_from_refined_prompt(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    refined_prompt = write_refined_prompt(out)
    calls = {}

    def capture_preflight(out_root: Path, goal: str, source_scope: str, limit: int, mode: str, **kwargs) -> dict:
        calls.update({"goal": goal, "source_scope": source_scope, "limit": limit, "mode": mode})
        return fake_preflight(out_root, goal, source_scope, limit, mode, **kwargs)

    monkeypatch.setattr("agent_context.context_review.build_codex_preflight", capture_preflight)

    result = run_context_review(
        out,
        action="generate",
        refined_prompt_path=refined_prompt,
        source_scope="all",
        limit=7,
        mode="deep",
    )

    assert result["status"] == "pending_review"
    assert result["session_id"] == "session-review"
    assert calls["goal"] == "任务目标：审查 Doctor 准备喂给模型的上下文\n\n请保留原目标。"
    assert calls["source_scope"] == "all"
    assert calls["limit"] == 7
    assert calls["mode"] == "deep"
    assert Path(result["context_review_json_path"]).exists()
    assert Path(result["context_review_md_path"]).exists()
    assert result["preflight"]["model_input_md_path"].endswith("model_input.md")
    assert read_jsonl(Path(result["events_jsonl_path"]))[-1]["action"] == "generate"
    assert not (out / "feedback" / "context_review_feedback.jsonl").exists()


def test_context_review_approve_and_reject_do_not_regenerate(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    refined_prompt = write_refined_prompt(out)
    monkeypatch.setattr("agent_context.context_review.build_codex_preflight", fake_preflight)
    generated = run_context_review(out, action="generate", refined_prompt_path=refined_prompt)

    def fail_if_called(*_args, **_kwargs) -> dict:
        raise AssertionError("approve/reject must not regenerate preflight")

    monkeypatch.setattr("agent_context.context_review.build_codex_preflight", fail_if_called)

    approved = run_context_review(out, action="approve", session_id=generated["session_id"], reason="matches intent")
    assert approved["status"] == "approved"
    assert approved["last_review_reason"] == "matches intent"

    rejected = run_context_review(out, action="reject", session_id=generated["session_id"], reason="wrong source mix")
    assert rejected["status"] == "rejected"
    assert rejected["last_review_reason"] == "wrong source mix"

    global_events = read_jsonl(out / "feedback" / "context_review_feedback.jsonl")
    assert [event["action"] for event in global_events] == ["approve", "reject"]
    assert global_events[-1]["model_input_md_path"] == generated["preflight"]["model_input_md_path"]


def test_context_review_cli_regenerates_from_session_id(tmp_path: Path, monkeypatch, capsys) -> None:
    out = tmp_path / "out"
    write_refined_prompt(out, session_id="session-cli-review")
    monkeypatch.setattr("agent_context.context_review.build_codex_preflight", fake_preflight)

    assert main(
        [
            "context-review",
            "--out",
            str(out),
            "--session-id",
            "session-cli-review",
            "--action",
            "regenerate",
            "--source-scope",
            "gitProjects",
            "--limit",
            "5",
            "--reason",
            "narrow scope",
        ]
    ) == 0

    result = json.loads(capsys.readouterr().out)

    assert result["action"] == "regenerate"
    assert result["source_scope"] == "gitProjects"
    assert result["limit"] == 5
    assert Path(result["context_review_md_path"]).exists()
