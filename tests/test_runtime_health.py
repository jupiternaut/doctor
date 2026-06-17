from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

import agent_context.acceptance as acceptance_module
from agent_context.cli import main
from agent_context.acceptance import run_v1_acceptance, run_v1_followup, run_v1_refresh, run_v1_stage_status
from agent_context.io import write_jsonl, write_text
from agent_context.launchd import run_semantic_launchd
from agent_context.mcp_live_smoke import run_mcp_live_smoke
from agent_context.mcp_server import (
    mcp_runtime_health,
    mcp_semantic_readiness,
    mcp_v1_acceptance,
    mcp_v1_followup,
    mcp_v1_refresh,
    mcp_v1_stage_status,
)
from agent_context.reproducibility import parse_status_line, run_reproducibility_snapshot
from agent_context.runtime_health import run_runtime_health, run_semantic_readiness, semantic_readiness, semantic_readiness_next_action


def test_runtime_health_writes_acceptance_matrix_and_reports(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_runtime_health_fixture(out, source_scope="all")

    result = run_runtime_health(out, codex_plus_root=tmp_path / "missing-codex-plus")

    assert result["runtime_health_version"] == "0.1"
    assert result["status"] == "warning"
    assert result["acceptance_ready"] is False
    assert Path(result["json_path"]).exists()
    assert Path(result["md_path"]).exists()
    assert Path(result["latest_json_path"]).exists()
    assert Path(result["latest_md_path"]).exists()
    assert len(result["acceptance_matrix"]) == 7
    assert any(item["id"] == "v1.5" and item["status"] == "warning" for item in result["acceptance_matrix"])
    assert any(item["id"] == "hot_context_pack" and item["status"] == "ok" for item in result["checks"])
    markdown = Path(result["md_path"]).read_text(encoding="utf-8")
    assert "Acceptance Matrix" in markdown
    assert "Warning checks are not fatal" in markdown


def test_runtime_health_warns_when_latest_pack_is_not_all_scope(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_runtime_health_fixture(out, source_scope="gitProjects")

    result = run_runtime_health(out, codex_plus_root=tmp_path / "missing-codex-plus")
    hot_pack = next(item for item in result["checks"] if item["id"] == "hot_context_pack")

    assert hot_pack["status"] == "warning"
    assert hot_pack["evidence"]["source_scope"] == "gitProjects"
    assert "source_scope=gitProjects" in hot_pack["summary"]


def test_mcp_runtime_health_returns_report_paths(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_runtime_health_fixture(out, source_scope="all")

    result = mcp_runtime_health(out_root=str(out), codex_plus_root=str(tmp_path / "missing-codex-plus"))

    assert result["mcp_version"] == "0.1"
    assert Path(result["latest_json_path"]).exists()
    assert result["summary"]["checks_total"] >= 9


def test_mcp_live_smoke_proves_stdio_client_can_call_runtime_tools(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_runtime_health_fixture(out, source_scope="all")

    smoke = run_mcp_live_smoke(out, codex_plus_root=tmp_path / "missing-codex-plus")
    result = run_runtime_health(out, codex_plus_root=tmp_path / "missing-codex-plus")
    mcp_check = next(item for item in result["checks"] if item["id"] == "mcp_surface")

    assert smoke["status"] == "ok"
    assert smoke["tools_total"] >= 6
    assert "v1_refresh" in smoke["tools"]
    assert smoke["read_source_status"] == "ok"
    assert smoke["v1_acceptance_status"]
    assert Path(smoke["v1_acceptance_latest_md_path"]).exists()
    assert smoke["v1_followup_next_evidence_gate_reason"]
    assert "v1_followup_next_evidence_gate_at" in smoke
    assert "v1_followup_acceptance_gate_at" in smoke
    assert smoke["v1_stage_next_evidence_gate_reason"]
    assert "v1_stage_next_evidence_gate_at" in smoke
    assert "v1_stage_acceptance_gate_at" in smoke
    assert Path(smoke["latest_json_path"]).exists()
    assert mcp_check["status"] == "ok"
    assert "mcp_v1_refresh" in mcp_check["evidence"]["required_functions"]
    assert mcp_check["evidence"]["live_smoke"]["status"] == "ok"
    assert mcp_check["evidence"]["live_smoke"]["v1_acceptance_status"]


def test_runtime_health_accepts_matching_reproducibility_snapshot(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_runtime_health_fixture(out, source_scope="all")
    write_text(out / ".gitignore", "reports/\n")
    init_git_repo(out)
    write_text(out / "dirty-runtime-change.txt", "local v1 work\n")

    before = run_runtime_health(out, codex_plus_root=tmp_path / "missing-codex-plus")
    before_check = next(item for item in before["checks"] if item["id"] == "reproducibility")

    snapshot = run_reproducibility_snapshot(out, roots=[out])
    after = run_runtime_health(out, codex_plus_root=tmp_path / "missing-codex-plus")
    after_check = next(item for item in after["checks"] if item["id"] == "reproducibility")

    assert before_check["status"] == "warning"
    assert snapshot["summary"]["dirty_roots"] == 1
    assert after_check["status"] == "ok"
    assert after_check["evidence"]["snapshot"]["status"] == "ok"
    assert after_check["evidence"]["snapshot"]["covered_roots"] == [str(out.resolve())]


def test_reproducibility_status_line_parser_keeps_first_path_character() -> None:
    assert parse_status_line("M README.md") == ("M", "README.md")
    assert parse_status_line(" M README.md") == (" M", "README.md")
    assert parse_status_line("?? src/new_file.py") == ("??", "src/new_file.py")


def test_semantic_readiness_marks_healthy_short_window_as_waiting_for_time(tmp_path: Path) -> None:
    trend_path = tmp_path / "semantic-launchd-trend-latest.json"
    write_text(
        trend_path,
        json.dumps(
            {
                "status": "short_window",
                "confidence": "short_window",
                "metrics": {
                    "snapshots": 50,
                    "days_observed": 1,
                    "runs_delta": 1,
                    "unhealthy_snapshots": 0,
                },
                "daily": [{"bucket": "2026-06-16"}],
                "limitations": ["Only 1 day(s) observed; need 2 for multi-day stability."],
            },
            ensure_ascii=False,
        )
        + "\n",
    )

    readiness = semantic_readiness(
        {"exists": True, "chunks": 602},
        {"health": "ok", "installed": True},
        {
            "summary": {
                "latest_health": "ok",
                "snapshots": 53,
                "unhealthy_snapshots": 0,
                "latest_runs": 6,
                "next_expected_run_after": "2026-06-16T10:40:37+08:00",
                "seconds_until_next_expected_run": 3600,
            }
        },
        {"health": "ok"},
        {
            "path": str(trend_path),
            "status": "short_window",
            "confidence": "short_window",
            "summary": {"days_observed": 1},
        },
        min_semantic_chunks=16,
    )

    assert readiness["status"] == "waiting_for_time"
    assert readiness["ready"] is False
    assert readiness["reason"] == "healthy_but_short_window"
    assert readiness["trend_days_remaining"] == 1
    assert readiness["earliest_multi_day_check_after"].startswith("2026-06-17T00:00:00")
    assert "Need 1 more observed day" in semantic_readiness_next_action(readiness)


def test_semantic_readiness_cli_report_and_mcp(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out"
    launch_agents = tmp_path / "LaunchAgents"
    write_runtime_health_fixture(out, source_scope="all")
    run_semantic_launchd(out, action="install", launch_agents_dir=launch_agents)
    write_semantic_readiness_reports(out)

    result = run_semantic_readiness(out, launch_agents_dir=launch_agents)

    assert result["semantic_readiness_version"] == "0.1"
    assert result["status"] == "waiting_for_time"
    assert result["ready"] is False
    assert Path(result["latest_json_path"]).exists()
    assert Path(result["latest_md_path"]).exists()

    assert main(["semantic-readiness", "--out", str(out), "--launch-agents-dir", str(launch_agents)]) == 0
    cli_result = json.loads(capsys.readouterr().out)
    assert cli_result["semantic_readiness_version"] == "0.1"
    assert cli_result["status"] == "waiting_for_time"

    mcp_result = mcp_semantic_readiness(out_root=str(out), launch_agents_dir=str(launch_agents))
    assert mcp_result["mcp_version"] == "0.1"
    assert mcp_result["status"] == "waiting_for_time"


def test_v1_acceptance_report_marks_time_gated_semantic_readiness(tmp_path: Path, capsys, monkeypatch) -> None:
    out = tmp_path / "out"
    codex_plus = tmp_path / "codex-plus"
    launch_agents = tmp_path / "LaunchAgents"
    write_runtime_health_fixture(out, source_scope="all")
    write_passing_feedback_fixture(out)
    write_fake_codex_plus_repo(codex_plus)
    write_text(out / ".gitignore", "reports/\n")
    init_git_repo(out)
    write_text(out / "dirty-runtime-change.txt", "local v1 work\n")
    run_semantic_launchd(out, action="install", launch_agents_dir=launch_agents)
    write_semantic_readiness_reports(out)
    run_semantic_readiness(out, launch_agents_dir=launch_agents)
    write_codex_plus_smoke_fixture(out)
    write_mcp_live_smoke_fixture(out)
    run_reproducibility_snapshot(out, roots=[out, codex_plus])
    run_runtime_health(out, codex_plus_root=codex_plus)

    result = run_v1_acceptance(
        out,
        codex_plus_root=codex_plus,
        now=datetime.fromisoformat("2026-06-16T09:00:00+08:00"),
    )

    assert result["v1_acceptance_version"] == "0.1"
    assert result["status"] == "waiting_for_time"
    assert result["ready"] is False
    assert "time-gated" in result["decision"]
    assert Path(result["latest_json_path"]).exists()
    assert Path(result["latest_md_path"]).exists()
    assert Path(result["latest_followup_json_path"]).exists()
    assert Path(result["latest_followup_md_path"]).exists()
    assert result["followup_plan"]["can_recheck_now"] is False
    assert result["followup_plan"]["latest_md_path"].endswith("v1-followup-latest.md")
    assert result["followup_plan"]["next_evidence_gate_reason"]
    assert result["followup_plan"]["next_evidence_gate_at"]
    assert result["followup_plan"]["acceptance_wait_reason"] == "multi_day_not_due"
    assert result["followup_plan"]["acceptance_gate_at"].startswith("2026-06-17T00:00:00")
    assert result["next_commands"][0].startswith("agent-context v1-refresh ")
    assert any(item["id"] == "semantic_readiness" and item["status"] == "waiting_for_time" for item in result["evidence"])
    assert main(["v1-acceptance", "--out", str(out), "--codex-plus-root", str(codex_plus)]) == 0
    cli_result = json.loads(capsys.readouterr().out)
    assert cli_result["status"] == "waiting_for_time"
    assert cli_result["latest_followup_md_path"].endswith("v1-followup-latest.md")

    mcp_result = mcp_v1_acceptance(out_root=str(out), codex_plus_root=str(codex_plus))
    assert mcp_result["mcp_version"] == "0.1"
    assert mcp_result["status"] == "waiting_for_time"
    assert mcp_result["latest_followup_md_path"].endswith("v1-followup-latest.md")

    followup = run_v1_followup(
        out,
        codex_plus_root=codex_plus,
        now=datetime.fromisoformat("2026-06-16T09:00:00+08:00"),
    )
    assert followup["v1_followup_check_version"] == "0.1"
    assert followup["status"] == "waiting_for_time"
    assert followup["action"] == "wait"
    assert followup["can_recheck_now"] is False
    assert followup["wait_reason"] == "monitor_not_due"
    assert followup["next_gate_at"] == "2026-06-16T10:40:37+08:00"
    assert followup["seconds_until_next_gate"] > 0
    assert followup["acceptance_wait_reason"] == "multi_day_not_due"
    assert followup["acceptance_gate_at"].startswith("2026-06-17T00:00:00")
    assert followup["seconds_until_acceptance_gate"] > followup["seconds_until_next_gate"]
    assert followup["followup_plan_latest_md_path"].endswith("v1-followup-latest.md")
    assert Path(followup["latest_json_path"]).exists()
    assert Path(followup["latest_md_path"]).exists()

    later_followup = run_v1_followup(
        out,
        codex_plus_root=codex_plus,
        now=datetime.fromisoformat("2026-06-16T12:00:00+08:00"),
    )
    assert later_followup["wait_reason"] == "multi_day_not_due"
    assert later_followup["next_gate_at"].startswith("2026-06-17T00:00:00")
    assert later_followup["acceptance_wait_reason"] == "multi_day_not_due"
    assert later_followup["acceptance_gate_at"].startswith("2026-06-17T00:00:00")

    assert main(["v1-followup", "--out", str(out), "--codex-plus-root", str(codex_plus)]) == 0
    cli_followup = json.loads(capsys.readouterr().out)
    assert cli_followup["action"] == "wait"
    assert cli_followup["wait_reason"]

    mcp_followup = mcp_v1_followup(out_root=str(out), codex_plus_root=str(codex_plus))
    assert mcp_followup["mcp_version"] == "0.1"
    assert mcp_followup["action"] == "wait"
    assert mcp_followup["wait_reason"]

    run_v1_followup(
        out,
        codex_plus_root=codex_plus,
        now=datetime.fromisoformat("2026-06-16T09:00:00+08:00"),
    )
    stage_status = run_v1_stage_status(out, codex_plus_root=codex_plus)
    assert stage_status["v1_stage_status_version"] == "0.1"
    assert stage_status["status"] == "waiting_for_time"
    assert stage_status["ready"] is False
    assert Path(stage_status["latest_json_path"]).exists()
    assert Path(stage_status["latest_md_path"]).exists()
    assert stage_status["next_gates"]["wait_reason"]
    assert stage_status["next_gates"]["next_evidence_gate_reason"] == stage_status["next_gates"]["wait_reason"]
    assert stage_status["next_gates"]["next_evidence_gate_at"] == stage_status["next_gates"]["next_gate_at"]
    assert stage_status["next_gates"]["seconds_until_next_evidence_gate"] == stage_status["next_gates"]["seconds_until_next_gate"]
    assert stage_status["next_gates"]["acceptance_gate_at"].startswith("2026-06-17T00:00:00")
    assert "Seconds until next evidence gate" in Path(stage_status["latest_md_path"]).read_text(encoding="utf-8")
    stage_ids = {stage["id"] for stage in stage_status["stages"]}
    assert {
        "downloads_ingestion",
        "provider_layer",
        "cold_indexes",
        "semantic_background",
        "mcp_surface",
        "codex_plus_integration",
        "v1_acceptance_gate",
    }.issubset(stage_ids)

    assert main(["v1-stage-status", "--out", str(out), "--codex-plus-root", str(codex_plus)]) == 0
    cli_stage_status = json.loads(capsys.readouterr().out)
    assert cli_stage_status["status"] == "waiting_for_time"
    assert cli_stage_status["latest_md_path"].endswith("v1-stage-status-latest.md")

    mcp_stage_status = mcp_v1_stage_status(out_root=str(out), codex_plus_root=str(codex_plus))
    assert mcp_stage_status["mcp_version"] == "0.1"
    assert mcp_stage_status["status"] == "waiting_for_time"
    assert mcp_stage_status["latest_md_path"].endswith("v1-stage-status-latest.md")

    refresh = run_v1_refresh(
        out,
        codex_plus_root=codex_plus,
        now=datetime.fromisoformat("2026-06-16T09:00:00+08:00"),
    )
    assert refresh["v1_refresh_version"] == "0.1"
    assert refresh["status"] == "waiting_for_time"
    assert refresh["ready"] is False
    assert refresh["semantic_evidence"]["refreshed"] is False
    assert refresh["semantic_evidence"]["reason"] == "monitor_not_due"
    assert refresh["semantic_evidence"]["next_gate_at"] == "2026-06-16T10:40:37+08:00"
    assert refresh["mcp_live_smoke"]["refreshed"] is True
    assert refresh["mcp_live_smoke"]["status"] == "ok"
    assert refresh["mcp_live_smoke"]["latest_md_path"].endswith("mcp-live-smoke-latest.md")
    assert refresh["runtime_health"]["refreshed"] is True
    assert refresh["runtime_health"]["latest_md_path"].endswith("runtime-health-latest.md")
    assert refresh["followup_check"]["action"] == "wait"
    assert refresh["followup_check"]["wait_reason"] == "monitor_not_due"
    assert refresh["stage_status"]["next_gates"]["acceptance_gate_at"].startswith("2026-06-17T00:00:00")
    assert refresh["panel"]["status_json_path"].endswith("panel/status.json")
    assert Path(refresh["latest_json_path"]).exists()
    assert Path(refresh["latest_md_path"]).exists()
    panel_status = json.loads((out / "panel" / "status.json").read_text(encoding="utf-8"))
    assert panel_status["v1_stage_status"]["status"] == "waiting_for_time"

    assert main(["v1-refresh", "--out", str(out), "--codex-plus-root", str(codex_plus)]) == 0
    cli_refresh = json.loads(capsys.readouterr().out)
    assert cli_refresh["v1_refresh_version"] == "0.1"
    assert "semantic_evidence" in cli_refresh
    assert cli_refresh["mcp_live_smoke"]["refreshed"] is True
    assert cli_refresh["runtime_health"]["refreshed"] is True
    assert cli_refresh["panel"]["status_json_path"].endswith("panel/status.json")

    mcp_refresh = mcp_v1_refresh(out_root=str(out), codex_plus_root=str(codex_plus))
    assert mcp_refresh["mcp_version"] == "0.1"
    assert mcp_refresh["v1_refresh_version"] == "0.1"
    assert mcp_refresh["mcp_live_smoke"]["refreshed"] is True
    assert mcp_refresh["runtime_health"]["refreshed"] is True
    assert mcp_refresh["panel"]["status_json_path"].endswith("panel/status.json")

    assert main(
        [
            "v1-refresh",
            "--out",
            str(out),
            "--codex-plus-root",
            str(codex_plus),
            "--no-refresh-semantic-evidence",
            "--no-refresh-mcp-smoke",
            "--no-refresh-runtime-health",
        ]
    ) == 0
    no_health_refresh = json.loads(capsys.readouterr().out)
    assert no_health_refresh["semantic_evidence"]["reason"] == "disabled"
    assert no_health_refresh["mcp_live_smoke"]["refreshed"] is False
    assert no_health_refresh["runtime_health"]["refreshed"] is False

    wait_calls: dict[str, int] = {}

    def fake_wait(out_root: Path, *, timeout_seconds: int, poll_seconds: int, **_kwargs):
        wait_calls["timeout_seconds"] = timeout_seconds
        wait_calls["poll_seconds"] = poll_seconds
        return {
            "status": "ok",
            "latest_json_path": str(out_root / "reports" / "semantic-launchd-wait-latest.json"),
            "latest_md_path": str(out_root / "reports" / "semantic-launchd-wait-latest.md"),
        }

    def fake_refresh_report(name: str):
        return {
            "status": "ok",
            "latest_json_path": str(out / "reports" / f"{name}-latest.json"),
            "latest_md_path": str(out / "reports" / f"{name}-latest.md"),
        }

    monkeypatch.setattr(acceptance_module, "wait_for_semantic_launchd_run", fake_wait)
    monkeypatch.setattr(acceptance_module, "run_semantic_launchd_monitor", lambda *_args, **_kwargs: fake_refresh_report("semantic-monitor"))
    monkeypatch.setattr(acceptance_module, "run_semantic_launchd_audit", lambda *_args, **_kwargs: fake_refresh_report("semantic-audit"))
    monkeypatch.setattr(acceptance_module, "run_semantic_launchd_trend", lambda *_args, **_kwargs: fake_refresh_report("semantic-trend"))
    monkeypatch.setattr(
        acceptance_module,
        "run_semantic_readiness",
        lambda *_args, **_kwargs: {
            **fake_refresh_report("semantic-readiness"),
            "ready": False,
            "readiness": {
                "next_monitor_due_at": "2026-06-16T11:40:37+08:00",
            },
        },
    )
    expected_consumed_gate = acceptance_module.latest_semantic_evidence_gate(out)
    waited_refresh = run_v1_refresh(
        out,
        codex_plus_root=codex_plus,
        now=datetime.fromisoformat("2026-06-16T09:00:00+08:00"),
        wait_for_semantic_evidence=True,
        semantic_wait_timeout_seconds=10,
        semantic_wait_poll_seconds=2,
    )
    assert wait_calls == {"timeout_seconds": 10, "poll_seconds": 2}
    assert waited_refresh["wait_for_semantic_evidence"] is True
    assert waited_refresh["semantic_evidence"]["refreshed"] is True
    assert waited_refresh["semantic_evidence"]["consumed_gate_at"] == expected_consumed_gate
    assert waited_refresh["semantic_evidence"]["next_gate_at"] == "2026-06-16T11:40:37+08:00"
    assert waited_refresh["semantic_evidence"]["wait"]["status"] == "ok"
    assert waited_refresh["followup_check"]["wait_reason"] != "monitor_not_due"


def test_v1_acceptance_refresh_evidence_runs_full_recheck(tmp_path: Path) -> None:
    out = tmp_path / "out"
    codex_plus = tmp_path / "codex-plus"
    write_runtime_health_fixture(out, source_scope="all")
    write_passing_feedback_fixture(out)
    write_fake_codex_plus_repo(codex_plus)
    write_semantic_monitor_history(out)
    write_text(out / ".gitignore", "reports/\n")
    init_git_repo(out)
    write_text(out / "dirty-runtime-change.txt", "local v1 work\n")

    result = run_v1_acceptance(
        out,
        codex_plus_root=codex_plus,
        refresh_evidence=True,
        mcp_timeout_seconds=30,
        with_manager_feedback_smoke=True,
    )

    assert result["refresh_evidence"] is True
    assert result["refresh_health"] is True
    assert result["with_manager_feedback_smoke"] is True
    assert result["next_commands"][0].startswith("agent-context v1-refresh ")
    assert result["next_commands"][0].endswith("--with-manager-feedback-smoke")
    assert any(command.endswith("--refresh-evidence --with-manager-feedback-smoke") for command in result["next_commands"])
    assert any(command.endswith("--with-manager-feedback") for command in result["next_commands"])
    assert any(command.endswith("--with-manager-feedback-smoke") and "mcp-live-smoke" in command for command in result["next_commands"])
    refreshed = result["refreshed_reports"]
    assert set(refreshed) == {
        "semantic_launchd_monitor",
        "semantic_launchd_audit",
        "semantic_launchd_trend",
        "semantic_readiness",
        "reproducibility_snapshot",
        "codex_plus_smoke",
        "mcp_live_smoke",
        "runtime_health",
    }
    assert refreshed["codex_plus_smoke"]["status"] == "ok"
    assert refreshed["mcp_live_smoke"]["status"] == "ok"
    assert refreshed["mcp_live_smoke"]["with_manager_feedback_smoke"] is True
    assert Path(out / "reports" / "semantic-launchd-monitor-latest.json").exists()
    assert Path(out / "reports" / "semantic-launchd-audit-latest.json").exists()
    assert Path(out / "reports" / "semantic-launchd-trend-latest.json").exists()
    assert Path(out / "reports" / "semantic-readiness-latest.json").exists()
    assert Path(out / "reports" / "v1-followup-latest.json").exists()
    assert Path(out / "reports" / "v1-followup-latest.md").exists()
    assert Path(out / "reports" / "mcp-live-smoke-latest.json").exists()
    assert Path(out / "reports" / "runtime-health-latest.json").exists()
    assert Path(out / "reports" / "reproducibility-snapshot-latest.json").exists()
    codex_smoke = json.loads((out / "reports" / "codex-plus-smoke-latest.json").read_text(encoding="utf-8"))
    assert [item["name"] for item in codex_smoke["scripts"]] == ["panel_status", "manager_feedback_replay"]


def write_runtime_health_fixture(out: Path, *, source_scope: str) -> None:
    write_jsonl(out / "manifests" / "documents.jsonl", [{"path": "/tmp/a.md"}])
    write_jsonl(out / "manifests" / "chunks.jsonl", [{"path": "/tmp/a.md", "text": "alpha"}])
    write_jsonl(out / "manifests" / "failures.jsonl", [])
    write_text(out / "extracted" / "hash.md", "# Extracted\n")
    write_jsonl(out / "manifests" / "projects.jsonl", [{"provider": "git_project", "path": "/tmp/project"}])
    write_jsonl(out / "manifests" / "sessions.jsonl", [{"provider": "codex_session", "path": "/tmp/session.jsonl"}])
    write_jsonl(out / "manifests" / "workflows.jsonl", [{"provider": "workflow_doc", "path": "/tmp/workflow.md"}])
    for name in ("context.sqlite", "projects.sqlite", "sessions.sqlite"):
        write_simple_index(out / "indexes" / name)
    write_semantic_index(out / "indexes" / "semantic.sqlite")
    pack = out / "packs" / "context-pack-resolve-test"
    write_text(pack / "context.md", "# Context\n\n## Sources\n")
    write_jsonl(pack / "sources.jsonl", [{"path": "/tmp/project/README.md", "snippet": "source"}])
    write_text(
        pack / "manifest.json",
        json.dumps({"source_scope": source_scope, "sources_included": 1}, ensure_ascii=False) + "\n",
    )
    write_text(pack / "resolution_plan.json", "{}\n")
    write_text(pack / "codex_preflight.md", "# Preflight\n")
    write_text(
        out / "feedback" / "model.json",
        json.dumps({"feedback_model_version": "0.7", "replay_supervision_cases": 1}, ensure_ascii=False) + "\n",
    )
    write_text(
        out / "feedback" / "route_selector_model.json",
        json.dumps({"route_selector_model_version": "0.1", "cases_seen": 1}, ensure_ascii=False) + "\n",
    )
    write_jsonl(out / "feedback" / "retrieval_eval_cases.curated.jsonl", [])
    write_feedback_replay_report(out)
    write_text(
        out / "config" / "access_policy.json",
        json.dumps({"deny_path_patterns": ["**/.ssh/**"], "allow_providers": []}, ensure_ascii=False) + "\n",
    )


def write_passing_feedback_fixture(out: Path) -> None:
    write_text(
        out / "feedback" / "model.json",
        json.dumps({"feedback_model_version": "0.7", "replay_supervision_cases": 3}, ensure_ascii=False) + "\n",
    )
    write_text(
        out / "feedback" / "route_selector_model.json",
        json.dumps({"route_selector_model_version": "0.1", "cases_seen": 3}, ensure_ascii=False) + "\n",
    )
    write_jsonl(
        out / "feedback" / "retrieval_eval_cases.curated.jsonl",
        [{"query": "test", "expected_sources": ["/tmp/project/README.md"], "source": "projects"}],
    )
    for index in range(2):
        write_text(
            out / "reports" / f"feedback_replay_2026061600000{index}000000.json",
            json.dumps(
                {
                    "created_at": f"2026-06-16T00:00:0{index}+08:00",
                    "summary": {
                        "cases": 1,
                        "improved_expected_top1": 0,
                        "regressed_expected_top1": 0,
                    },
                    "cases": [
                        {
                            "goal": "test",
                            "expected_source": "/tmp/project/README.md",
                            "delta": {"expected_rank_before": 1, "expected_rank_after": 1},
                        }
                    ],
                },
                ensure_ascii=False,
            )
            + "\n",
        )


def write_fake_codex_plus_repo(root: Path) -> None:
    write_text(
        root / "scripts" / "smoke-agent-context-panel-status.mjs",
        'console.log(JSON.stringify({status:"ok", codexPlusConsumesStatus:true, v1AcceptanceStatus:"waiting_for_time"}));\n',
    )
    write_text(
        root / "scripts" / "smoke-agent-context-manager-feedback-replay.mjs",
        'console.log(JSON.stringify({status:"ok", replayHealth:"ok"}));\n',
    )
    write_text(
        root / "scripts" / "smoke-agent-context-runtime.mjs",
        'console.log(JSON.stringify({status:"ok", hasHint:true}));\n',
    )
    for relative in [
        "assets/inject/renderer-inject.js",
        "crates/codex-plus-core/src/agent_context.rs",
        "apps/codex-plus-manager/src/App.tsx",
    ]:
        write_text(root / relative, "fixture\n")
    init_git_repo(root)


def write_mcp_live_smoke_fixture(out: Path) -> None:
    write_text(
        out / "reports" / "mcp-live-smoke-latest.json",
        json.dumps(
            {
                "status": "ok",
                "tools_total": 35,
                "read_source_status": "ok",
                "semantic_readiness_status": "waiting_for_time",
                "latest_md_path": str(out / "reports" / "mcp-live-smoke-latest.md"),
            },
            ensure_ascii=False,
        )
        + "\n",
    )
    write_text(out / "reports" / "mcp-live-smoke-latest.md", "# MCP Live Smoke\n")


def write_codex_plus_smoke_fixture(out: Path) -> None:
    write_text(
        out / "reports" / "codex-plus-smoke-latest.json",
        json.dumps(
            {
                "status": "ok",
                "latest_md_path": str(out / "reports" / "codex-plus-smoke-latest.md"),
                "scripts": [
                    {
                        "name": "panel_status",
                        "status": "ok",
                        "returncode": 0,
                        "summary": {"status": "ok", "codexPlusConsumesStatus": True},
                    }
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
    )
    write_text(out / "reports" / "codex-plus-smoke-latest.md", "# Codex++ Smoke\n")


def write_semantic_monitor_history(out: Path) -> None:
    write_jsonl(
        out / "reports" / "semantic-launchd-monitor.jsonl",
        [
            {
                "created_at": "2026-06-16T00:00:00+08:00",
                "health": "ok",
                "launchctl": {"loaded": True, "runs": 1, "run_interval_seconds": 3600},
                "reports": {
                    "semantic_maintain": {"summary": {"status": "ok", "started_at": "2026-06-16T00:00:00+08:00"}},
                    "semantic_ann_prune": {"summary": {"status": "ok", "started_at": "2026-06-16T00:00:01+08:00"}},
                },
            },
            {
                "created_at": "2026-06-16T01:00:00+08:00",
                "health": "ok",
                "launchctl": {"loaded": True, "runs": 2, "run_interval_seconds": 3600},
                "reports": {
                    "semantic_maintain": {"summary": {"status": "ok", "started_at": "2026-06-16T01:00:00+08:00"}},
                    "semantic_ann_prune": {"summary": {"status": "ok", "started_at": "2026-06-16T01:00:01+08:00"}},
                },
            },
        ],
    )


def init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=path, check=True, capture_output=True, text=True)


def write_simple_index(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE documents (id TEXT)")
        conn.execute("CREATE TABLE chunks (id TEXT)")
        conn.execute("INSERT INTO documents VALUES ('doc')")
        conn.execute("INSERT INTO chunks VALUES ('chunk')")
        conn.commit()
    finally:
        conn.close()


def write_semantic_index(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            """
            CREATE TABLE semantic_chunks (
              source_kind TEXT NOT NULL,
              source_chunk_id TEXT NOT NULL,
              path TEXT,
              relative_path TEXT,
              text TEXT,
              embedding_json TEXT NOT NULL,
              embedding_backend TEXT NOT NULL,
              embedding_model TEXT,
              updated_at TEXT NOT NULL,
              metadata_json TEXT NOT NULL,
              PRIMARY KEY (source_kind, source_chunk_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE semantic_jobs (
              job_id TEXT PRIMARY KEY,
              source TEXT NOT NULL,
              backend TEXT NOT NULL,
              budget INTEGER NOT NULL,
              created_at TEXT NOT NULL,
              status TEXT NOT NULL,
              processed INTEGER NOT NULL,
              skipped INTEGER NOT NULL,
              error TEXT NOT NULL
            )
            """
        )
        for index in range(16):
            conn.execute(
                """
                INSERT INTO semantic_chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "projects",
                    f"chunk-{index}",
                    "/tmp/project/README.md",
                    "README.md",
                    "semantic text",
                    "[0.1, 0.2]",
                    "fastembed",
                    "test",
                    "2026-06-16T00:00:00+08:00",
                    "{}",
                ),
            )
        conn.execute(
            "INSERT INTO semantic_jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("job", "projects", "fastembed", 16, "2026-06-16T00:00:00+08:00", "ok", 16, 0, ""),
        )
        conn.commit()
    finally:
        conn.close()


def write_feedback_replay_report(out: Path) -> None:
    write_text(
        out / "reports" / "feedback_replay_20260616000000000000.json",
        json.dumps(
            {
                "created_at": "2026-06-16T00:00:00+08:00",
                "summary": {
                    "cases": 1,
                    "improved_expected_top1": 1,
                    "regressed_expected_top1": 0,
                },
                "cases": [
                    {
                        "goal": "test",
                        "expected_source": "/tmp/project/README.md",
                        "delta": {"expected_rank_before": 2, "expected_rank_after": 1},
                    }
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
    )


def write_semantic_readiness_reports(out: Path) -> None:
    reports = out / "reports"
    write_text(
        reports / "semantic-launchd-monitor-latest.json",
        json.dumps(
            {
                "summary": {
                    "latest_health": "ok",
                    "snapshots": 3,
                    "unhealthy_snapshots": 0,
                    "latest_runs": 2,
                    "next_expected_run_after": "2026-06-16T10:40:37+08:00",
                    "seconds_until_next_expected_run": 3600,
                }
            },
            ensure_ascii=False,
        )
        + "\n",
    )
    write_text(
        reports / "semantic-launchd-audit-latest.json",
        json.dumps({"health": "ok", "status": "ok", "summary": {"health": "ok"}}, ensure_ascii=False) + "\n",
    )
    write_text(
        reports / "semantic-launchd-trend-latest.json",
        json.dumps(
            {
                "status": "short_window",
                "confidence": "short_window",
                "metrics": {
                    "snapshots": 3,
                    "days_observed": 1,
                    "runs_delta": 1,
                    "unhealthy_snapshots": 0,
                },
                "daily": [{"bucket": "2026-06-16"}],
                "limitations": ["Only 1 day(s) observed; need 2 for multi-day stability."],
            },
            ensure_ascii=False,
        )
        + "\n",
    )
