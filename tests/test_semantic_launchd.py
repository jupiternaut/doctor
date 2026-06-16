from __future__ import annotations

import json
import plistlib
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from agent_context.cli import main
from agent_context.launchd import (
    run_semantic_launchd,
    run_semantic_launchd_audit,
    run_semantic_launchd_monitor,
    run_semantic_launchd_recover,
    run_semantic_launchd_trend,
    semantic_launchd_status,
)
from agent_context.mcp_server import mcp_semantic_launchd_audit, mcp_semantic_launchd_monitor, mcp_semantic_launchd_recover, mcp_semantic_launchd_trend


def test_semantic_launchd_print_renders_plist_and_script_without_writing(tmp_path: Path) -> None:
    out = tmp_path / "out"
    launch_agents = tmp_path / "LaunchAgents"

    result = run_semantic_launchd(
        out,
        action="print",
        interval_minutes=15,
        agent_context_bin="/usr/local/bin/agent-context",
        launch_agents_dir=launch_agents,
    )

    plist = plistlib.loads(result["plist_xml"].encode("utf-8"))
    assert plist["Label"] == "com.gengrf.agent-context.semantic-maintenance"
    assert plist["StartInterval"] == 900
    assert plist["ProgramArguments"] == [result["script_path"]]
    assert plist["EnvironmentVariables"]["PATH"] == "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    assert ".codex/tmp" not in plist["EnvironmentVariables"]["PATH"]
    assert "semantic-maintain" in result["script_text"]
    assert "semantic-ann-prune" in result["script_text"]
    assert not Path(result["plist_path"]).exists()
    assert not Path(result["script_path"]).exists()


def test_semantic_launchd_install_and_uninstall(tmp_path: Path) -> None:
    out = tmp_path / "out"
    launch_agents = tmp_path / "LaunchAgents"

    installed = run_semantic_launchd(
        out,
        action="install",
        label="com.example.agent-context.test",
        interval_minutes=5,
        source="projects",
        budget=7,
        max_jobs=3,
        min_interval_minutes=11,
        ann_max_entries=9,
        ann_max_bytes=12345,
        agent_context_bin="/tmp/agent-context",
        launch_agents_dir=launch_agents,
    )

    plist_path = Path(installed["plist_path"])
    script_path = Path(installed["script_path"])
    assert plist_path.exists()
    assert script_path.exists()
    assert Path(installed["stdout_path"]).parent.exists()
    assert script_path.stat().st_mode & 0o111
    script = script_path.read_text(encoding="utf-8")
    assert "--source projects" in script
    assert "--budget 7" in script
    assert "--max-jobs 3" in script
    assert "--min-interval-minutes 11" in script
    assert "--max-entries 9" in script
    assert "--max-bytes 12345" in script

    uninstalled = run_semantic_launchd(
        out,
        action="uninstall",
        label="com.example.agent-context.test",
        launch_agents_dir=launch_agents,
    )

    assert str(plist_path) in uninstalled["removed"]
    assert str(script_path) in uninstalled["removed"]
    assert not plist_path.exists()
    assert not script_path.exists()


def test_semantic_launchd_status_reports_not_installed(tmp_path: Path) -> None:
    out = tmp_path / "out"
    launch_agents = tmp_path / "LaunchAgents"

    status = semantic_launchd_status(out, launch_agents_dir=launch_agents)

    assert status["health"] == "not_installed"
    assert status["installed"] is False
    assert status["plist"]["exists"] is False
    assert status["script"]["exists"] is False
    assert status["launchctl"]["checked"] is False


