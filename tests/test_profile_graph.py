from __future__ import annotations

import json
from pathlib import Path

from agent_context.io import read_jsonl, write_jsonl
from agent_context.profile_graph import (
    approve_profile_diff,
    build_profile_graph,
    propose_profile_diff,
    record_profile_event,
)


def test_profile_graph_build_propose_and_approve(tmp_path: Path) -> None:
    out = tmp_path / "out"
    project_dir = out / "vault" / "projects"
    project_dir.mkdir(parents=True)
    project_dir.joinpath("project-alpha.md").write_text(
        """---
type: "project"
title: "Alpha Context Runtime"
description: "主项目 candidate for local context and resume packaging."
timestamp: "2026-06-24T00:00:00+00:00"
id: "project-alpha"
aliases: ["Alpha"]
tags: ["project", "resume", "active"]
freshness: {"status": "fresh_at_test", "checked_at": "2026-06-24T00:00:00+00:00"}
confidence: 0.7
source_path: "/Users/example/alpha"
source_status: "available"
---

# Alpha Context Runtime

这个项目适合简历和作品集展示，也是当前主项目候选。
""",
        encoding="utf-8",
    )

    event = record_profile_event(
        out,
        target_id="project-alpha",
        label="negative_feedback",
        source="manual",
        note="Do not promote this project without review.",
    )
    assert Path(event["profile_events_path"]).exists()
    assert read_jsonl(out / "profiles" / "profile_events.jsonl")[0]["target_id"] == "project-alpha"
    write_jsonl(
        out / "feedback" / "mirror_feedback.jsonl",
        [{"target_id": "project-alpha", "rating": 1, "reason": "not a fit for this profile"}],
    )

    graph = build_profile_graph(out)

    assert graph["status"] == "draft"
    assert graph["canonical_write"] is False
    assert not (out / "profiles" / "profile_graph.json").exists()
    categories = {claim["category"] for claim in graph["claims"]}
    assert {
        "main_project_candidate",
        "resume_project_candidate",
        "negative_feedback_project",
    } <= categories
    assert all("evidence" in claim for claim in graph["claims"])
    assert all("confidence" in claim for claim in graph["claims"])
    assert all("status" in claim for claim in graph["claims"])

    proposed = propose_profile_diff(out)

    diff_json_path = Path(proposed["diff_json_path"])
    diff_md_path = Path(proposed["profile_diff_md_path"])
    assert proposed["canonical_write"] is False
    assert diff_json_path.exists()
    assert diff_md_path.exists()
    diff_payload = json.loads(diff_json_path.read_text(encoding="utf-8"))
    assert diff_payload["status"] == "pending_review"
    assert diff_payload["graph"]["claim_groups"]["negative_feedback_project"]
    assert "Negative Feedback Projects" in diff_md_path.read_text(encoding="utf-8")

    approved = approve_profile_diff(out, proposed["diff_id"])

    profile_graph_path = Path(approved["profile_graph_path"])
    personal_profile_path = Path(approved["personal_profile_md_path"])
    assert approved["canonical_write"] is True
    assert profile_graph_path.exists()
    assert personal_profile_path.exists()
    canonical = json.loads(profile_graph_path.read_text(encoding="utf-8"))
    assert canonical["status"] == "approved"
    assert all(claim["status"] == "approved" for claim in canonical["claims"])
    assert all(claim["evidence"] and "confidence" in claim and "freshness" in claim for claim in canonical["claims"])
    assert "Resume Project Candidates" in personal_profile_path.read_text(encoding="utf-8")
