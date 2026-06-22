from __future__ import annotations

import json
from pathlib import Path

from agent_context.cli import main
from agent_context.context_review import extract_retrieval_goal, run_context_review
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


def test_extract_retrieval_goal_removes_review_process_clauses() -> None:
    refined_prompt = "\n".join(
        [
            "任务目标：我codex的项目和这个人的简历比起来有什么区别；输入包含一张简历截图，需要先归一化问题，不访问冷索引，等待用户审查后再生成上下文",
            "",
            "请在下一阶段使用 Doctor 检索本机上下文前，严格保留这个目标。",
            "任务类型：comparison",
        ]
    )

    assert extract_retrieval_goal(refined_prompt) == "我codex的项目和这个人的简历比起来有什么区别"


def test_context_review_generates_model_input_from_refined_prompt(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    refined_prompt = write_refined_prompt(out)
    calls = {}

    def capture_preflight(out_root: Path, goal: str, source_scope: str, limit: int, mode: str, **kwargs) -> dict:
        calls.update(
            {
                "goal": goal,
                "retrieval_goal": kwargs.get("retrieval_goal"),
                "source_scope": source_scope,
                "limit": limit,
                "mode": mode,
            }
        )
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
    assert calls["retrieval_goal"] == "审查 Doctor 准备喂给模型的上下文"
    assert result["retrieval_goal"] == "审查 Doctor 准备喂给模型的上下文"
    assert calls["source_scope"] == "all"
    assert calls["limit"] == 7
    assert calls["mode"] == "deep"
    assert Path(result["context_review_json_path"]).exists()
    assert Path(result["context_review_md_path"]).exists()
    markdown = Path(result["context_review_md_path"]).read_text(encoding="utf-8")
    assert "doctor context-review" in markdown
    assert "agent-context context-review" not in markdown
    assert result["preflight"]["model_input_md_path"].endswith("model_input.md")
    assert read_jsonl(Path(result["events_jsonl_path"]))[-1]["action"] == "generate"
    assert not (out / "feedback" / "context_review_feedback.jsonl").exists()


def test_context_review_uses_comparison_slots_for_resume_comparison(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    session_dir = out / "runtime" / "sessions" / "session-comparison"
    session_dir.mkdir(parents=True)
    refined_prompt = session_dir / "refined_prompt.md"
    refined_prompt.write_text(
        "\n".join(
            [
                "# Doctor Clarify Review",
                "",
                "## Refined Prompt",
                "",
                "任务目标：我codex的项目和这个人的简历比起来有什么区别；输入包含一张简历截图，需要先归一化问题，不访问冷索引，等待用户审查后再生成上下文",
                "",
                "期望输出：comparison table plus evidence-backed conclusion",
            ]
        ),
        encoding="utf-8",
    )

    def fake_resolve_context(out_root: Path, goal: str, limit: int, source_scope: str) -> dict:
        pack = out_root / "packs" / "left-resolve"
        pack.mkdir(parents=True)
        sources_path = pack / "sources.jsonl"
        context_path = pack / "context.md"
        sources = [
            {
                "path": "/Users/gengrf/agent-context-system/docs/DOCTOR_RUNTIME_VM.md",
                "source_group": "git_repositories",
                "source_id": "doctor-runtime-vm",
                "snippet": "Doctor Context Resolver Evidence DB MCP feedback cold index hot pack.",
                "score": 0.8,
            },
            {
                "path": "/Users/gengrf/Code/random/README.md",
                "source_group": "git_repositories",
                "source_id": "random",
                "snippet": "Random local repo.",
                "score": 0.9,
            },
        ]
        sources_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in sources) + "\n", encoding="utf-8")
        context_path.write_text("# Left Context\n", encoding="utf-8")
        return {
            "resolver_version": "0.5",
            "route": "rule_based_v0",
            "task_id": "left-resolve",
            "goal": goal,
            "intent": "project_code",
            "source_scope": source_scope,
            "selected_sources": ["git_repositories"],
            "queries": [goal],
            "context_md_path": str(context_path),
            "sources_jsonl_path": str(sources_path),
            "manifest_json_path": str(pack / "manifest.json"),
            "resolution_plan_json_path": str(pack / "resolution_plan.json"),
            "sources_included": 2,
        }

    monkeypatch.setattr("agent_context.context_review.resolve_context", fake_resolve_context)

    result = run_context_review(
        out,
        action="generate",
        refined_prompt_path=refined_prompt,
        session_id="session-comparison",
        limit=4,
    )

    assert result["status"] == "pending_review"
    assert result["retrieval_goal"] == "我codex的项目和这个人的简历比起来有什么区别"
    assert result["preflight"]["intent"] == "comparison"
    context = Path(result["preflight"]["context_md_path"]).read_text(encoding="utf-8")
    model_input = Path(result["preflight"]["model_input_md_path"]).read_text(encoding="utf-8")
    sources = read_jsonl(Path(result["preflight"]["sources_jsonl_path"]))

    assert "left_user_projects" in context
    assert "right_resume" in context
    assert "No resume OCR text is available" in context
    assert "Compare the two slots" in model_input
    assert {source.get("slot") for source in sources} >= {"left_user_projects", "right_resume"}
    assert "agent-context-system" in sources[1]["path"]
    assert "# Doctor Clarify Review" not in context


