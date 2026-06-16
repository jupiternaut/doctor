from __future__ import annotations

import json
from pathlib import Path

from agent_context.cli import main
from agent_context.feedback_replay import expected_rank, normalize_replay_case
from agent_context.io import read_jsonl, write_jsonl
from agent_context.mcp_server import mcp_feedback_replay_cases


def make_project(root: Path, name: str, readme: str) -> Path:
    project = root / name
    (project / ".git").mkdir(parents=True)
    (project / "src").mkdir()
    (project / "README.md").write_text(readme, encoding="utf-8")
    (project / "src" / "ranker.py").write_text(
        "def rank_items(candidates):\n    return sorted(candidates)\n",
        encoding="utf-8",
    )
    return project


def test_feedback_replay_reports_before_after_rerank(tmp_path: Path, capsys) -> None:
    projects_root = tmp_path / "projects"
    make_project(projects_root, "alpha-recommender", "Personal recommendation system ranking feedback.")
    beta = make_project(projects_root, "beta-recommender", "Personal recommendation system ranking feedback.")
    out = tmp_path / "out"
    goal = "告诉我本地所有项目里如何构建个人推荐系统"

    assert main(
        [
            "index-projects",
            "--out",
            str(out),
            "--project-root",
            str(projects_root),
            "--max-files-per-project",
            "20",
        ]
    ) == 0
    capsys.readouterr()
    write_jsonl(
        out / "feedback" / "mcp_feedback.jsonl",
        [
            {
                "goal": goal,
                "selected_source": {
                    "project_name": beta.name,
                    "project_path": str(beta),
                    "source_group": "git_repositories",
                },
                "rating": 5,
            }
        ],
    )

    assert main(
        [
            "feedback-replay",
            "--out",
            str(out),
            "--case",
            goal,
            "--source-scope",
            "gitProjects",
            "--limit",
            "5",
        ]
    ) == 0

    result = json.loads(capsys.readouterr().out)
    report_json = Path(result["report_json_path"])
    report_md = Path(result["report_md_path"])
    payload = json.loads(report_json.read_text(encoding="utf-8"))
    case = payload["cases"][0]

    assert report_json.exists()
    assert report_md.exists()
    assert result["cases"] == 1
    assert payload["feedback_model_version"] == "0.7"
    assert case["query_family"] == "recommendation_system+project_code"
    assert case["baseline"]["top_sources"]
    assert case["with_feedback"]["top_sources"]
    assert any(
        source.get("project_name") == beta.name
        and source["resolver_score_parts"]["feedback_query_family_source"] > 0
        for source in case["with_feedback"]["top_sources"]
    )
    assert "Feedback Replay Report" in report_md.read_text(encoding="utf-8")


def test_feedback_replay_cases_generate_from_feedback_logs(tmp_path: Path, capsys) -> None:
    projects_root = tmp_path / "projects"
    make_project(projects_root, "alpha-recommender", "Personal recommendation system ranking feedback.")
    beta = make_project(projects_root, "beta-recommender", "Personal recommendation system ranking feedback.")
    out = tmp_path / "out"
    goal = "告诉我本地所有项目里如何构建个人推荐系统"
    assert main(
        [
            "index-projects",
            "--out",
            str(out),
            "--project-root",
            str(projects_root),
            "--max-files-per-project",
            "20",
        ]
    ) == 0
    capsys.readouterr()
    write_jsonl(
        out / "feedback" / "mcp_feedback.jsonl",
        [
            {
                "goal": goal,
                "selected_source": {
                    "project_name": beta.name,
                    "project_path": str(beta),
                    "source_group": "git_repositories",
                },
                "rating": 5,
            }
        ],
    )
    write_jsonl(
        out / "feedback" / "retrieval_eval_cases.jsonl",
        [
            {
                "query": "recommendation ranking project",
                "source": "projects",
                "expected_sources": [beta.name],
                "origin_id": "eval:beta",
            }
        ],
    )

    assert main(["feedback-replay-cases", "--out", str(out), "--source-scope", "gitProjects"]) == 0
    generated = json.loads(capsys.readouterr().out)
    cases = read_jsonl(Path(generated["output_cases_path"]))

    assert generated["cases"] == 2
    assert {case["origin"] for case in cases} == {"mcp_feedback", "retrieval_eval_case"}
    assert {case["source_scope"] for case in cases} == {"gitProjects"}
    assert all(case["expected_source"] for case in cases)

    assert main(["feedback-replay", "--out", str(out), "--limit", "5"]) == 0
    replay_result = json.loads(capsys.readouterr().out)
    assert replay_result["cases"] == 2

    mcp_result = mcp_feedback_replay_cases(out_root=str(out), source_scope="gitProjects")
    assert mcp_result["mcp_version"] == "0.1"
    assert mcp_result["cases"] == 2


def test_feedback_replay_cases_generate_from_retrieval_eval_reports(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out"
    report = out / "reports" / "retrieval_eval_20260616000000000000.json"
    report.parent.mkdir(parents=True)
    report.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "query": "recommendation system ranking feedback local project",
                        "source": "projects",
                        "expected_sources": ["data/preference_state.json"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert main(["feedback-replay-cases", "--out", str(out)]) == 0
    result = json.loads(capsys.readouterr().out)
    cases = read_jsonl(Path(result["output_cases_path"]))

    assert result["cases"] == 1
    assert cases[0]["origin"] == "retrieval_eval_report"
    assert cases[0]["source_scope"] == "gitProjects"
    assert cases[0]["expected_source"] == "data/preference_state.json"


def test_feedback_replay_expected_rank_matches_path_fragments() -> None:
    assert (
        expected_rank(
            [{"path": "/Users/gengrf/project/data/preference_state.json"}],
            "data/preference_state.json",
        )
        == 1
    )


def test_feedback_replay_limit_caps_case_limit() -> None:
    case = normalize_replay_case(
        {
            "goal": "recommendation system ranking feedback local project",
            "source_scope": "gitProjects",
            "limit": 12,
        },
        source_scope="all",
        limit=8,
    )

    assert case["limit"] == 8
