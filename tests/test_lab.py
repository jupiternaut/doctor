from __future__ import annotations

import base64
from pathlib import Path

from agent_context.io import read_jsonl, write_jsonl
from agent_context.lab import record_lab_feedback, resolver_goal_for, run_lab_once


ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def test_lab_once_accepts_text_and_image_attachment(tmp_path: Path) -> None:
    out = tmp_path / "out"
    image_path = tmp_path / "input.png"
    image_path.write_bytes(ONE_PIXEL_PNG)
    write_jsonl(out / "manifests" / "projects.jsonl", [])
    write_jsonl(out / "manifests" / "sessions.jsonl", [])
    write_jsonl(
        out / "manifests" / "workflows.jsonl",
        [
            {
                "provider": "workflow_doc",
                "path": "/Users/example/workflows/recommender.md",
                "relative_path": "recommender.md",
                "workflow_id": "workflow:recommender",
                "title": "Recommendation Workflow",
                "text": "个人推荐系统 feedback rerank lab evidence.",
            }
        ],
    )

    result = run_lab_once(
        out,
        text="告诉我如何构建个人推荐系统",
        image_paths=[str(image_path)],
        limit=3,
    )

    assert result["status"] == "ok"
    assert result["images"][0]["source_type"] == "image"
    assert result["images"][0]["width"] == 1
    assert result["images"][0]["height"] == 1
    assert Path(result["input_md_path"]).exists()
    assert str(image_path.resolve()) in Path(result["context_md_path"]).read_text(encoding="utf-8")
    assert str(image_path.resolve()) not in Path(result["resolution_plan_json_path"]).read_text(encoding="utf-8")
    assert "attachment_hint: resume_image" in resolver_goal_for("比较我的项目和这份简历", result["images"])
    attachments = read_jsonl(Path(result["attachments_jsonl_path"]))
    assert attachments[0]["source_group"] == "lab_inputs"
    assert result["top_sources"]


def test_lab_feedback_records_panel_feedback(tmp_path: Path) -> None:
    out = tmp_path / "out"
    lab_result = {
        "run_id": "lab-test",
        "run_json_path": str(out / "lab" / "runs" / "lab-test" / "run.json"),
        "context_md_path": str(out / "packs" / "task" / "context.md"),
        "top_sources": [
            {
                "rank": 1,
                "source_id": "workflow:recommender",
                "path": "/Users/example/workflows/recommender.md",
            }
        ],
    }

    result = record_lab_feedback(out, lab_result, "1", rating="useful", reason="matches intent")

    assert result["rating"] == "useful"
    panel_feedback = read_jsonl(out / "feedback" / "panel_feedback.jsonl")
    lab_feedback = read_jsonl(out / "feedback" / "lab_feedback.jsonl")
    assert panel_feedback[-1]["selected_source"] == "workflow:recommender"
    assert lab_feedback[-1]["source"] == "workflow:recommender"