def test_context_review_uses_session_attachment_resume_ocr(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    session_dir = out / "runtime" / "sessions" / "session-image-comparison"
    session_dir.mkdir(parents=True)
    image_path = tmp_path / "resume.jpg"
    image_path.write_bytes(b"fake-image")
    (session_dir / "attachments.json").write_text(
        json.dumps(
            [
                {
                    "path": str(image_path.resolve()),
                    "name": "resume.jpg",
                    "source_type": "image",
                    "exists": True,
                    "status": "ok",
                    "sha256": "abc123",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    refined_prompt = session_dir / "refined_prompt.md"
    refined_prompt.write_text(
        "\n".join(
            [
                "# Doctor Clarify Review",
                "",
                "## Refined Prompt",
                "",
                "任务目标：我codex的项目和这个人的简历比起来有什么区别",
            ]
        ),
        encoding="utf-8",
    )

    def fake_resolve_context(out_root: Path, goal: str, limit: int, source_scope: str) -> dict:
        pack = out_root / "packs" / "left-resolve-image"
        pack.mkdir(parents=True)
        sources_path = pack / "sources.jsonl"
        sources_path.write_text(
            json.dumps(
                {
                    "path": "/Users/gengrf/agent-context-system/docs/DOCTOR_RUNTIME_VM.md",
                    "source_group": "git_repositories",
                    "source_id": "doctor-runtime-vm",
                    "snippet": "Doctor Context Resolver MCP cold index hot pack.",
                    "score": 1.0,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        context_path = pack / "context.md"
        context_path.write_text("# Left Context\n", encoding="utf-8")
        return {
            "resolver_version": "0.5",
            "task_id": "left-resolve-image",
            "selected_sources": ["git_repositories"],
            "queries": [goal],
            "context_md_path": str(context_path),
            "sources_jsonl_path": str(sources_path),
            "manifest_json_path": str(pack / "manifest.json"),
            "resolution_plan_json_path": str(pack / "resolution_plan.json"),
        }

    def fake_extract_resume(attachments, run_dir):
        return {
            "resume_schema_version": "0.1",
            "provider": "doctor_resume_ocr",
            "source_group": "lab_inputs",
            "attachments": attachments,
            "ocr": [{"status": "ok", "engine": "test", "text": "求职意向 AI 应用实习生"}],
            "target_role": "AI 应用实习生",
            "technologies": ["Python", "FastAPI", "LangChain", "FAISS"],
            "limits": [],
            "markdown": "# Resume OCR Evidence\n\n- Target role: AI 应用实习生\n- Technologies: Python, FastAPI, LangChain, FAISS\n",
            "resume_json_path": str(run_dir / "resume.json"),
            "resume_md_path": str(run_dir / "resume.md"),
        }

    monkeypatch.setattr("agent_context.context_review.resolve_context", fake_resolve_context)
    monkeypatch.setattr("agent_context.context_review.extract_resume_from_attachments", fake_extract_resume)

    result = run_context_review(out, action="generate", session_id="session-image-comparison", limit=4)

    context = Path(result["preflight"]["context_md_path"]).read_text(encoding="utf-8")
    sources = read_jsonl(Path(result["preflight"]["sources_jsonl_path"]))

    assert result["preflight"]["intent"] == "comparison"
    assert result["preflight"]["sources_included"] == 2
    assert "AI 应用实习生" in context
    assert "left_user_projects" in context
    assert "right_resume" in context
    assert sources[0]["slot"] == "right_resume"
    assert sources[0]["path"] == str(image_path.resolve())


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
