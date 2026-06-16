from __future__ import annotations

from agent_context.codex_plus_smoke import render_codex_plus_smoke_markdown


def test_codex_plus_smoke_markdown_includes_v1_stage_summary() -> None:
    markdown = render_codex_plus_smoke_markdown(
        {
            "status": "ok",
            "created_at": "2026-06-16T13:20:00+08:00",
            "codex_plus_root": "/tmp/codex-plus",
            "error": "",
            "scripts": [
                {
                    "name": "manager_feedback_replay",
                    "status": "ok",
                    "returncode": 0,
                    "path": "/tmp/smoke.mjs",
                    "summary": {
                        "v1StageStatus": {
                            "status": "waiting_for_time",
                            "ok": 8,
                            "waitingForTime": 2,
                            "reportMarkdownPath": "/tmp/v1-stage-status-latest.md",
                        }
                    },
                }
            ],
        }
    )

    assert "## Key Status" in markdown
    assert "manager_feedback_replay" in markdown
    assert "waiting_for_time" in markdown
    assert "ok=8" in markdown
    assert "waiting=2" in markdown
    assert "/tmp/v1-stage-status-latest.md" in markdown