def test_semantic_launchd_status_reports_installed_health_reports_and_logs(tmp_path: Path) -> None:
    out = tmp_path / "out"
    launch_agents = tmp_path / "LaunchAgents"
    run_semantic_launchd(
        out,
        action="install",
        label="com.example.agent-context.test",
        launch_agents_dir=launch_agents,
    )
    reports = out / "reports"
    reports.mkdir(parents=True)
    (reports / "semantic-maintain-20260616010101000000.json").write_text(
        json.dumps({"run_id": "semantic-maintain-test", "status": "ok", "processed": 7}),
        encoding="utf-8",
    )
    (reports / "semantic-ann-prune-20260616010102000000.json").write_text(
        json.dumps({"run_id": "semantic-ann-prune-test", "status": "ok", "files_removed": 2}),
        encoding="utf-8",
    )
    logs = out / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "com.example.agent-context.test.out.log").write_text("first\nsecond\n", encoding="utf-8")
    (logs / "com.example.agent-context.test.err.log").write_text("warn\nlast\n", encoding="utf-8")

    status = semantic_launchd_status(
        out,
        label="com.example.agent-context.test",
        launch_agents_dir=launch_agents,
        tail_lines=1,
    )

    assert status["health"] == "ok"
    assert status["installed"] is True
    assert status["log_dir_exists"] is True
    assert status["plist"]["label_matches"] is True
    assert status["script"]["has_semantic_maintain"] is True
    assert status["script"]["has_semantic_ann_prune"] is True
    assert status["reports"]["semantic_maintain"]["summary"]["run_id"] == "semantic-maintain-test"
    assert status["reports"]["semantic_ann_prune"]["summary"]["files_removed"] == 2
    assert status["logs"]["stdout"]["tail"] == ["second"]
    assert status["logs"]["stderr"]["tail"] == ["last"]


def test_semantic_launchd_status_can_include_launchctl_state(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    launch_agents = tmp_path / "LaunchAgents"
    run_semantic_launchd(
        out,
        action="install",
        label="com.example.agent-context.test",
        launch_agents_dir=launch_agents,
    )

    def fake_run(*args, **kwargs):
        assert args[0] == ["launchctl", "print", "gui/501/com.example.agent-context.test"]
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout="\n".join(
                [
                    "gui/501/com.example.agent-context.test = {",
                    "\tpath = /tmp/LaunchAgents/com.example.agent-context.test.plist",
                    "\tstate = not running",
                    "\tprogram = /tmp/out/scripts/com.example.agent-context.test.sh",
                    "\tstdout path = /tmp/out/logs/com.example.agent-context.test.out.log",
                    "\tstderr path = /tmp/out/logs/com.example.agent-context.test.err.log",
                    "\truns = 3",
                    "\tlast exit code = 0",
                    "\trun interval = 3600 seconds",
                    "}",
                ]
            ),
            stderr="",
        )

    monkeypatch.setattr("agent_context.launchd.os.getuid", lambda: 501)
    monkeypatch.setattr("agent_context.launchd.subprocess.run", fake_run)

    status = semantic_launchd_status(
        out,
        label="com.example.agent-context.test",
        launch_agents_dir=launch_agents,
        include_launchctl=True,
    )

    assert status["launchctl"]["checked"] is True
    assert status["launchctl"]["loaded"] is True
    assert status["launchctl"]["state"] == "not running"
    assert status["launchctl"]["runs"] == 3
    assert status["launchctl"]["last_exit_code"] == "0"
    assert status["launchctl"]["run_interval_seconds"] == 3600
    assert status["launchctl"]["program"] == "/tmp/out/scripts/com.example.agent-context.test.sh"
    assert "launchctl_not_loaded" not in status["issues"]
    assert status["health"] == "ok"


