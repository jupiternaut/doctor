from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .io import ensure_dir, write_text

FEEDBACK_REPLAY_TREND_VERSION = "0.1"
REPLAY_REPORT_PREFIX = "feedback_replay_"


def run_feedback_replay_trend(
    out_root: Path,
    *,
    max_reports: int = 20,
    min_reports: int = 2,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    reports = load_feedback_replay_reports(out_root, max_reports=max(1, int(max_reports)))
    summary = replay_trend_summary(reports, min_reports=max(1, int(min_reports)))
    created_at = datetime.now().astimezone().isoformat(timespec="seconds")
    report_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
    report_root = ensure_dir(out_root / "reports")
    json_path = report_root / f"feedback_replay_trend_{report_id}.json"
    md_path = report_root / f"feedback_replay_trend_{report_id}.md"
    payload = {
        "feedback_replay_trend_version": FEEDBACK_REPLAY_TREND_VERSION,
        "created_at": created_at,
        "out_root": str(out_root),
        "max_reports": max(1, int(max_reports)),
        "min_reports": max(1, int(min_reports)),
        "summary": summary,
        "reports": [report_summary(report) for report in reports],
        "case_trends": case_trends(reports),
    }
    write_text(json_path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(md_path, render_replay_trend_report(payload))
    return {
        "feedback_replay_trend_version": FEEDBACK_REPLAY_TREND_VERSION,
        "created_at": created_at,
        "health": summary["health"],
        "reasons": summary["reasons"],
        "reports": summary["reports"],
        "cases": summary["cases"],
        "latest_expected_top1_rate": summary["latest_expected_top1_rate"],
        "trend_rank_improvements": summary["trend_rank_improvements"],
        "trend_rank_regressions": summary["trend_rank_regressions"],
        "latest_rank_regressions": summary["latest_rank_regressions"],
        "historical_rank_regressions": summary["historical_rank_regressions"],
        "report_json_path": str(json_path),
        "report_md_path": str(md_path),
    }


def feedback_replay_trend_status(
    out_root: Path,
    *,
    max_reports: int = 20,
    min_reports: int = 2,
) -> dict[str, Any]:
    out_root = out_root.expanduser().resolve()
    reports = load_feedback_replay_reports(out_root, max_reports=max(1, int(max_reports)))
    summary = replay_trend_summary(reports, min_reports=max(1, int(min_reports)))
    return {
        "exists": bool(reports),
        "feedback_replay_trend_version": FEEDBACK_REPLAY_TREND_VERSION,
        "health": summary["health"],
        "summary": summary,
        "latest_replay_report_path": summary.get("latest_report_path"),
        "latest_trend_report_path": latest_feedback_replay_trend_report_path(out_root),
    }


def latest_feedback_replay_trend_report_path(out_root: Path) -> str | None:
    reports_root = out_root / "reports"
    if not reports_root.exists():
        return None
    paths = sorted(
        reports_root.glob("feedback_replay_trend_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return str(paths[0]) if paths else None


def load_feedback_replay_reports(out_root: Path, *, max_reports: int) -> list[dict[str, Any]]:
    reports_root = out_root / "reports"
    records = []
    if not reports_root.exists():
        return []
    for path in reports_root.glob(f"{REPLAY_REPORT_PREFIX}*.json"):
        suffix = path.stem.removeprefix(REPLAY_REPORT_PREFIX)
        if not suffix or not suffix[0].isdigit():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        payload["_report_path"] = str(path)
        records.append(payload)
    records.sort(key=lambda report: (str(report.get("created_at") or ""), str(report.get("_report_path") or "")))
    return records[-max_reports:]


def replay_trend_summary(reports: list[dict[str, Any]], *, min_reports: int) -> dict[str, Any]:
    all_cases = [case for report in reports for case in report.get("cases") or []]
    latest_report = reports[-1] if reports else {}
    latest_cases = latest_report.get("cases") or []
    latest_expected_cases = [case for case in latest_cases if case.get("expected_source")]
    latest_expected_top1 = sum(1 for case in latest_expected_cases if case_after_rank(case) == 1)
    latest_expected_missing = sum(1 for case in latest_expected_cases if case_after_rank(case) == 0)
    latest_summary = latest_report.get("summary") or {}
    rank_changes = trend_rank_changes(reports)
    trend_rank_improvements = sum(1 for change in rank_changes if change["direction"] == "improved")
    trend_rank_regressions = sum(1 for change in rank_changes if change["direction"] == "regressed")
    latest_report_path = latest_report.get("_report_path")
    latest_rank_regressions = sum(
        1
        for change in rank_changes
        if change["direction"] == "regressed" and change.get("current_report_path") == latest_report_path
    )
    historical_rank_regressions = max(0, trend_rank_regressions - latest_rank_regressions)
    reasons = []
    health = "ok"
    if not reports:
        health = "warning"
        reasons.append("no feedback replay reports found")
    if reports and len(reports) < min_reports:
        health = max_health(health, "warning")
        reasons.append(f"insufficient replay history: {len(reports)} < {min_reports}")
    if latest_expected_missing:
        health = "alert"
        reasons.append(f"latest replay lost {latest_expected_missing} expected source(s)")
    if int(latest_summary.get("regressed_expected_top1") or 0) > 0:
        health = "alert"
        reasons.append(f"latest replay has {latest_summary.get('regressed_expected_top1')} top1 regression(s)")
    if latest_rank_regressions:
        health = "alert"
        reasons.append(f"latest replay has {latest_rank_regressions} expected-rank regression(s)")
    elif historical_rank_regressions:
        reasons.append(f"history had {historical_rank_regressions} expected-rank regression(s), latest replay recovered")
    if not reasons and reports:
        reasons.append("latest replay history is stable")
    return {
        "health": health,
        "reasons": reasons,
        "reports": len(reports),
        "cases": len(all_cases),
        "latest_report_path": latest_report_path,
        "latest_created_at": latest_report.get("created_at"),
        "latest_cases": len(latest_cases),
        "latest_expected_cases": len(latest_expected_cases),
        "latest_expected_top1": latest_expected_top1,
        "latest_expected_missing": latest_expected_missing,
        "latest_expected_top1_rate": round(latest_expected_top1 / len(latest_expected_cases), 6)
        if latest_expected_cases
        else 0.0,
        "latest_improved_expected_top1": int(latest_summary.get("improved_expected_top1") or 0),
        "latest_regressed_expected_top1": int(latest_summary.get("regressed_expected_top1") or 0),
        "trend_rank_improvements": trend_rank_improvements,
        "trend_rank_regressions": trend_rank_regressions,
        "latest_rank_regressions": latest_rank_regressions,
        "historical_rank_regressions": historical_rank_regressions,
    }


def max_health(existing: str, candidate: str) -> str:
    order = {"ok": 0, "warning": 1, "alert": 2}
    return candidate if order[candidate] > order[existing] else existing


def trend_rank_changes(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    previous_by_case: dict[str, dict[str, Any]] = {}
    changes = []
    for report in reports:
        for case in report.get("cases") or []:
            if not case.get("expected_source"):
                continue
            key = case_key(case)
            current_rank = case_after_rank(case)
            previous = previous_by_case.get(key)
            if previous:
                previous_rank = int(previous["rank"])
                direction = rank_direction(previous_rank, current_rank)
                if direction:
                    changes.append(
                        {
                            "case_key": key,
                            "goal": case.get("goal"),
                            "source_scope": case.get("source_scope"),
                            "expected_source": case.get("expected_source"),
                            "previous_rank": previous_rank,
                            "current_rank": current_rank,
                            "direction": direction,
                            "previous_report_path": previous["report_path"],
                            "current_report_path": report.get("_report_path"),
                        }
                    )
            previous_by_case[key] = {"rank": current_rank, "report_path": report.get("_report_path")}
    return changes


def rank_direction(previous_rank: int, current_rank: int) -> str:
    previous_value = comparable_rank(previous_rank)
    current_value = comparable_rank(current_rank)
    if current_value < previous_value:
        return "improved"
    if current_value > previous_value:
        return "regressed"
    return ""


def comparable_rank(rank: int) -> int:
    return 1_000_000 if int(rank) <= 0 else int(rank)


def case_trends(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_case: dict[str, dict[str, Any]] = {}
    for report in reports:
        for case in report.get("cases") or []:
            if not case.get("expected_source"):
                continue
            key = case_key(case)
            record = by_case.setdefault(
                key,
                {
                    "case_key": key,
                    "goal": case.get("goal"),
                    "source_scope": case.get("source_scope"),
                    "expected_source": case.get("expected_source"),
                    "observations": 0,
                    "best_rank": 0,
                    "worst_rank": 0,
                    "latest_rank": 0,
                    "latest_report_path": "",
                },
            )
            rank = case_after_rank(case)
            record["observations"] += 1
            record["best_rank"] = best_rank(record["best_rank"], rank)
            record["worst_rank"] = worst_rank(record["worst_rank"], rank)
            record["latest_rank"] = rank
            record["latest_report_path"] = report.get("_report_path")
    return sorted(
        by_case.values(),
        key=lambda item: (
            comparable_rank(int(item["latest_rank"])),
            -int(item["observations"]),
            str(item["case_key"]),
        ),
    )


def best_rank(existing: int, rank: int) -> int:
    if int(existing) <= 0:
        return int(rank)
    if int(rank) <= 0:
        return int(existing)
    return min(int(existing), int(rank))


def worst_rank(existing: int, rank: int) -> int:
    if int(existing) <= 0 or int(rank) <= 0:
        return 0
    return max(int(existing), int(rank))


def case_key(case: dict[str, Any]) -> str:
    return "|".join(
        [
            str(case.get("goal") or ""),
            str(case.get("source_scope") or ""),
            str(case.get("expected_source") or ""),
        ]
    )


def case_after_rank(case: dict[str, Any]) -> int:
    delta = case.get("delta") or {}
    if "expected_rank_after" in delta:
        return int(delta.get("expected_rank_after") or 0)
    return int((case.get("with_feedback") or {}).get("expected_rank") or 0)


def report_summary(report: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("summary") or {}
    return {
        "path": report.get("_report_path"),
        "created_at": report.get("created_at"),
        "feedback_model_version": report.get("feedback_model_version"),
        "cases": int(summary.get("cases") or len(report.get("cases") or [])),
        "changed_top1": int(summary.get("changed_top1") or 0),
        "improved_expected_top1": int(summary.get("improved_expected_top1") or 0),
        "regressed_expected_top1": int(summary.get("regressed_expected_top1") or 0),
    }


def render_replay_trend_report(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Feedback Replay Trend Report",
        "",
        f"- Created at: `{payload['created_at']}`",
        f"- Out root: `{payload['out_root']}`",
        f"- Health: `{summary['health']}`",
        f"- Reports: `{summary['reports']}`",
        f"- Cases: `{summary['cases']}`",
        f"- Latest expected top1 rate: `{summary['latest_expected_top1_rate']}`",
        f"- Trend rank improvements: `{summary['trend_rank_improvements']}`",
        f"- Trend rank regressions: `{summary['trend_rank_regressions']}`",
        f"- Latest rank regressions: `{summary['latest_rank_regressions']}`",
        f"- Historical recovered rank regressions: `{summary['historical_rank_regressions']}`",
        "",
        "## Reasons",
        "",
    ]
    for reason in summary["reasons"]:
        lines.append(f"- {reason}")
    lines.extend(
        [
            "",
            "## Report History",
            "",
            "| Report | Created | Cases | Improved Top1 | Regressed Top1 |",
            "| --- | --- | ---: | ---: | ---: |",
        ]
    )
    for report in payload["reports"]:
        lines.append(
            "| "
            f"`{escape_table_value(Path(str(report.get('path') or '')).name)}` | "
            f"`{escape_table_value(report.get('created_at') or '')}` | "
            f"`{report.get('cases')}` | "
            f"`{report.get('improved_expected_top1')}` | "
            f"`{report.get('regressed_expected_top1')}` |"
        )
    lines.extend(
        [
            "",
            "## Case Trends",
            "",
            "| Case | Expected | Observations | Best | Worst | Latest |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for trend in payload["case_trends"][:50]:
        lines.append(
            "| "
            f"`{escape_table_value(trend.get('goal') or '')}` | "
            f"`{escape_table_value(trend.get('expected_source') or '')}` | "
            f"`{trend.get('observations')}` | "
            f"`{trend.get('best_rank')}` | "
            f"`{trend.get('worst_rank')}` | "
            f"`{trend.get('latest_rank')}` |"
        )
    lines.append("")
    return "\n".join(lines)


def escape_table_value(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")
