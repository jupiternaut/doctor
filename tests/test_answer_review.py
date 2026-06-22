from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_context.answer_review import run_answer_review
from agent_context.cli import main
from agent_context.io import read_jsonl


def write_context_review(out: Path, *, status: str = "approved", session_id: str = "session-answer") -> Path:
    session_dir = out / "runtime" / "sessions" / session_id
    session_dir.mkdir(parents=True)
    model_input = out / "packs" / "task" / "model_input.md"
    model_input.parent.mkdir(parents=True)
    model_input.write_text("# Doctor Model Input Review\n", encoding="utf-8")
    path = session_dir / "context_review.json"
    path.write_text(
        json.dumps(
            {
                "context_review_version": "0.1",
                "stage": "resolve_review",
                "status": status,
                "session_id": session_id,
                "refined_prompt": "任务目标：回答 Doctor 四阶段运行时如何工作",
                "preflight": {
                    "model_input_md_path": str(model_input),
                    "context_md_path": str(out / "packs" / "task" / "context.md"),
                    "sources_jsonl_path": str(out / "packs" / "task" / "sources.jsonl"),
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_answer_review_requires_approved_context(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_context_review(out, status="pending_review")

    with pytest.raises(ValueError, match="must be approved"):
        run_answer_review(out, action="prepare", session_id="session-answer")

    assert not (out / "runtime" / "sessions" / "session-answer" / "answer_review.json").exists()


def test_answer_review_prepare_record_and_feedback(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_context_review(out, status="approved")

    prepared = run_answer_review(out, action="prepare", session_id="session-answer", reason="approved context")

    assert prepared["status"] == "awaiting_answer"
    assert Path(prepared["answer_packet_md_path"]).exists()
    packet = Path(prepared["answer_packet_md_path"]).read_text(encoding="utf-8")
    assert "approved `model_input.md`" in packet
    assert prepared["model_input_md_path"].endswith("model_input.md")

    recorded = run_answer_review(
        out,
        action="record",
        session_id="session-answer",
        answer_text="Doctor 现在有 clarify、context review、answer review 三段。",
        reason="model output pasted",
    )

    assert recorded["status"] == "pending_review"
    assert Path(recorded["answer_md_path"]).exists()
    assert "clarify、context review、answer review" in Path(recorded["answer_md_path"]).read_text(encoding="utf-8")

    approved = run_answer_review(out, action="approve", session_id="session-answer", reason="answer matches intent")
    assert approved["status"] == "approved"

    rejected = run_answer_review(out, action="reject", session_id="session-answer", reason="needs tighter source citation")
    assert rejected["status"] == "rejected"

    feedback = read_jsonl(out / "feedback" / "answer_review_feedback.jsonl")
    assert [event["action"] for event in feedback] == ["approve", "reject"]
    assert feedback[-1]["answer_packet_md_path"] == prepared["answer_packet_md_path"]


def test_answer_review_cli_records_answer_from_file(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out"
    write_context_review(out, status="approved", session_id="session-cli-answer")
    run_answer_review(out, action="prepare", session_id="session-cli-answer")
    answer_file = tmp_path / "answer.md"
    answer_file.write_text("这是从 Codex++ 或 Warp 产出的答案。", encoding="utf-8")

    assert main(
        [
            "answer-review",
            "--out",
            str(out),
            "--session-id",
            "session-cli-answer",
            "--action",
            "record",
            "--answer-file",
            str(answer_file),
            "--reason",
            "captured from external agent",
        ]
    ) == 0

    result = json.loads(capsys.readouterr().out)

    assert result["status"] == "pending_review"
    assert result["answer_source_path"] == str(answer_file.resolve())
    assert "外部" not in Path(result["answer_md_path"]).read_text(encoding="utf-8")
    assert "Codex++ 或 Warp" in Path(result["answer_md_path"]).read_text(encoding="utf-8")


def test_answer_review_cli_reports_unapproved_context_as_json_error(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out"
    write_context_review(out, status="pending_review", session_id="session-unapproved")

    assert main(
        [
            "answer-review",
            "--out",
            str(out),
            "--session-id",
            "session-unapproved",
            "--action",
            "prepare",
        ]
    ) == 1

    result = json.loads(capsys.readouterr().out)

    assert result["status"] == "error"
    assert result["command"] == "answer-review"
    assert "must be approved" in result["error"]