def test_semantic_launchd_monitor_writes_history_latest_reports_and_mcp(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    launch_agents = tmp_path / "LaunchAgents"
    run_semantic_launchd(
        out,
        action="install",
        label="com.example.agent-context.test",
        launch_agents_dir=launch_agents,
    )
    reports = out / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "semantic-maintain-20260616010101000000.json").write_text(
        json.dumps(
            {
                "run_id": "semantic-maintain-test",
                "status": "ok",
                "processed": 9,
                "jobs_run": 1,
                "started_at": "2026-06-16T01:01:01+00:00",
            }
        ),
        encoding="utf-8",
    )
    (reports / "semantic-ann-prune-20260616010102000000.json").write_text(
        json.dumps(
            {
                "run_id": "semantic-ann-prune-test",
                "status": "ok",
                "files_removed": 0,
                "started_at": "2026-06-16T01:01:02+00:00",
            }
        ),
        encoding="utf-8",
    )

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout="\n".join(
                [
                    "gui/501/com.example.agent-context.test = {",
                    "\tstate = not running",
                    "\tprogram = /tmp/out/scripts/com.example.agent-context.test.sh",
                    "\truns = 4",
                    "\tlast exit code = 0",
                    "\trun interval = 3600 seconds",
                    "}",
                ]
            ),
            stderr="",
        )

    monkeypatch.setattr("agent_context.launchd.os.getuid", lambda: 501)
    monkeypatch.setattr("agent_context.launchd.subprocess.run", fake_run)

    result = run_semantic_launchd_monitor(
        out,
        label="com.example.agent-context.test",
        launch_agents_dir=launch_agents,
        with_launchctl=True,
        max_history=10,
    )

    history_path = Path(result["history_path"])
    latest_json_path = Path(result["latest_json_path"])
    latest_md_path = Path(result["latest_md_path"])
    assert history_path.exists()
    assert latest_json_path.exists()
    assert latest_md_path.exists()
    assert result["snapshot"]["health"] == "ok"
    assert result["snapshot"]["launchctl"]["runs"] == 4
    assert result["snapshot"]["reports"]["semantic_maintain"]["summary"]["processed"] == 9
    assert result["summary"]["latest_last_exit_code"] == "0"
    assert isinstance(result["summary"]["latest_snapshot_age_seconds"], int)
    assert result["summary"]["latest_launchd_activity_at"] == "2026-06-16T01:01:02+00:00"
    assert result["summary"]["next_expected_run_after"] == "2026-06-16T02:01:02+00:00"
    assert isinstance(result["summary"]["natural_run_due"], bool)
    assert "Semantic LaunchAgent Monitor" in latest_md_path.read_text(encoding="utf-8")

    mcp_result = mcp_semantic_launchd_monitor(
        out_root=str(out),
        label="com.example.agent-context.test",
        launch_agents_dir=str(launch_agents),
        with_launchctl=True,
    )
    assert mcp_result["mcp_version"] == "0.1"
    assert mcp_result["snapshot"]["launchctl"]["runs"] == 4


