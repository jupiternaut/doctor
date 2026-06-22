from __future__ import annotations

import json
from pathlib import Path

from agent_context.access_policy import record_access_audit
from agent_context.cold_index import build_cold_index
from agent_context.feedback_model import feedback_boost, load_feedback_model
from agent_context.io import write_jsonl
from agent_context.panel import build_context_panel, record_panel_feedback
from agent_context.semantic_index import run_semantic_refresh, semantic_index_status
from agent_context.mcp_server import (
    mcp_context_panel,
    mcp_record_panel_feedback,
    mcp_semantic_index_status,
    mcp_semantic_launchd_status,
)


def write_feedback_replay_report(out: Path, suffix: str, *, rank_after: int, improved: int = 0) -> None:
    path = out / "reports" / f"feedback_replay_{suffix}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "created_at": f"2026-06-16T08:{suffix[-2:]}:00+08:00",
                "feedback_model_version": "0.7",
                "summary": {
                    "cases": 1,
                    "changed_top1": 1 if improved else 0,
                    "improved_expected_top1": improved,
                    "regressed_expected_top1": 0,
                },
                "cases": [
                    {
                        "goal": "告诉我本地所有项目里如何构建个人推荐系统",
                        "source_scope": "gitProjects",
                        "expected_source": "data/preference_state.json",
                        "delta": {
                            "expected_rank_before": 3,
                            "expected_rank_after": rank_after,
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_context_panel_writes_status_html_and_feedback(tmp_path: Path) -> None:
    out = tmp_path / "out"
    pack = out / "packs" / "sample-resolve"
    pack.mkdir(parents=True)
    (pack / "context.md").write_text("# Context\n", encoding="utf-8")
    (pack / "sources.jsonl").write_text("", encoding="utf-8")
    (pack / "manifest.json").write_text("{}\n", encoding="utf-8")
    record_access_audit(
        out,
        action="mcp_read_provider",
        decision="denied",
        identifier="project:secret",
        reason="path_denied:secret",
        record={
            "provider": "git_project",
            "source_id": "project:secret",
            "path": "/tmp/secret",
        },
    )

    panel = build_context_panel(out, auto_context=False, mode="fast", source_scope="gitProjects")

    status_path = Path(panel["status_json_path"])
    html_path = Path(panel["html_path"])
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status_path.exists()
    assert html_path.exists()
    assert status["auto_context"] is False
    assert status["last_generated_pack"] == str(pack / "context.md")
    assert status["access_audit"]["events_total"] == 1
    assert status["access_audit"]["summary"]["decisions"]["denied"] == 1
    assert status["access_audit"]["summary"]["last_denied"]["identifier"] == "project:secret"
    assert status["semantic_launchd"]["health"] in {"not_installed", "ok", "degraded"}
    assert status["semantic_launchd"]["monitor"]["exists"] is False
    assert status["semantic_launchd"]["audit"]["exists"] is False
    assert status["semantic_launchd"]["recovery"]["exists"] is False
    assert status["semantic_readiness"]["exists"] is False
    assert status["semantic_readiness"]["status"] == "missing"
    assert status["runtime_vm"]["exists"] is False
    assert status["runtime_vm"]["status"] == "missing"
    assert status["feedback"]["replay_trend"]["exists"] is False
    assert status["feedback"]["replay_trend"]["health"] == "warning"
    panel_html = html_path.read_text(encoding="utf-8")
    assert "Agent Context Panel" in panel_html
    assert "Access Audit" in panel_html
    assert "Semantic LaunchAgent" in panel_html
    assert "Semantic Readiness" in panel_html
    assert "Runtime VM Status" in panel_html
    assert "Feedback Replay Health" in panel_html
    assert "project:secret" in panel_html

    feedback = record_panel_feedback(
        out,
        source="/repo/ranker.py",
        rating="useful",
        reason="good source",
        status_path=str(status_path),
    )

    assert Path(feedback["feedback_path"]).exists()
    model = load_feedback_model(out)
    assert feedback_boost(model, {"path": "/repo/ranker.py"}) > 0


def test_context_panel_reads_feedback_replay_trend_health(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_feedback_replay_report(out, "20260616085000000000", rank_after=3)
    write_feedback_replay_report(out, "20260616085100000000", rank_after=1, improved=1)

    panel = build_context_panel(out, auto_context=False)
    status = json.loads(Path(panel["status_json_path"]).read_text(encoding="utf-8"))
    trend = status["feedback"]["replay_trend"]
    panel_html = Path(panel["html_path"]).read_text(encoding="utf-8")

    assert trend["exists"] is True
    assert trend["health"] == "ok"
    assert trend["summary"]["latest_expected_top1_rate"] == 1.0
    assert trend["summary"]["trend_rank_improvements"] == 1
    assert "Feedback Replay Health" in panel_html
    assert "Feedback Replay Top1" in panel_html


def test_context_panel_reads_latest_launchd_monitor_summary(tmp_path: Path) -> None:
    out = tmp_path / "out"
    reports = out / "reports"
    reports.mkdir(parents=True)
    latest = reports / "semantic-launchd-monitor-latest.json"
    latest.write_text(
        json.dumps(
            {
                "history_path": str(reports / "semantic-launchd-monitor.jsonl"),
                "latest_md_path": str(reports / "semantic-launchd-monitor-latest.md"),
                "summary": {
                    "latest_health": "ok",
                    "latest_launchd_activity_at": "2026-06-16T05:39:52+08:00",
                    "next_expected_run_after": "2026-06-16T06:39:52+08:00",
                    "natural_run_due": False,
                    "natural_run_overdue": False,
                    "seconds_overdue": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    panel = build_context_panel(out, auto_context=False)
    status = json.loads(Path(panel["status_json_path"]).read_text(encoding="utf-8"))
    monitor = status["semantic_launchd"]["monitor"]

    assert monitor["exists"] is True
    assert monitor["path"] == str(latest)
    assert monitor["summary"]["next_expected_run_after"] == "2026-06-16T06:39:52+08:00"
    assert monitor["summary"]["natural_run_overdue"] is False


def test_context_panel_reads_latest_launchd_audit_summary(tmp_path: Path) -> None:
    out = tmp_path / "out"
    reports = out / "reports"
    reports.mkdir(parents=True)
    latest = reports / "semantic-launchd-audit-latest.json"
    latest.write_text(
        json.dumps(
            {
                "run_id": "semantic-launchd-audit-test",
                "status": "warning",
                "health": "warning",
                "started_at": "2026-06-16T06:00:00+08:00",
                "latest_md_path": str(reports / "semantic-launchd-audit-latest.md"),
                "alerts": [{"code": "insufficient_monitor_history"}],
                "recommendations": ["Collect more monitor snapshots."],
            }
        ),
        encoding="utf-8",
    )

    panel = build_context_panel(out, auto_context=False)
    status = json.loads(Path(panel["status_json_path"]).read_text(encoding="utf-8"))
    audit = status["semantic_launchd"]["audit"]

    assert audit["exists"] is True
    assert audit["path"] == str(latest)
    assert audit["health"] == "warning"
    assert audit["summary"]["alerts"] == 1


def test_context_panel_reads_latest_launchd_recovery_summary(tmp_path: Path) -> None:
    out = tmp_path / "out"
    reports = out / "reports"
    reports.mkdir(parents=True)
    latest = reports / "semantic-launchd-recover-latest.json"
    latest.write_text(
        json.dumps(
            {
                "run_id": "semantic-launchd-recover-test",
                "status": "planned",
                "dry_run": True,
                "started_at": "2026-06-16T06:00:00+08:00",
                "latest_md_path": str(reports / "semantic-launchd-recover-latest.md"),
                "action_count": 2,
                "failed_action_count": 0,
            }
        ),
        encoding="utf-8",
    )

    panel = build_context_panel(out, auto_context=False)
    status = json.loads(Path(panel["status_json_path"]).read_text(encoding="utf-8"))
    recovery = status["semantic_launchd"]["recovery"]

    assert recovery["exists"] is True
    assert recovery["path"] == str(latest)
    assert recovery["status"] == "planned"
    assert recovery["summary"]["action_count"] == 2


def test_context_panel_reads_latest_launchd_trend_summary(tmp_path: Path) -> None:
    out = tmp_path / "out"
    reports = out / "reports"
    reports.mkdir(parents=True)
    latest = reports / "semantic-launchd-trend-latest.json"
    latest.write_text(
        json.dumps(
            {
                "run_id": "semantic-launchd-trend-test",
                "status": "short_window",
                "confidence": "short_window",
                "started_at": "2026-06-16T06:00:00+08:00",
                "latest_md_path": str(reports / "semantic-launchd-trend-latest.md"),
                "metrics": {
                    "snapshots": 50,
                    "days_observed": 1,
                    "runs_delta": 1,
                    "unhealthy_snapshots": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    panel = build_context_panel(out, auto_context=False)
    status = json.loads(Path(panel["status_json_path"]).read_text(encoding="utf-8"))
    trend = status["semantic_launchd"]["trend"]

    assert trend["exists"] is True
    assert trend["path"] == str(latest)
    assert trend["status"] == "short_window"
    assert trend["summary"]["days_observed"] == 1


def test_context_panel_reads_latest_semantic_readiness(tmp_path: Path) -> None:
    out = tmp_path / "out"
    reports = out / "reports"
    reports.mkdir(parents=True)
    latest = reports / "semantic-readiness-latest.json"
    latest.write_text(
        json.dumps(
            {
                "status": "waiting_for_time",
                "ready": False,
                "latest_md_path": str(reports / "semantic-readiness-latest.md"),
                "next_action": "Need 1 more observed day.",
                "readiness": {
                    "reason": "healthy_but_short_window",
                    "semantic_chunks": 666,
                    "launchd_health": "ok",
                    "latest_monitor_health": "ok",
                    "trend_days_observed": 1,
                    "trend_days_remaining": 1,
                    "monitor_snapshots": 56,
                    "next_monitor_due_at": "2026-06-16T11:40:58+08:00",
                    "earliest_multi_day_check_after": "2026-06-17T00:00:00+08:00",
                },
            }
        ),
        encoding="utf-8",
    )

    panel = build_context_panel(out, auto_context=False)
    status = json.loads(Path(panel["status_json_path"]).read_text(encoding="utf-8"))
    readiness = status["semantic_readiness"]
    panel_html = Path(panel["html_path"]).read_text(encoding="utf-8")

    assert readiness["exists"] is True
    assert readiness["path"] == str(latest)
    assert readiness["status"] == "waiting_for_time"
    assert readiness["ready"] is False
    assert readiness["summary"]["semantic_chunks"] == 666
    assert readiness["summary"]["trend_days_remaining"] == 1
    assert "Semantic Readiness" in panel_html
    assert "Need 1 more observed day." in panel_html


def test_context_panel_reads_latest_v1_acceptance(tmp_path: Path) -> None:
    out = tmp_path / "out"
    reports = out / "reports"
    reports.mkdir(parents=True)
    latest = reports / "v1-acceptance-latest.json"
    latest.write_text(
        json.dumps(
            {
                "status": "waiting_for_time",
                "ready": False,
                "decision": "Implementation evidence is present, but final v1 acceptance is time-gated.",
                "created_at": "2026-06-16T11:12:11+08:00",
                "latest_md_path": str(reports / "v1-acceptance-latest.md"),
                "latest_followup_md_path": str(reports / "v1-followup-latest.md"),
                "latest_followup_json_path": str(reports / "v1-followup-latest.json"),
                "followup_plan": {
                    "can_recheck_now": False,
                    "earliest_recheck_after": "2026-06-17T00:00:00+08:00",
                    "latest_md_path": str(reports / "v1-followup-latest.md"),
                },
                "next_commands": ["agent-context v1-acceptance --out /tmp/out"],
            }
        ),
        encoding="utf-8",
    )
    (reports / "v1-followup-check-latest.json").write_text(
        json.dumps(
            {
                "status": "waiting_for_time",
                "action": "wait",
                "wait_reason": "monitor_not_due",
                "next_gate_at": "2026-06-16T12:41:23+08:00",
                "seconds_until_next_gate": 1000,
                "acceptance_wait_reason": "multi_day_not_due",
                "acceptance_gate_at": "2026-06-17T00:00:00+08:00",
                "seconds_until_acceptance_gate": 40920,
                "latest_md_path": str(reports / "v1-followup-check-latest.md"),
            }
        ),
        encoding="utf-8",
    )

    panel = build_context_panel(out, auto_context=False)
    status = json.loads(Path(panel["status_json_path"]).read_text(encoding="utf-8"))
    acceptance = status["v1_acceptance"]
    panel_html = Path(panel["html_path"]).read_text(encoding="utf-8")

    assert acceptance["exists"] is True
    assert acceptance["path"] == str(latest)
    assert acceptance["status"] == "waiting_for_time"
    assert acceptance["ready"] is False
    assert acceptance["decision"].startswith("Implementation evidence")
    assert acceptance["next_commands"] == ["agent-context v1-acceptance --out /tmp/out"]
    assert acceptance["latest_followup_md_path"].endswith("v1-followup-latest.md")
    assert acceptance["followup_plan"]["earliest_recheck_after"] == "2026-06-17T00:00:00+08:00"
    assert acceptance["followup_check"]["wait_reason"] == "monitor_not_due"
    assert acceptance["followup_check"]["next_gate_at"] == "2026-06-16T12:41:23+08:00"
    assert acceptance["followup_check"]["acceptance_wait_reason"] == "multi_day_not_due"
    assert acceptance["followup_check"]["acceptance_gate_at"] == "2026-06-17T00:00:00+08:00"
    assert "V1 Acceptance" in panel_html
    assert "monitor_not_due" in panel_html
    assert "multi_day_not_due" in panel_html
    assert "V1 Follow-Up Plan" in panel_html
    assert "time-gated" in panel_html


def test_context_panel_reads_latest_runtime_vm_acceptance(tmp_path: Path) -> None:
    out = tmp_path / "out"
    reports = out / "reports"
    reports.mkdir(parents=True)
    latest = reports / "runtime-vm-acceptance-latest.json"
    latest.write_text(
        json.dumps(
            {
                "status": "awaiting_context_review",
                "ready": False,
                "session_id": "doctor-panel-session",
                "latest_md_path": str(reports / "runtime-vm-acceptance-latest.md"),
                "session": {
                    "files": {
                        "runtime_adapter_manifest_json_path": str(
                            out / "runtime" / "sessions" / "doctor-panel-session" / "adapters" / "adapter_manifest.json"
                        ),
                        "execution_artifact_index_md_path": str(
                            out / "runtime" / "sessions" / "doctor-panel-session" / "execution_artifacts.md"
                        ),
                    },
                    "next": {
                        "review_file": str(out / "packs" / "task" / "model_input.md"),
                        "message": "Review model_input.md before any model or agent consumes it.",
                        "commands": [
                            "doctor context-review --out /tmp/out --session-id doctor-panel-session --action approve",
                            "doctor context-review --out /tmp/out --session-id doctor-panel-session --action reject",
                        ],
                    }
                },
                "checks": [
                    {
                        "id": "context_model_input",
                        "status": "ok",
                        "required_for_complete": True,
                    },
                    {
                        "id": "context_approved",
                        "status": "missing",
                        "required_for_complete": True,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    panel = build_context_panel(out, auto_context=False)
    status = json.loads(Path(panel["status_json_path"]).read_text(encoding="utf-8"))
    runtime_vm = status["runtime_vm"]
    panel_html = Path(panel["html_path"]).read_text(encoding="utf-8")

    assert runtime_vm["exists"] is True
    assert runtime_vm["status"] == "awaiting_context_review"
    assert runtime_vm["ready"] is False
    assert runtime_vm["session_id"] == "doctor-panel-session"
    assert runtime_vm["review_file"].endswith("model_input.md")
    assert runtime_vm["runtime_adapter_manifest_json_path"].endswith("adapter_manifest.json")
    assert runtime_vm["execution_artifact_index_md_path"].endswith("execution_artifacts.md")
    assert runtime_vm["missing_required"] == ["context_approved"]
    assert len(runtime_vm["next_commands"]) == 2
    assert "Runtime VM Status" in panel_html
    assert "awaiting_context_review" in panel_html
    assert "doctor-panel-session" in panel_html
    assert "Runtime VM Next Commands" in panel_html


def test_context_panel_reads_latest_v1_stage_status(tmp_path: Path) -> None:
    out = tmp_path / "out"
    reports = out / "reports"
    reports.mkdir(parents=True)
    latest = reports / "v1-stage-status-latest.json"
    latest.write_text(
        json.dumps(
            {
                "status": "waiting_for_time",
                "ready": False,
                "decision": "Implementation evidence is present, final gate is time-gated.",
                "created_at": "2026-06-16T13:05:25+08:00",
                "latest_md_path": str(reports / "v1-stage-status-latest.md"),
                "summary": {
                    "stages_total": 10,
                    "ok": 8,
                    "waiting_for_time": 2,
                    "warning": 0,
                    "failed": 0,
                    "status_counts": {"ok": 8, "waiting_for_time": 2},
                },
                "next_gates": {
                    "wait_reason": "monitor_not_due",
                    "next_gate_at": "2026-06-16T13:41:35+08:00",
                    "seconds_until_next_gate": 1200,
                    "next_evidence_gate_reason": "monitor_not_due",
                    "next_evidence_gate_at": "2026-06-16T13:41:35+08:00",
                    "seconds_until_next_evidence_gate": 1200,
                    "acceptance_wait_reason": "multi_day_not_due",
                    "acceptance_gate_at": "2026-06-17T00:00:00+08:00",
                    "seconds_until_acceptance_gate": 40920,
                    "trend_days_remaining": 1,
                },
                "stages": [
                    {
                        "id": "downloads_ingestion",
                        "title": "Downloads ingestion",
                        "status": "ok",
                        "progress": 100,
                        "summary": "997 documents",
                    },
                    {
                        "id": "semantic_background",
                        "title": "Background semantic index",
                        "status": "waiting_for_time",
                        "progress": 90,
                        "summary": "days=1/2",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    panel = build_context_panel(out, auto_context=False)
    status = json.loads(Path(panel["status_json_path"]).read_text(encoding="utf-8"))
    stage_status = status["v1_stage_status"]
    panel_html = Path(panel["html_path"]).read_text(encoding="utf-8")

    assert stage_status["exists"] is True
    assert stage_status["path"] == str(latest)
    assert stage_status["status"] == "waiting_for_time"
    assert stage_status["ready"] is False
    assert stage_status["summary"]["ok"] == 8
    assert stage_status["next_gates"]["acceptance_gate_at"] == "2026-06-17T00:00:00+08:00"
    assert stage_status["next_gates"]["next_evidence_gate_at"] == "2026-06-16T13:41:35+08:00"
    assert stage_status["next_gates"]["seconds_until_next_evidence_gate"] == 1200
    assert [stage["id"] for stage in stage_status["stages"]] == ["downloads_ingestion", "semantic_background"]
    assert "V1 Stage Status" in panel_html
    assert "monitor_not_due" in panel_html
    assert "waiting_for_time" in panel_html


def test_context_panel_refreshes_v1_stage_status_when_source_reports_exist(tmp_path: Path) -> None:
    out = tmp_path / "out"
    reports = out / "reports"
    reports.mkdir(parents=True)
    (reports / "runtime-health-latest.json").write_text(
        json.dumps(
            {
                "status": "warning",
                "summary": {"ok": 1, "warning": 1, "failed": 0, "warning_checks": ["semantic_background"]},
                "checks": [
                    {
                        "id": "downloads_ingestion",
                        "title": "Downloads ingestion",
                        "status": "ok",
                        "summary": "1 documents, 1 chunks, 0 failures",
                        "evidence": {"documents_jsonl": str(out / "manifests" / "documents.jsonl")},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (reports / "v1-acceptance-latest.json").write_text(
        json.dumps(
            {
                "status": "waiting_for_time",
                "ready": False,
                "decision": "Implementation evidence is present, but final gate is time-gated.",
                "latest_md_path": str(reports / "v1-acceptance-latest.md"),
                "followup_plan": {
                    "next_evidence_gate_reason": "monitor_not_due",
                    "next_evidence_gate_at": "2026-06-16T13:41:35+08:00",
                    "acceptance_wait_reason": "multi_day_not_due",
                    "acceptance_gate_at": "2026-06-17T00:00:00+08:00",
                    "trend_days_remaining": 1,
                },
            }
        ),
        encoding="utf-8",
    )
    (reports / "v1-stage-status-latest.json").write_text(
        json.dumps({"status": "stale", "ready": False, "stages": []}),
        encoding="utf-8",
    )

    panel = build_context_panel(out, auto_context=False)
    status = json.loads(Path(panel["status_json_path"]).read_text(encoding="utf-8"))
    stage_status = status["v1_stage_status"]

    assert stage_status["status"] == "waiting_for_time"
    assert stage_status["latest_md_path"].endswith("v1-stage-status-latest.md")
    assert stage_status["next_gates"]["acceptance_gate_at"] == "2026-06-17T00:00:00+08:00"
    assert stage_status["next_gates"]["next_evidence_gate_reason"] == "monitor_not_due"
    assert stage_status["next_gates"]["next_evidence_gate_at"] == "2026-06-16T13:41:35+08:00"
    assert any(stage["id"] == "downloads_ingestion" for stage in stage_status["stages"])


def test_mcp_context_panel_and_panel_feedback(tmp_path: Path) -> None:
    out = tmp_path / "out"

    panel = mcp_context_panel(out_root=str(out), auto_context=False)
    feedback = mcp_record_panel_feedback(
        source="/repo/source.md",
        rating="irrelevant",
        reason="wrong source",
        status_path=panel["status_json_path"],
        out_root=str(out),
    )

    assert panel["mcp_version"] == "0.1"
    assert Path(panel["status_json_path"]).exists()
    assert feedback["mcp_version"] == "0.1"
    assert Path(feedback["feedback_path"]).exists()
    assert feedback_boost(load_feedback_model(out), {"path": "/repo/source.md"}) < 0


class FakeEmbeddingBackend:
    backend_id = "fastembed"
    dimensions = 3
    model_name = "fake"
    storage_format = "json_dense_float32"

    def embed_document(self, text: str) -> str:
        return json.dumps([1.0, 0.0, 0.0])

    def embed_documents(self, texts: list[str]) -> list[str]:
        return [self.embed_document(text) for text in texts]

    def score_rows(self, rows: dict[str, dict], query: str, limit: int) -> dict[str, float]:
        return {}


def test_semantic_refresh_processes_budgeted_chunks(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    write_jsonl(
        out / "manifests" / "documents.jsonl",
        [
            {
                "doc_id": "doc-1",
                "path": "/tmp/a.md",
                "relative_path": "a.md",
                "scope": "/tmp",
                "sha256": "doc-1",
                "size_bytes": 20,
                "mtime": "2026-01-01T00:00:00+00:00",
                "extension": ".md",
                "mime": "text/markdown",
                "policy": "content",
                "parser": "direct_text",
                "parser_version": "test",
                "status": "ok",
                "extracted_md_path": "/tmp/a.md",
                "text_chars": 20,
                "chunk_count": 2,
            }
        ],
    )
    write_jsonl(
        out / "manifests" / "chunks.jsonl",
        [
            {"doc_id": "doc-1", "chunk_id": "doc-1:0001", "path": "/tmp/a.md", "chunk_index": 1, "text": "ranking"},
            {"doc_id": "doc-1", "chunk_id": "doc-1:0002", "path": "/tmp/a.md", "chunk_index": 2, "text": "feedback"},
        ],
    )
    write_jsonl(out / "manifests" / "failures.jsonl", [])
    build_cold_index(out)
    monkeypatch.setattr("agent_context.semantic_index.get_embedding_backend", lambda _config: FakeEmbeddingBackend())

    first = run_semantic_refresh(out, source="downloads", budget=1)
    second = run_semantic_refresh(out, source="downloads", budget=10)
    third = run_semantic_refresh(out, source="downloads", budget=10)
    status = semantic_index_status(out)

    assert first["processed"] == 1
    assert second["processed"] == 1
    assert third["status"] == "noop"
    assert status["chunks"] == 2
    assert status["jobs"] == 3


def test_mcp_semantic_index_status(tmp_path: Path) -> None:
    status = mcp_semantic_index_status(out_root=str(tmp_path / "out"))

    assert status["mcp_version"] == "0.1"
    assert status["exists"] is False


def test_mcp_semantic_launchd_status(tmp_path: Path) -> None:
    status = mcp_semantic_launchd_status(
        out_root=str(tmp_path / "out"),
        launch_agents_dir=str(tmp_path / "LaunchAgents"),
        tail_lines=3,
    )

    assert status["mcp_version"] == "0.1"
    assert status["health"] == "not_installed"
    assert status["installed"] is False
    assert status["load_note"].startswith("Status is read-only")
