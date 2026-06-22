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


def test_lab_comparison_task_builds_two_slot_context(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    image_path = tmp_path / "resume.png"
    image_path.write_bytes(ONE_PIXEL_PNG)
    write_jsonl(out / "manifests" / "projects.jsonl", [])
    write_jsonl(out / "manifests" / "sessions.jsonl", [])
    write_jsonl(
        out / "manifests" / "workflows.jsonl",
        [
            {
                "provider": "workflow_doc",
                "path": "/Users/example/workflows/doctor.md",
                "relative_path": "doctor.md",
                "workflow_id": "workflow:doctor",
                "title": "Doctor Context Runtime",
                "text": "Doctor Codex Context Resolver Evidence DB MCP feedback rerank cold index hot pack.",
            }
        ],
    )

    def fake_extract_resume(attachments, run_dir):
        return {
            "resume_schema_version": "0.1",
            "provider": "doctor_resume_ocr",
            "source_group": "lab_inputs",
            "attachments": attachments,
            "ocr": [{"status": "ok", "engine": "test_ocr", "text": "求职意向 AI 应用实习生 LangChain FAISS"}],
            "ocr_text": "求职意向：AI 应用实习生\n项目：双模式 RAG 智能问答系统\n技术：Python FastAPI LangChain FAISS",
            "target_role": "AI 应用实习生",
            "technologies": ["Python", "FastAPI", "LangChain", "FAISS"],
            "projects": ["项目：双模式 RAG 智能问答系统"],
            "education": [],
            "sections": [],
            "limits": [],
            "markdown": "# Resume OCR Evidence\n\n- Target role: AI 应用实习生\n- Technologies: Python, FastAPI, LangChain, FAISS\n",
        }

    monkeypatch.setattr("agent_context.lab.extract_resume_from_attachments", fake_extract_resume)

    result = run_lab_once(
        out,
        text="我codex的项目和这个人的简历比起来有什么区别",
        image_paths=[str(image_path)],
        limit=4,
    )

    assert result["task_type"] == "comparison"
    assert result["resume"]["target_role"] == "AI 应用实习生"
    context = Path(result["context_md_path"]).read_text(encoding="utf-8")
    assert "left_user_projects" in context
    assert "right_resume" in context
    assert "AI 应用实习生" in context
    assert "Doctor Codex Context Resolver" in context
    assert str(image_path.resolve()) not in Path(result["resolution_plan_json_path"]).read_text(encoding="utf-8")
    sources = read_jsonl(Path(result["sources_jsonl_path"]))
    assert {source.get("slot") for source in sources} >= {"left_user_projects", "right_resume"}
