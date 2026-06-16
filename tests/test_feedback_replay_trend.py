from __future__ import annotations

import json
from pathlib import Path

from agent_context.cli import main
from agent_context.feedback_replay_trend import run_feedback_replay_trend
from agent_context.mcp_server import mcp_feedback_replay_trend


def write_replay_report(
    out: Path,
    suffix: str,
    *,
    rank_after: int,
    rank_before: int = 3,
    improved: int = 0,
    regressed: int = 0,
) -> Path:
    path = out / "reports" / f"feedback_replay_{suffix}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "created_at": f"2026-06-16T08:{suffix[-2:]}:00+08:00",
                "feedback_model_version": "0.7",
                "summary": {
                    "cases": 1,
                    "changed_top1": 1 if improved or regressed else 0,
                    "improved_expected_top1": improved,
                    "regressed_expected_top1": regressed,
                },
                "cases": [
                    {
                        "goal": "告诉我本地所有项目里如何构建个人推荐系统",
                        "source_scope": "gitProjects",
                        "expected_source": "data/preference_state.json",
                        "delta": {
                            "expected_rank_before": rank_before,
                            "expected_rank_after": rank_after,
                        },
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def test_feedback_replay_trend_reports_stable_improvement(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out"
    write_replay_report(out, "20260616085000000000", rank_after=3)
    write_replay_report(out, "20260616085100000000", rank_after=1, improved=1)

    assert main(["feedback-replay-trend", "--out", str(out), "--max-reports", "5"]) == 0
    result = json.loads(capsys.readouterr().out)
    payload = json.loads(Path(result["report_json_path"]).read_text(encoding="utf-8"))

    assert result["health"] == "ok"
    assert result["reports"] == 2
    assert result["trend_rank_improvements"] == 1
    assert result["trend_rank_regressions"] == 0
    assert payload["summary"]["latest_expected_top1_rate"] == 1.0
    assert "Feedback Replay Trend Report" in Path(result["report_md_path"]).read_text(encoding="utf-8")


def test_feedback_replay_trend_alerts_on_expected_source_loss(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_replay_report(out, "20260616085000000000", rank_after=1)
    write_replay_report(out, "20260616085100000000", rank_after=0)

    result = run_feedback_replay_trend(out, max_reports=5, min_reports=2)

    assert result["health"] == "alert"
    assert result["trend_rank_regressions"] == 1
    assert any("lost" in reason or "regression" in reason for reason in result["reasons"])


def test_feedback_replay_trend_recovers_from_historical_regression(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_replay_report(out, "20260616085000000000", rank_after=1)
    write_replay_report(out, "20260616085100000000", rank_after=3)
    write_replay_report(out, "20260616085200000000", rank_after=1, improved=1)

    result = run_feedback_replay_trend(out, max_reports=5, min_reports=2)
    payload = json.loads(Path(result["report_json_path"]).read_text(encoding="utf-8"))

    assert result["health"] == "ok"
    assert result["trend_rank_regressions"] == 1
    assert result["latest_rank_regressions"] == 0
    assert result["historical_rank_regressions"] == 1
    assert "latest replay recovered" in " ".join(result["reasons"])
    assert payload["summary"]["latest_rank_regressions"] == 0


def test_feedback_replay_trend_mcp_wrapper(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_replay_report(out, "20260616085000000000", rank_after=3)
    write_replay_report(out, "20260616085100000000", rank_after=1, improved=1)

    result = mcp_feedback_replay_trend(out_root=str(out), max_reports=5, min_reports=2)

    assert result["mcp_version"] == "0.1"
    assert result["health"] == "ok"
    assert result["trend_rank_improvements"] == 1
