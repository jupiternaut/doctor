from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agent_context.cli import main
from agent_context.execution_review import run_execution_review
from agent_context.io import read_jsonl


def write_answer_review(out: Path, *, status: str = "approved", session_id: str = "session-execute") -> Path:
    session_dir = out / "runtime" / "sessions" / session_id
    session_dir.mkdir(parents=True)
    answer_packet = session_dir / "answer_packet.md"
    answer_md = session_dir / "answer.md"
    answer_packet.write_text("# Doctor Answer Review\n", encoding="utf-8")
    answer_md.write_text("# Recorded Answer\n\nRun the local artifact command.", encoding="utf-8")
    path = session_dir / "answer_review.json"
    path.write_text(
        json.dumps(
            {
                "answer_review_version": "0.1",
                "stage": "answer_review",
                "status": status,
                "session_id": session_id,
                "answer_packet_md_path": str(answer_packet),
                "answer_md_path": str(answer_md),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_execution_review_requires_approved_answer(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_answer_review(out, status="pending_review")

    with pytest.raises(ValueError, match="answer_review must be approved"):
        run_execution_review(out, action="prepare", session_id="session-execute")

    assert not (out / "runtime" / "sessions" / "session-execute" / "execution_review.json").exists()


def test_execution_review_runs_command_and_records_artifacts(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_answer_review(out, status="approved")

    prepared = run_execution_review(out, action="prepare", session_id="session-execute", reason="answer approved")
    assert prepared["status"] == "awaiting_execution"
    assert Path(prepared["execution_report_md_path"]).exists()

    command = f"{sys.executable} -c \"print('doctor ' + 'artifact')\""
    executed = run_execution_review(
        out,
        action="run",
        session_id="session-execute",
        command=command,
        cwd=tmp_path,
        timeout_seconds=10,
        reason="smoke command",
    )

    assert executed["status"] == "executed"
    assert executed["last_returncode"] == 0
    assert executed["commands"]
    latest = executed["commands"][-1]
    assert Path(latest["stdout_path"]).read_text(encoding="utf-8").strip() == "doctor artifact"
    assert Path(latest["stderr_path"]).read_text(encoding="utf-8") == ""
    assert Path(latest["result_json_path"]).exists()
    report = Path(executed["execution_report_md_path"]).read_text(encoding="utf-8")
    assert "doctor artifact" not in report
    assert "Return code: `0`" in report

    approved = run_execution_review(out, action="approve", session_id="session-execute", reason="artifact accepted")
    assert approved["status"] == "approved"
    rejected = run_execution_review(out, action="reject", session_id="session-execute", reason="needs rerun")
    assert rejected["status"] == "rejected"

    feedback = read_jsonl(out / "feedback" / "execution_review_feedback.jsonl")
    assert [event["action"] for event in feedback] == ["approve", "reject"]
    assert feedback[-1]["last_returncode"] == 0


def test_execution_review_records_external_artifact(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_answer_review(out, status="approved")
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("external result", encoding="utf-8")

    recorded = run_execution_review(out, action="record", session_id="session-execute", artifact_file=artifact)

    assert recorded["status"] == "pending_review"
    assert recorded["external_artifacts"][0]["path"] == str(artifact.resolve())
    assert recorded["external_artifacts"][0]["size_bytes"] == len("external result")


def test_execution_review_cli_reports_unapproved_answer_as_json_error(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out"
    write_answer_review(out, status="pending_review", session_id="session-unapproved-execute")

    assert main(
        [
            "execution-review",
            "--out",
            str(out),
            "--session-id",
            "session-unapproved-execute",
            "--action",
            "prepare",
        ]
    ) == 1

    result = json.loads(capsys.readouterr().out)

    assert result["status"] == "error"
    assert result["command"] == "execution-review"
    assert "answer_review must be approved" in result["error"]
