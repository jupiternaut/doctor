from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .io import ensure_dir, write_text


MCP_LIVE_SMOKE_VERSION = "0.1"
REQUIRED_TOOLS = [
    "resolve_context",
    "read_source",
    "doctor_run",
    "doctor_session",
    "doctor_runtime_acceptance",
    "doctor_runtime_handoff",
    "doctor_context_review",
    "doctor_answer_review",
    "doctor_execution_review",
    "context_panel",
    "record_panel_feedback",
    "semantic_index_status",
    "semantic_readiness",
    "runtime_health",
    "v1_acceptance",
    "v1_followup",
    "v1_refresh",
    "v1_stage_status",
    "codex_plus_smoke",
    "reproducibility_snapshot",
]


def run_mcp_live_smoke(
    out_root: Path,
    *,
    codex_plus_root: Path | None = None,
    timeout_seconds: int = 60,
    with_manager_feedback_smoke: bool | None = None,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    started_at = datetime.now().astimezone()
    report_id = started_at.strftime("%Y%m%d%H%M%S%f")
    reports_dir = ensure_dir(out_root / "reports")
    json_path = reports_dir / f"mcp-live-smoke-{report_id}.json"
    md_path = reports_dir / f"mcp-live-smoke-{report_id}.md"
    latest_json_path = reports_dir / "mcp-live-smoke-latest.json"
    latest_md_path = reports_dir / "mcp-live-smoke-latest.md"
    try:
        result = asyncio.run(
            asyncio.wait_for(
                run_mcp_live_smoke_async(
                    out_root,
                    codex_plus_root=codex_plus_root,
                    with_manager_feedback_smoke=with_manager_feedback_smoke,
                ),
                timeout=max(5, timeout_seconds),
            )
        )
    except Exception as exc:
        result = {
            "status": "failed",
            "error": str(exc),
            "tools": [],
            "required_tools_missing": REQUIRED_TOOLS,
            "runtime_health_status": "",
            "read_source_status": "",
        }
    report = {
        "mcp_live_smoke_version": MCP_LIVE_SMOKE_VERSION,
        "created_at": started_at.isoformat(),
        "out_root": str(out_root),
        "codex_plus_root": str(codex_plus_root.expanduser().resolve()) if codex_plus_root else "",
        "with_manager_feedback_smoke": resolve_manager_feedback_smoke(out_root, with_manager_feedback_smoke),
        **result,
        "json_path": str(json_path),
        "md_path": str(md_path),
        "latest_json_path": str(latest_json_path),
        "latest_md_path": str(latest_md_path),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    write_text(json_path, text)
    write_text(latest_json_path, text)
    markdown = render_mcp_live_smoke_markdown(report)
    write_text(md_path, markdown)
    write_text(latest_md_path, markdown)
    return report


async def run_mcp_live_smoke_async(
    out_root: Path,
    *,
    codex_plus_root: Path | None = None,
    with_manager_feedback_smoke: bool | None = None,
) -> dict[str, Any]:
    manager_smoke = resolve_manager_feedback_smoke(out_root, with_manager_feedback_smoke)
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-c",
            "from agent_context.cli import main; raise SystemExit(main())",
            "mcp",
            "--out",
            str(out_root),
        ],
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            tool_names = sorted(tool.name for tool in tools_result.tools)
            missing = [tool for tool in REQUIRED_TOOLS if tool not in tool_names]
            if missing:
                return {
                    "status": "failed",
                    "tools": tool_names,
                    "required_tools_missing": missing,
                    "runtime_health_status": "",
                    "read_source_status": "",
                    "error": "required MCP tools are missing",
                }
            health = await session.call_tool(
                "runtime_health",
                {"codex_plus_root": str(codex_plus_root.expanduser().resolve()) if codex_plus_root else None},
            )
            health_payload = tool_payload(health)
            latest_md_path = str(health_payload.get("latest_md_path") or "")
            if not latest_md_path:
                return {
                    "status": "failed",
                    "tools": tool_names,
                    "required_tools_missing": [],
                    "runtime_health_status": str(health_payload.get("status") or ""),
                    "read_source_status": "",
                    "error": "runtime_health did not return latest_md_path",
                }
            read_result = await session.call_tool("read_source", {"identifier": latest_md_path, "max_chars": 1200})
            read_payload = tool_payload(read_result)
            text = str(read_payload.get("text") or "")
            read_ok = read_payload.get("read_mode") == "generated_artifact" and "Agent Context Runtime Health" in text
            semantic_result = await session.call_tool("semantic_index_status", {})
            semantic_payload = tool_payload(semantic_result)
            readiness_result = await session.call_tool("semantic_readiness", {})
            readiness_payload = tool_payload(readiness_result)
            acceptance_result = await session.call_tool(
                "v1_acceptance",
                {
                    "codex_plus_root": str(codex_plus_root.expanduser().resolve()) if codex_plus_root else None,
                    "refresh_health": False,
                    "refresh_evidence": False,
                    "with_manager_feedback_smoke": manager_smoke,
                },
            )
            acceptance_payload = tool_payload(acceptance_result)
            acceptance_followup_plan = (
                acceptance_payload.get("followup_plan")
                if isinstance(acceptance_payload.get("followup_plan"), dict)
                else {}
            )
            followup_result = await session.call_tool(
                "v1_followup",
                {
                    "codex_plus_root": str(codex_plus_root.expanduser().resolve()) if codex_plus_root else None,
                    "run_when_ready": False,
                    "force": False,
                    "with_manager_feedback_smoke": manager_smoke,
                },
            )
            followup_payload = tool_payload(followup_result)
            stage_status_result = await session.call_tool(
                "v1_stage_status",
                {"codex_plus_root": str(codex_plus_root.expanduser().resolve()) if codex_plus_root else None},
            )
            stage_status_payload = tool_payload(stage_status_result)
            stage_gates = (
                stage_status_payload.get("next_gates") if isinstance(stage_status_payload.get("next_gates"), dict) else {}
            )
            return {
                "status": "ok" if read_ok else "failed",
                "tools": tool_names,
                "tools_total": len(tool_names),
                "required_tools_missing": [],
                "runtime_health_status": str(health_payload.get("status") or ""),
                "runtime_health_latest_md_path": latest_md_path,
                "read_source_status": "ok" if read_ok else "failed",
                "read_source_mode": str(read_payload.get("read_mode") or ""),
                "semantic_index_exists": bool(semantic_payload.get("exists")),
                "semantic_index_chunks": int(semantic_payload.get("chunks") or 0),
                "semantic_readiness_status": str(readiness_payload.get("status") or ""),
                "semantic_readiness_ready": bool(readiness_payload.get("ready", False)),
                "with_manager_feedback_smoke": manager_smoke,
                "v1_acceptance_status": str(acceptance_payload.get("status") or ""),
                "v1_acceptance_ready": bool(acceptance_payload.get("ready", False)),
                "v1_acceptance_latest_md_path": str(acceptance_payload.get("latest_md_path") or ""),
                "v1_followup_status": str(followup_payload.get("status") or ""),
                "v1_followup_action": str(followup_payload.get("action") or ""),
                "v1_followup_can_recheck_now": bool(followup_payload.get("can_recheck_now", False)),
                "v1_followup_wait_reason": str(followup_payload.get("wait_reason") or ""),
                "v1_followup_next_gate_at": str(followup_payload.get("next_gate_at") or ""),
                "v1_followup_next_evidence_gate_reason": str(
                    followup_payload.get("next_evidence_gate_reason")
                    or acceptance_followup_plan.get("next_evidence_gate_reason")
                    or followup_payload.get("wait_reason")
                    or ""
                ),
                "v1_followup_next_evidence_gate_at": str(
                    followup_payload.get("next_evidence_gate_at")
                    or acceptance_followup_plan.get("next_evidence_gate_at")
                    or followup_payload.get("next_gate_at")
                    or ""
                ),
                "v1_followup_seconds_until_next_evidence_gate": int(
                    followup_payload.get("seconds_until_next_evidence_gate")
                    or acceptance_followup_plan.get("seconds_until_next_evidence_gate")
                    or followup_payload.get("seconds_until_next_gate")
                    or 0
                ),
                "v1_followup_acceptance_wait_reason": str(followup_payload.get("acceptance_wait_reason") or ""),
                "v1_followup_acceptance_gate_at": str(
                    followup_payload.get("acceptance_gate_at") or acceptance_followup_plan.get("acceptance_gate_at") or ""
                ),
                "v1_followup_seconds_until_acceptance_gate": int(
                    followup_payload.get("seconds_until_acceptance_gate")
                    or acceptance_followup_plan.get("seconds_until_acceptance_gate")
                    or 0
                ),
                "v1_followup_latest_md_path": str(followup_payload.get("latest_md_path") or ""),
                "v1_stage_status": str(stage_status_payload.get("status") or ""),
                "v1_stage_next_evidence_gate_reason": str(
                    stage_gates.get("next_evidence_gate_reason") or stage_gates.get("wait_reason") or ""
                ),
                "v1_stage_next_evidence_gate_at": str(
                    stage_gates.get("next_evidence_gate_at") or stage_gates.get("next_gate_at") or ""
                ),
                "v1_stage_seconds_until_next_evidence_gate": int(
                    stage_gates.get("seconds_until_next_evidence_gate") or stage_gates.get("seconds_until_next_gate") or 0
                ),
                "v1_stage_acceptance_wait_reason": str(stage_gates.get("acceptance_wait_reason") or ""),
                "v1_stage_acceptance_gate_at": str(stage_gates.get("acceptance_gate_at") or ""),
                "v1_stage_seconds_until_acceptance_gate": int(stage_gates.get("seconds_until_acceptance_gate") or 0),
                "v1_stage_status_latest_md_path": str(stage_status_payload.get("latest_md_path") or ""),
                "error": "" if read_ok else "read_source did not return the runtime health markdown",
            }


def resolve_manager_feedback_smoke(out_root: Path, requested: bool | None) -> bool:
    if requested is not None:
        return requested
    latest = out_root / "reports" / "v1-acceptance-latest.json"
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return data.get("with_manager_feedback_smoke") is True


def tool_payload(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    content = getattr(result, "content", None) or []
    if content:
        text = getattr(content[0], "text", "")
        if text:
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
            return value if isinstance(value, dict) else {"value": value}
    return {}


def latest_mcp_live_smoke_status(out_root: Path) -> dict[str, Any]:
    path = out_root.expanduser().resolve() / "reports" / "mcp-live-smoke-latest.json"
    if not path.exists():
        return {"exists": False, "path": str(path), "status": "missing"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"exists": True, "path": str(path), "status": "failed", "error": str(exc)}
    return {
        "exists": True,
        "path": str(path),
        "latest_md_path": payload.get("latest_md_path") or "",
        "status": payload.get("status") or "unknown",
        "created_at": payload.get("created_at") or "",
        "tools_total": payload.get("tools_total") or 0,
        "runtime_health_status": payload.get("runtime_health_status") or "",
        "read_source_status": payload.get("read_source_status") or "",
        "semantic_readiness_status": payload.get("semantic_readiness_status") or "",
        "semantic_readiness_ready": bool(payload.get("semantic_readiness_ready", False)),
        "v1_acceptance_status": payload.get("v1_acceptance_status") or "",
        "v1_acceptance_ready": bool(payload.get("v1_acceptance_ready", False)),
        "v1_acceptance_latest_md_path": payload.get("v1_acceptance_latest_md_path") or "",
        "v1_followup_status": payload.get("v1_followup_status") or "",
        "v1_followup_action": payload.get("v1_followup_action") or "",
        "v1_followup_can_recheck_now": bool(payload.get("v1_followup_can_recheck_now", False)),
        "v1_followup_wait_reason": payload.get("v1_followup_wait_reason") or "",
        "v1_followup_next_gate_at": payload.get("v1_followup_next_gate_at") or "",
        "v1_followup_next_evidence_gate_reason": payload.get("v1_followup_next_evidence_gate_reason") or "",
        "v1_followup_next_evidence_gate_at": payload.get("v1_followup_next_evidence_gate_at") or "",
        "v1_followup_seconds_until_next_evidence_gate": payload.get(
            "v1_followup_seconds_until_next_evidence_gate"
        )
        or 0,
        "v1_followup_acceptance_wait_reason": payload.get("v1_followup_acceptance_wait_reason") or "",
        "v1_followup_acceptance_gate_at": payload.get("v1_followup_acceptance_gate_at") or "",
        "v1_followup_seconds_until_acceptance_gate": payload.get("v1_followup_seconds_until_acceptance_gate") or 0,
        "v1_followup_latest_md_path": payload.get("v1_followup_latest_md_path") or "",
        "v1_stage_status": payload.get("v1_stage_status") or "",
        "v1_stage_next_evidence_gate_reason": payload.get("v1_stage_next_evidence_gate_reason") or "",
        "v1_stage_next_evidence_gate_at": payload.get("v1_stage_next_evidence_gate_at") or "",
        "v1_stage_seconds_until_next_evidence_gate": payload.get("v1_stage_seconds_until_next_evidence_gate") or 0,
        "v1_stage_acceptance_wait_reason": payload.get("v1_stage_acceptance_wait_reason") or "",
        "v1_stage_acceptance_gate_at": payload.get("v1_stage_acceptance_gate_at") or "",
        "v1_stage_seconds_until_acceptance_gate": payload.get("v1_stage_seconds_until_acceptance_gate") or 0,
        "v1_stage_status_latest_md_path": payload.get("v1_stage_status_latest_md_path") or "",
    }


def render_mcp_live_smoke_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# MCP Live Smoke",
            "",
            f"- status: `{report.get('status')}`",
            f"- created_at: `{report.get('created_at')}`",
            f"- out_root: `{report.get('out_root')}`",
            f"- tools_total: `{report.get('tools_total', 0)}`",
            f"- runtime_health_status: `{report.get('runtime_health_status', '')}`",
            f"- read_source_status: `{report.get('read_source_status', '')}`",
            f"- runtime_health_latest_md_path: `{report.get('runtime_health_latest_md_path', '')}`",
            f"- semantic_index_chunks: `{report.get('semantic_index_chunks', 0)}`",
            f"- semantic_readiness_status: `{report.get('semantic_readiness_status', '')}`",
            f"- semantic_readiness_ready: `{str(report.get('semantic_readiness_ready', False)).lower()}`",
            f"- v1_acceptance_status: `{report.get('v1_acceptance_status', '')}`",
            f"- v1_acceptance_ready: `{str(report.get('v1_acceptance_ready', False)).lower()}`",
            f"- v1_acceptance_latest_md_path: `{report.get('v1_acceptance_latest_md_path', '')}`",
            f"- v1_followup_status: `{report.get('v1_followup_status', '')}`",
            f"- v1_followup_action: `{report.get('v1_followup_action', '')}`",
            f"- v1_followup_can_recheck_now: `{str(report.get('v1_followup_can_recheck_now', False)).lower()}`",
            f"- v1_followup_wait_reason: `{report.get('v1_followup_wait_reason', '')}`",
            f"- v1_followup_next_gate_at: `{report.get('v1_followup_next_gate_at', '')}`",
            f"- v1_followup_next_evidence_gate_reason: `{report.get('v1_followup_next_evidence_gate_reason', '')}`",
            f"- v1_followup_next_evidence_gate_at: `{report.get('v1_followup_next_evidence_gate_at', '')}`",
            f"- v1_followup_seconds_until_next_evidence_gate: `{report.get('v1_followup_seconds_until_next_evidence_gate', 0)}`",
            f"- v1_followup_acceptance_wait_reason: `{report.get('v1_followup_acceptance_wait_reason', '')}`",
            f"- v1_followup_acceptance_gate_at: `{report.get('v1_followup_acceptance_gate_at', '')}`",
            f"- v1_followup_seconds_until_acceptance_gate: `{report.get('v1_followup_seconds_until_acceptance_gate', 0)}`",
            f"- v1_followup_latest_md_path: `{report.get('v1_followup_latest_md_path', '')}`",
            f"- v1_stage_status: `{report.get('v1_stage_status', '')}`",
            f"- v1_stage_next_evidence_gate_reason: `{report.get('v1_stage_next_evidence_gate_reason', '')}`",
            f"- v1_stage_next_evidence_gate_at: `{report.get('v1_stage_next_evidence_gate_at', '')}`",
            f"- v1_stage_seconds_until_next_evidence_gate: `{report.get('v1_stage_seconds_until_next_evidence_gate', 0)}`",
            f"- v1_stage_acceptance_wait_reason: `{report.get('v1_stage_acceptance_wait_reason', '')}`",
            f"- v1_stage_acceptance_gate_at: `{report.get('v1_stage_acceptance_gate_at', '')}`",
            f"- v1_stage_seconds_until_acceptance_gate: `{report.get('v1_stage_seconds_until_acceptance_gate', 0)}`",
            f"- v1_stage_status_latest_md_path: `{report.get('v1_stage_status_latest_md_path', '')}`",
            f"- error: `{report.get('error', '')}`",
            "",
        ]
    )
