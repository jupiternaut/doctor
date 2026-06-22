from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .io import ensure_dir, write_text
from .pack import slugify


CLARIFY_VERSION = "0.1"


def build_clarification(
    out_root: str | Path,
    goal: str,
    *,
    session_id: str | None = None,
    mode: str = "standard",
) -> dict[str, Any]:
    root = Path(out_root).expanduser().resolve()
    normalized_goal = normalize_goal(goal)
    now = datetime.now().astimezone()
    session = session_id or f"session-{slugify(normalized_goal)}-{now.strftime('%Y%m%d%H%M%S%f')}"
    session_dir = ensure_dir(root / "runtime" / "sessions" / session)
    clarify_json_path = session_dir / "clarify.json"
    refined_prompt_path = session_dir / "refined_prompt.md"

    profile = classify_goal(normalized_goal)
    clarification = {
        "clarify_version": CLARIFY_VERSION,
        "stage": "clarify",
        "status": "ok",
        "created_at": now.isoformat(),
        "session_id": session,
        "mode": mode,
        "doctor_access": False,
        "resolver_called": False,
        "index_access": False,
        "original_goal": goal,
        "normalized_goal": normalized_goal,
        "intent": profile["intent"],
        "source_scope_hint": profile["source_scope_hint"],
        "expected_output": profile["expected_output"],
        "evidence_need": profile["evidence_need"],
        "review_questions": review_questions(normalized_goal, profile),
        "refined_prompt": refined_prompt(normalized_goal, profile),
        "clarify_json_path": str(clarify_json_path),
        "refined_prompt_md_path": str(refined_prompt_path),
    }

    write_text(clarify_json_path, json.dumps(clarification, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(refined_prompt_path, render_refined_prompt(clarification))
    return clarification


def normalize_goal(goal: str) -> str:
    return re.sub(r"\s+", " ", goal).strip()


def classify_goal(goal: str) -> dict[str, str]:
    lower = goal.lower()
    if (
        ("归一化" in goal and "冷热索引" in goal)
        or ("model_input" in lower and "codex-preflight" in lower)
        or ("artifacts" in lower and ("doctor" in lower or "codex" in lower))
    ):
        return {
            "intent": "runtime_pipeline",
            "source_scope_hint": "all",
            "expected_output": "four-stage runtime packet with review checkpoints and concrete next command",
            "evidence_need": "Doctor runtime docs, CLI contracts, context pack outputs, feedback records, and execution artifacts",
        }
    if any(marker in goal for marker in ("比较", "对比", "区别", "差异", "比起来")) or " compare " in f" {lower} ":
        return {
            "intent": "comparison",
            "source_scope_hint": "all",
            "expected_output": "comparison table plus evidence-backed conclusion",
            "evidence_need": "two clearly separated evidence slots and cited local paths",
        }
    if any(marker in lower for marker in ("code", "codex", "repo", "github")) or any(marker in goal for marker in ("代码", "仓库", "项目", "实现")):
        return {
            "intent": "project_research",
            "source_scope_hint": "gitProjects",
            "expected_output": "project-grounded analysis with file paths and next actions",
            "evidence_need": "local project README/docs/source chunks and workflow notes",
        }
    if any(marker in goal for marker in ("运行", "执行", "脚本", "调用", "产出")) or any(marker in lower for marker in ("run", "execute", "script")):
        return {
            "intent": "local_execution",
            "source_scope_hint": "all",
            "expected_output": "execution plan, allowed commands, artifacts, and review report",
            "evidence_need": "relevant local scripts, configs, and prior run reports",
        }
    if any(marker in goal for marker in ("图片", "视频", "音频", "简历", "截图")) or any(marker in lower for marker in ("image", "video", "audio", "resume")):
        return {
            "intent": "multimodal_review",
            "source_scope_hint": "all",
            "expected_output": "structured review separating attachment evidence from local context",
            "evidence_need": "attachment-derived OCR/KV plus relevant local sources",
        }
    return {
        "intent": "research",
        "source_scope_hint": "all",
        "expected_output": "evidence-backed answer with limitations and next steps",
        "evidence_need": "task-relevant local documents, sessions, workflows, or project files",
    }


def review_questions(goal: str, profile: dict[str, str]) -> list[str]:
    questions = [
        "这个 refined prompt 是否准确表达了你的真实任务？",
        f"下一阶段是否应该按 `{profile['source_scope_hint']}` 范围检索 Doctor？",
        f"你是否接受输出形态：{profile['expected_output']}？",
    ]
    if profile["intent"] == "comparison":
        questions.append("比较对象是否需要拆成明确的左右证据槽？")
    if profile["intent"] == "local_execution":
        questions.append("是否允许下一阶段建议本机执行命令，但仍先等待人工确认？")
    if len(goal) < 12:
        questions.append("原始任务很短，是否需要补充目标、对象或期望输出？")
    return questions


def refined_prompt(goal: str, profile: dict[str, str]) -> str:
    return "\n".join(
        [
            f"任务目标：{goal}",
            "",
            "请在下一阶段使用 Doctor 检索本机上下文前，严格保留这个目标，不要把任务扩大成泛泛研究。",
            f"任务类型：{profile['intent']}",
            f"建议检索范围：{profile['source_scope_hint']}",
            f"需要的证据：{profile['evidence_need']}",
            f"期望输出：{profile['expected_output']}",
            "",
            "回答时必须区分：本机证据、模型推断、限制、下一步。",
        ]
    )


def render_refined_prompt(clarification: dict[str, Any]) -> str:
    lines = [
        "---",
        f"clarify_version: {clarification['clarify_version']}",
        f"stage: {clarification['stage']}",
        f"status: {clarification['status']}",
        f"session_id: {clarification['session_id']}",
        f"doctor_access: {str(clarification['doctor_access']).lower()}",
        f"resolver_called: {str(clarification['resolver_called']).lower()}",
        f"index_access: {str(clarification['index_access']).lower()}",
        f"intent: {clarification['intent']}",
        f"source_scope_hint: {clarification['source_scope_hint']}",
        "---",
        "",
        "# Doctor Clarify Review",
        "",
        "This stage only normalizes the user's task. It does not read Doctor indexes, call the resolver, or inspect local sources.",
        "",
        "## Original User Request",
        "",
        clarification["original_goal"],
        "",
        "## Refined Prompt",
        "",
        clarification["refined_prompt"],
        "",
        "## Review Questions",
        "",
    ]
    lines.extend(f"- {question}" for question in clarification["review_questions"])
    lines.extend(
        [
            "",
            "## Next Stage",
            "",
            "If this prompt is accepted, pass the refined prompt to `agent-context codex-preflight` to generate `model_input.md` for review.",
            "",
        ]
    )
    return "\n".join(lines)