def test_semantic_launchd_monitor_marks_overdue_after_grace(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    launch_agents = tmp_path / "LaunchAgents"
    run_semantic_launchd(
        out,
        action="install",
        label="com.example.agent-context.test",
        launch_agents_dir=launch_agents,
    )
    reports = out / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "semantic-maintain-20000101000000000000.json").write_text(
        json.dumps(
            {
                "run_id": "semantic-maintain-old",
                "status": "ok",
                "processed": 1,
                "started_at": "2000-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout="\n".join(
                [
                    "gui/501/com.example.agent-context.test = {",
                    "\tstate = not running",
                    "\truns = 1",
                    "\tlast exit code = 0",
                    "\trun interval = 3600 seconds",
                    "}",
                ]
            ),
            stderr="",
        )

    monkeypatch.setattr("agent_context.launchd.os.getuid", lambda: 501)
    monkeypatch.setattr("agent_context.launchd.subprocess.run", fake_run)

    result = run_semantic_launchd_monitor(
        out,
        label="com.example.agent-context.test",
        launch_agents_dir=launch_agents,
        with_launchctl=True,
    )

    assert result["summary"]["natural_run_due"] is True
    assert result["summary"]["natural_run_overdue"] is True
    assert result["summary"]["seconds_overdue"] > result["summary"]["overdue_grace_seconds"]


def test_semantic_launchd_wait_returns_when_runs_increase(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    calls = []
    snapshots = [
        {
            "summary": {
                "latest_created_at": "2026-06-16T06:00:00+00:00",
                "latest_health": "ok",
                "latest_runs": 2,
                "latest_state": "not running",
                "latest_last_exit_code": "0",
                "latest_launchd_activity_at": "2026-06-16T05:00:00+00:00",
                "seconds_until_next_expected_run": 1,
                "next_expected_run_after": "2026-06-16T06:00:00+00:00",
                "natural_run_due": False,
                "natural_run_overdue": False,
            }
        },
        {
            "summary": {
                "latest_created_at": "2026-06-16T06:01:00+00:00",
                "latest_health": "ok",
                "latest_runs": 3,
                "latest_state": "not running",
                "latest_last_exit_code": "0",
                "latest_launchd_activity_at": "2026-06-16T06:00:05+00:00",
                "seconds_until_next_expected_run": 3600,
                "next_expected_run_after": "2026-06-16T07:00:05+00:00",
                "natural_run_due": False,
                "natural_run_overdue": False,
            }
        },
    ]

    def fake_monitor(*args, **kwargs):
        calls.append((args, kwargs))
        return snapshots[min(len(calls) - 1, len(snapshots) - 1)]

    monkeypatch.setattr("agent_context.launchd.run_semantic_launchd_monitor", fake_monitor)
    monkeypatch.setattr("agent_context.launchd.time.monotonic", iter([0, 0, 0, 1]).__next__)
    monkeypatch.setattr("agent_context.launchd.time.sleep", lambda _seconds: None)

    from agent_context.launchd import wait_for_semantic_launchd_run

    result = wait_for_semantic_launchd_run(out, timeout_seconds=10, poll_seconds=5)

    assert result["status"] == "ok"
    assert result["stop_reason"] == "runs_increased"
    assert result["initial_runs"] == 2
    assert result["latest_summary"]["latest_runs"] == 3
    assert len(result["snapshots"]) == 2
    assert Path(result["report_json_path"]).exists()
    assert Path(result["report_md_path"]).exists()


def test_semantic_launchd_wait_times_out_without_progress(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"

    def fake_monitor(*args, **kwargs):
        return {
            "summary": {
                "latest_created_at": "2026-06-16T06:00:00+00:00",
                "latest_health": "ok",
                "latest_runs": 2,
                "latest_state": "not running",
                "latest_last_exit_code": "0",
                "latest_launchd_activity_at": "2026-06-16T05:00:00+00:00",
                "seconds_until_next_expected_run": 0,
                "next_expected_run_after": "2026-06-16T06:00:00+00:00",
                "natural_run_due": True,
                "natural_run_overdue": False,
            }
        }

    monkeypatch.setattr("agent_context.launchd.run_semantic_launchd_monitor", fake_monitor)
    monkeypatch.setattr("agent_context.launchd.time.monotonic", iter([0, 1]).__next__)
    monkeypatch.setattr("agent_context.launchd.time.sleep", lambda _seconds: None)

    from agent_context.launchd import wait_for_semantic_launchd_run

    result = wait_for_semantic_launchd_run(out, timeout_seconds=0, poll_seconds=1)

    assert result["status"] == "timeout"
    assert result["stop_reason"] == "timeout"
    assert result["initial_runs"] == 2
    assert len(result["snapshots"]) == 1


def test_semantic_launchd_audit_reports_ok_for_healthy_history(tmp_path: Path) -> None:
    out = tmp_path / "out"
    reports = out / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone()
    history = [
        {
            "created_at": (now - timedelta(minutes=30)).isoformat(),
            "health": "ok",
            "installed": True,
            "issues": [],
            "launchctl": {
                "checked": True,
                "loaded": True,
                "state": "not running",
                "runs": 2,
                "last_exit_code": "0",
                "run_interval_seconds": 3600,
            },
            "reports": {
                "semantic_maintain": {
                    "summary": {
                        "status": "ok",
                        "started_at": (now - timedelta(minutes=30)).isoformat(),
                    }
                },
                "semantic_ann_prune": {
                    "summary": {
                        "status": "ok",
                        "started_at": (now - timedelta(minutes=30)).isoformat(),
                    }
                },
            },
            "logs": {"stderr_size_bytes": 0},
        },
        {
            "created_at": now.isoformat(),
            "health": "ok",
            "installed": True,
            "issues": [],
            "launchctl": {
                "checked": True,
                "loaded": True,
                "state": "not running",
                "runs": 3,
                "last_exit_code": "0",
                "run_interval_seconds": 3600,
            },
            "reports": {
                "semantic_maintain": {
                    "summary": {
                        "status": "skipped",
                        "started_at": now.isoformat(),
                    }
                },
                "semantic_ann_prune": {
                    "summary": {
                        "status": "ok",
                        "started_at": now.isoformat(),
                    }
                },
            },
            "logs": {"stderr_size_bytes": 0},
        },
    ]
    history_path = reports / "semantic-launchd-monitor.jsonl"
    history_path.write_text("\n".join(json.dumps(item) for item in history) + "\n", encoding="utf-8")

    result = run_semantic_launchd_audit(out)

    assert result["semantic_launchd_audit_version"] == "0.1"
    assert result["health"] == "ok"
    assert result["alerts"] == []
    assert result["notification"]["requested"] is False
    assert result["notification"]["sent"] is False
    assert result["notification"]["skipped_reason"] == "not_requested"
    assert result["metrics"]["snapshots"] == 2
    assert Path(result["report_json_path"]).exists()
    assert Path(result["latest_json_path"]).exists()


def test_semantic_launchd_audit_alerts_on_failed_overdue_history(tmp_path: Path) -> None:
    out = tmp_path / "out"
    reports = out / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    history = [
        {
            "created_at": "2000-01-01T00:00:00+00:00",
            "health": "degraded",
            "installed": True,
            "issues": ["example"],
            "launchctl": {
                "checked": True,
                "loaded": True,
                "state": "not running",
                "runs": 7,
                "last_exit_code": "1",
                "run_interval_seconds": 3600,
            },
            "reports": {
                "semantic_maintain": {
                    "summary": {
                        "status": "failed",
                        "started_at": "2000-01-01T00:00:00+00:00",
                    }
                },
                "semantic_ann_prune": {
                    "summary": {
                        "status": "ok",
                        "started_at": "2000-01-01T00:00:00+00:00",
                    }
                },
            },
            "logs": {"stderr_size_bytes": 12},
        },
        {
            "created_at": "2000-01-01T02:00:00+00:00",
            "health": "degraded",
            "installed": True,
            "issues": ["example"],
            "launchctl": {
                "checked": True,
                "loaded": True,
                "state": "not running",
                "runs": 7,
                "last_exit_code": "1",
                "run_interval_seconds": 3600,
            },
            "reports": {
                "semantic_maintain": {
                    "summary": {
                        "status": "failed",
                        "started_at": "2000-01-01T02:00:00+00:00",
                    }
                },
                "semantic_ann_prune": {
                    "summary": {
                        "status": "ok",
                        "started_at": "2000-01-01T02:00:00+00:00",
                    }
                },
            },
            "logs": {"stderr_size_bytes": 12},
        },
    ]
    (reports / "semantic-launchd-monitor.jsonl").write_text(
        "\n".join(json.dumps(item) for item in history) + "\n",
        encoding="utf-8",
    )

    result = run_semantic_launchd_audit(out, consecutive_unhealthy_threshold=2, max_snapshot_age_seconds=1)

    codes = {alert["code"] for alert in result["alerts"]}
    assert result["health"] == "alert"
    assert "latest_snapshot_stale" in codes
    assert "latest_health_not_ok" in codes
    assert "last_exit_nonzero" in codes
    assert "natural_run_overdue" in codes
    assert "maintain_status_failed" in codes
    assert "stderr_has_output" in codes
    assert "consecutive_unhealthy_snapshots" in codes


def test_semantic_launchd_audit_can_send_macos_notification(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    reports = out / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "semantic-launchd-monitor.jsonl").write_text(
        json.dumps(
            {
                "created_at": "2000-01-01T00:00:00+00:00",
                "health": "degraded",
                "installed": True,
                "issues": ["example"],
                "launchctl": {
                    "checked": True,
                    "loaded": True,
                    "state": "not running",
                    "runs": 1,
                    "last_exit_code": "7",
                    "run_interval_seconds": 3600,
                },
                "reports": {
                    "semantic_maintain": {"summary": {"status": "failed", "started_at": "2000-01-01T00:00:00+00:00"}},
                    "semantic_ann_prune": {"summary": {"status": "ok", "started_at": "2000-01-01T00:00:00+00:00"}},
                },
                "logs": {"stderr_size_bytes": 0},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr("agent_context.launchd.subprocess.run", fake_run)

    result = run_semantic_launchd_audit(out, notify=True, notify_on="warning")

    assert result["health"] == "alert"
    assert result["notification"]["requested"] is True
    assert result["notification"]["sent"] is True
    assert calls
    command = calls[0][0][0]
    assert command[0] == "osascript"
    assert "display notification" in command[2]
    assert "Agent Context Semantic Audit" in command[2]


def test_semantic_launchd_audit_skips_notification_below_threshold(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    reports = out / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone()
    (reports / "semantic-launchd-monitor.jsonl").write_text(
        json.dumps(
            {
                "created_at": now.isoformat(),
                "health": "ok",
                "installed": True,
                "issues": [],
                "launchctl": {
                    "checked": True,
                    "loaded": True,
                    "state": "not running",
                    "runs": 1,
                    "last_exit_code": "0",
                    "run_interval_seconds": 3600,
                },
                "reports": {
                    "semantic_maintain": {"summary": {"status": "ok", "started_at": now.isoformat()}},
                    "semantic_ann_prune": {"summary": {"status": "ok", "started_at": now.isoformat()}},
                },
                "logs": {"stderr_size_bytes": 0},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def fail_run(*_args, **_kwargs):
        raise AssertionError("osascript should not run for ok health with notify_on=alert")

    monkeypatch.setattr("agent_context.launchd.subprocess.run", fail_run)

    result = run_semantic_launchd_audit(out, min_snapshots=1, notify=True, notify_on="alert")

    assert result["health"] == "ok"
    assert result["notification"]["requested"] is True
    assert result["notification"]["sent"] is False
    assert result["notification"]["skipped_reason"] == "health_below_threshold"


def test_semantic_launchd_recover_dry_run_plans_install_and_bootstrap(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    launch_agents = tmp_path / "LaunchAgents"

    def fake_run(*args, **kwargs):
        assert args[0][:2] == ["launchctl", "print"]
        return subprocess.CompletedProcess(args[0], 113, stdout="", stderr="not found")

    monkeypatch.setattr("agent_context.launchd.subprocess.run", fake_run)

    result = run_semantic_launchd_recover(out, launch_agents_dir=launch_agents)

    action_ids = [action["id"] for action in result["actions"]]
    assert result["status"] == "planned"
    assert result["dry_run"] is True
    assert action_ids == ["install_files", "bootstrap"]
    assert not (launch_agents / "com.gengrf.agent-context.semantic-maintenance.plist").exists()


def test_semantic_launchd_recover_apply_installs_and_bootstraps(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    launch_agents = tmp_path / "LaunchAgents"
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args[0])
        if args[0][:2] == ["launchctl", "print"]:
            return subprocess.CompletedProcess(args[0], 113, stdout="", stderr="not found")
        if args[0][:2] == ["launchctl", "bootstrap"]:
            return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")
        raise AssertionError(args[0])

    monkeypatch.setattr("agent_context.launchd.subprocess.run", fake_run)

    result = run_semantic_launchd_recover(out, launch_agents_dir=launch_agents, apply=True)

    plist = launch_agents / "com.gengrf.agent-context.semantic-maintenance.plist"
    script = out / "scripts" / "com.gengrf.agent-context.semantic-maintenance.sh"
    assert result["status"] == "applied"
    assert result["dry_run"] is False
    assert plist.exists()
    assert script.exists()
    assert [action["status"] for action in result["actions"]] == ["applied", "applied"]
    assert any(call[:2] == ["launchctl", "bootstrap"] for call in calls)


def test_semantic_launchd_recover_verify_after_apply(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    launch_agents = tmp_path / "LaunchAgents"
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args[0])
        if args[0][:2] == ["launchctl", "print"] and not (launch_agents / "com.gengrf.agent-context.semantic-maintenance.plist").exists():
            return subprocess.CompletedProcess(args[0], 113, stdout="", stderr="not found")
        if args[0][:2] == ["launchctl", "print"]:
            return subprocess.CompletedProcess(
                args[0],
                0,
                stdout="\n".join(
                    [
                        "gui/501/com.gengrf.agent-context.semantic-maintenance = {",
                        "\tstate = not running",
                        "\truns = 1",
                        "\tlast exit code = 0",
                        "\trun interval = 3600 seconds",
                        "}",
                    ]
                ),
                stderr="",
            )
        if args[0][:2] == ["launchctl", "bootstrap"]:
            return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")
        raise AssertionError(args[0])

    monkeypatch.setattr("agent_context.launchd.subprocess.run", fake_run)

    result = run_semantic_launchd_recover(
        out,
        launch_agents_dir=launch_agents,
        apply=True,
        verify_after_apply=True,
    )

    assert result["status"] == "applied"
    assert result["verification"]["requested"] is True
    assert result["verification"]["passed"] is True
    check_names = {check["name"] for check in result["verification"]["checks"]}
    assert {"installed", "launchctl_loaded", "audit_not_alert"} <= check_names
    assert Path(result["verification"]["monitor_path"]).exists()


def test_semantic_launchd_recover_plans_kickstart_for_loaded_alert(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    launch_agents = tmp_path / "LaunchAgents"
    run_semantic_launchd(out, action="install", label="com.example.agent-context.test", launch_agents_dir=launch_agents)
    reports = out / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "semantic-launchd-monitor.jsonl").write_text(
        json.dumps(
            {
                "created_at": "2000-01-01T00:00:00+00:00",
                "health": "degraded",
                "installed": True,
                "issues": [],
                "launchctl": {
                    "checked": True,
                    "loaded": True,
                    "state": "not running",
                    "runs": 3,
                    "last_exit_code": "1",
                    "run_interval_seconds": 3600,
                },
                "reports": {
                    "semantic_maintain": {"summary": {"status": "failed", "started_at": "2000-01-01T00:00:00+00:00"}},
                    "semantic_ann_prune": {"summary": {"status": "ok", "started_at": "2000-01-01T00:00:00+00:00"}},
                },
                "logs": {"stderr_size_bytes": 0},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def fake_run(*args, **kwargs):
        assert args[0][:2] == ["launchctl", "print"]
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout="\n".join(
                [
                    "gui/501/com.example.agent-context.test = {",
                    "\tstate = not running",
                    "\truns = 3",
                    "\tlast exit code = 1",
                    "\trun interval = 3600 seconds",
                    "}",
                ]
            ),
            stderr="",
        )

    monkeypatch.setattr("agent_context.launchd.os.getuid", lambda: 501)
    monkeypatch.setattr("agent_context.launchd.subprocess.run", fake_run)

    result = run_semantic_launchd_recover(
        out,
        label="com.example.agent-context.test",
        launch_agents_dir=launch_agents,
    )

    action_ids = [action["id"] for action in result["actions"]]
    assert result["status"] == "planned"
    assert action_ids == ["kickstart"]
    assert result["actions"][0]["command"] == ["launchctl", "kickstart", "-k", "gui/501/com.example.agent-context.test"]


def test_semantic_launchd_trend_marks_single_day_short_window(tmp_path: Path) -> None:
    out = tmp_path / "out"
    reports = out / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    history = [
        {
            "created_at": "2026-06-16T01:00:00+00:00",
            "health": "ok",
            "launchctl": {"runs": 1, "run_interval_seconds": 3600},
            "reports": {
                "semantic_maintain": {"summary": {"status": "ok"}},
                "semantic_ann_prune": {"summary": {"status": "ok"}},
            },
        },
        {
            "created_at": "2026-06-16T02:00:00+00:00",
            "health": "ok",
            "launchctl": {"runs": 2, "run_interval_seconds": 3600},
            "reports": {
                "semantic_maintain": {"summary": {"status": "ok"}},
                "semantic_ann_prune": {"summary": {"status": "ok"}},
            },
        },
    ]
    (reports / "semantic-launchd-monitor.jsonl").write_text(
        "\n".join(json.dumps(item) for item in history) + "\n",
        encoding="utf-8",
    )

    result = run_semantic_launchd_trend(out, min_days=2)

    assert result["status"] == "short_window"
    assert result["confidence"] == "short_window"
    assert result["metrics"]["days_observed"] == 1
    assert result["metrics"]["runs_delta"] == 1
    assert result["daily"][0]["bucket"] == "2026-06-16"
    assert Path(result["latest_json_path"]).exists()


def test_semantic_launchd_trend_reports_multi_day_ok(tmp_path: Path) -> None:
    out = tmp_path / "out"
    reports = out / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    history = [
        {
            "created_at": "2026-06-15T23:00:00+00:00",
            "health": "ok",
            "launchctl": {"runs": 10, "run_interval_seconds": 3600},
            "reports": {
                "semantic_maintain": {"summary": {"status": "ok"}},
                "semantic_ann_prune": {"summary": {"status": "ok"}},
            },
        },
        {
            "created_at": "2026-06-16T01:00:00+00:00",
            "health": "ok",
            "launchctl": {"runs": 12, "run_interval_seconds": 3600},
            "reports": {
                "semantic_maintain": {"summary": {"status": "skipped"}},
                "semantic_ann_prune": {"summary": {"status": "ok"}},
            },
        },
    ]
    (reports / "semantic-launchd-monitor.jsonl").write_text(
        "\n".join(json.dumps(item) for item in history) + "\n",
        encoding="utf-8",
    )

    result = run_semantic_launchd_trend(out, min_days=2)

    assert result["status"] == "ok"
    assert result["confidence"] == "multi_day"
    assert result["metrics"]["days_observed"] == 2
    assert result["metrics"]["unhealthy_snapshots"] == 0
    assert [item["bucket"] for item in result["daily"]] == ["2026-06-15", "2026-06-16"]


def test_semantic_launchd_cli_print(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out"
    launch_agents = tmp_path / "LaunchAgents"

    assert main(
        [
            "semantic-launchd",
            "--out",
            str(out),
            "--print",
            "--launch-agents-dir",
            str(launch_agents),
            "--interval-minutes",
            "20",
        ]
    ) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["action"] == "print"
    assert result["interval_minutes"] == 20
    assert "semantic-maintain" in result["script_text"]
    assert not Path(result["plist_path"]).exists()


def test_semantic_launchd_cli_status(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out"
    launch_agents = tmp_path / "LaunchAgents"

    assert main(
        [
            "semantic-launchd-status",
            "--out",
            str(out),
            "--launch-agents-dir",
            str(launch_agents),
        ]
    ) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["health"] == "not_installed"
    assert result["installed"] is False


def test_semantic_launchd_cli_monitor(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out"
    launch_agents = tmp_path / "LaunchAgents"

    assert main(
        [
            "semantic-launchd-monitor",
            "--out",
            str(out),
            "--launch-agents-dir",
            str(launch_agents),
            "--max-history",
            "5",
        ]
    ) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["semantic_launchd_monitor_version"] == "0.1"
    assert Path(result["history_path"]).exists()
    assert result["snapshot"]["health"] == "not_installed"


def test_semantic_launchd_cli_wait_timeout(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out"
    launch_agents = tmp_path / "LaunchAgents"

    assert main(
        [
            "semantic-launchd-wait",
            "--out",
            str(out),
            "--launch-agents-dir",
            str(launch_agents),
            "--timeout-seconds",
            "0",
            "--poll-seconds",
            "1",
        ]
    ) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["semantic_launchd_wait_version"] == "0.1"
    assert result["status"] == "timeout"
    assert Path(result["report_json_path"]).exists()


def test_semantic_launchd_cli_audit_and_mcp(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out"

    assert main(
        [
            "semantic-launchd-audit",
            "--out",
            str(out),
            "--max-history",
            "5",
        ]
    ) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["semantic_launchd_audit_version"] == "0.1"
    assert result["health"] == "warning"
    assert result["alerts"][0]["code"] == "monitor_history_missing"
    assert Path(result["report_json_path"]).exists()

    mcp_result = mcp_semantic_launchd_audit(out_root=str(out), max_history=5)
    assert mcp_result["mcp_version"] == "0.1"
    assert mcp_result["semantic_launchd_audit_version"] == "0.1"


def test_semantic_launchd_cli_recover_and_mcp(tmp_path: Path, capsys, monkeypatch) -> None:
    out = tmp_path / "out"
    launch_agents = tmp_path / "LaunchAgents"

    def fake_run(*args, **kwargs):
        assert args[0][:2] == ["launchctl", "print"]
        return subprocess.CompletedProcess(args[0], 113, stdout="", stderr="not found")

    monkeypatch.setattr("agent_context.launchd.subprocess.run", fake_run)

    assert main(
        [
            "semantic-launchd-recover",
            "--out",
            str(out),
            "--launch-agents-dir",
            str(launch_agents),
        ]
    ) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["semantic_launchd_recover_version"] == "0.1"
    assert result["status"] == "planned"
    assert result["dry_run"] is True
    assert result["verification"]["status"] == "skipped"

    mcp_result = mcp_semantic_launchd_recover(out_root=str(out), launch_agents_dir=str(launch_agents))
    assert mcp_result["mcp_version"] == "0.1"
    assert mcp_result["semantic_launchd_recover_version"] == "0.1"


def test_semantic_launchd_cli_trend_and_mcp(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out"

    assert main(
        [
            "semantic-launchd-trend",
            "--out",
            str(out),
        ]
    ) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["semantic_launchd_trend_version"] == "0.1"
    assert result["status"] == "missing"
    assert Path(result["report_json_path"]).exists()

    mcp_result = mcp_semantic_launchd_trend(out_root=str(out))
    assert mcp_result["mcp_version"] == "0.1"
    assert mcp_result["semantic_launchd_trend_version"] == "0.1"
