from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .io import append_jsonl, ensure_dir, read_jsonl, write_text
from .vault_index import parse_frontmatter


PROFILE_GRAPH_VERSION = "0.2"
PROFILE_EVENT_VERSION = "0.2"
PROFILE_DIFF_VERSION = "0.2"

CLAIM_CATEGORY_LABELS = {
    "main_project_candidate": "Main Project Candidates",
    "resume_project_candidate": "Resume Project Candidates",
    "negative_feedback_project": "Negative Feedback Projects",
}
CLAIM_CATEGORY_ORDER = {
    "main_project_candidate": 0,
    "resume_project_candidate": 1,
    "negative_feedback_project": 2,
}
MAIN_KEYWORDS = {
    "main",
    "primary",
    "active",
    "focus",
    "core",
    "flagship",
    "主项目",
    "主线",
    "核心",
    "重点",
}
RESUME_KEYWORDS = {
    "resume",
    "portfolio",
    "job",
    "interview",
    "career",
    "简历",
    "作品集",
    "求职",
    "面试",
}
NEGATIVE_KEYWORDS = {
    "negative",
    "reject",
    "rejected",
    "bad",
    "wrong",
    "not_right",
    "downrank",
    "irrelevant",
    "useless",
    "负反馈",
    "不对",
    "不相关",
    "淘汰",
}


def build_profile_graph(out_root: Path) -> dict[str, Any]:
    """Build a deterministic profile graph draft from local reviewed sources."""
    root = out_root.expanduser().resolve()
    built_at = now_iso()
    projects = load_vault_projects(root)
    profile_events = read_jsonl(profile_events_path(root))
    mirror_feedback = read_jsonl(mirror_feedback_path(root))
    project_by_id = {project["target_id"]: project for project in projects}

    claims: list[dict[str, Any]] = []
    for project in projects:
        claims.append(main_project_claim(project, profile_events, built_at=built_at))
        resume_claim = resume_project_claim(project, profile_events, built_at=built_at)
        if resume_claim is not None:
            claims.append(resume_claim)

    event_targets = sorted({str(event.get("target_id") or "").strip() for event in profile_events if str(event.get("target_id") or "").strip()})
    for target_id in event_targets:
        if target_id in project_by_id:
            continue
        events = [event for event in profile_events if str(event.get("target_id") or "").strip() == target_id]
        if any(is_main_label(str(event.get("label") or "")) for event in events):
            claims.append(main_project_claim(event_only_project(target_id), profile_events, built_at=built_at))
        if any(is_resume_label(str(event.get("label") or "")) for event in events):
            claims.append(resume_project_claim(event_only_project(target_id), profile_events, built_at=built_at))

    negative_claims = negative_feedback_claims(projects, profile_events, mirror_feedback, built_at=built_at)
    claims.extend(negative_claims)
    claims = sorted(claims, key=claim_sort_key)

    return {
        "profile_graph_version": PROFILE_GRAPH_VERSION,
        "status": "draft",
        "canonical_write": False,
        "built_at": built_at,
        "out_root": str(root),
        "sources": {
            "vault_projects_path": str(root / "vault" / "projects"),
            "profile_events_path": str(profile_events_path(root)),
            "mirror_feedback_path": str(mirror_feedback_path(root)),
            "project_count": len(projects),
            "profile_event_count": len(profile_events),
            "mirror_feedback_count": len(mirror_feedback),
        },
        "nodes": sorted([project_node(project) for project in projects], key=lambda item: item["target_id"]),
        "claim_groups": group_claim_ids(claims),
        "claims": claims,
    }


def record_profile_event(
    out_root: Path,
    *,
    target_id: str,
    label: str,
    source: str = "manual",
    note: str = "",
) -> dict[str, Any]:
    root = out_root.expanduser().resolve()
    clean_target = target_id.strip()
    clean_label = label.strip()
    if not clean_target:
        raise ValueError("target_id is required")
    if not clean_label:
        raise ValueError("label is required")

    created_at = now_iso()
    record = {
        "profile_event_version": PROFILE_EVENT_VERSION,
        "event_id": stable_id("profile-event", created_at, clean_target, clean_label, source, note),
        "created_at": created_at,
        "target_id": clean_target,
        "label": clean_label,
        "source": source.strip() or "manual",
        "note": note.strip(),
    }
    append_jsonl(profile_events_path(root), record)
    return {
        "status": "ok",
        "event": record,
        "profile_events_path": str(profile_events_path(root)),
    }


def propose_profile_diff(out_root: Path) -> dict[str, Any]:
    root = out_root.expanduser().resolve()
    graph = build_profile_graph(root)
    created_at = now_iso()
    diff_id = unique_diff_id(root, created_at)
    diff_root = ensure_dir(root / "profiles" / "diffs" / diff_id)
    previous_graph = load_canonical_graph(root)
    diff = compare_graphs(previous_graph, graph)
    payload = {
        "profile_diff_version": PROFILE_DIFF_VERSION,
        "diff_id": diff_id,
        "created_at": created_at,
        "status": "pending_review",
        "canonical_write": False,
        "diff": diff,
        "graph": graph,
    }
    diff_json = diff_root / "diff.json"
    diff_md = diff_root / "PROFILE_DIFF.md"
    write_text(diff_json, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(diff_md, render_profile_diff(payload))
    return {
        "status": "ok",
        "diff_id": diff_id,
        "canonical_write": False,
        "claims": len(graph["claims"]),
        "diff_json_path": str(diff_json),
        "profile_diff_md_path": str(diff_md),
        "diff_root": str(diff_root),
    }


def approve_profile_diff(out_root: Path, diff_id: str) -> dict[str, Any]:
    root = out_root.expanduser().resolve()
    diff_json = root / "profiles" / "diffs" / diff_id / "diff.json"
    if not diff_json.exists():
        raise FileNotFoundError(f"profile diff not found: {diff_json}")

    payload = json.loads(diff_json.read_text(encoding="utf-8"))
    graph = dict(payload["graph"])
    approved_at = now_iso()
    graph["status"] = "approved"
    graph["canonical_write"] = True
    graph["approved_at"] = approved_at
    graph["approved_diff_id"] = diff_id
    graph["claims"] = [dict(claim, status="approved") for claim in graph.get("claims", [])]
    graph["claim_groups"] = group_claim_ids(graph["claims"])

    profiles = ensure_dir(root / "profiles")
    graph_path = profiles / "profile_graph.json"
    markdown_path = profiles / "personal_profile.md"
    write_text(graph_path, json.dumps(graph, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_text(markdown_path, render_personal_profile(graph))
    return {
        "status": "ok",
        "diff_id": diff_id,
        "canonical_write": True,
        "claims": len(graph["claims"]),
        "profile_graph_path": str(graph_path),
        "personal_profile_md_path": str(markdown_path),
    }


def profile_events_path(root: Path) -> Path:
    return root / "profiles" / "profile_events.jsonl"


def mirror_feedback_path(root: Path) -> Path:
    return root / "feedback" / "mirror_feedback.jsonl"


def load_vault_projects(root: Path) -> list[dict[str, Any]]:
    projects_dir = root / "vault" / "projects"
    projects = []
    for path in sorted(projects_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, body = parse_frontmatter(text)
        target_id = str(frontmatter.get("id") or path.stem).strip()
        title = str(frontmatter.get("title") or target_id).strip()
        description = str(frontmatter.get("description") or first_paragraph(body) or title).strip()
        projects.append(
            {
                "target_id": target_id,
                "title": title,
                "description": description,
                "aliases": list_field(frontmatter.get("aliases")),
                "tags": list_field(frontmatter.get("tags")),
                "source_path": str(frontmatter.get("source_path") or ""),
                "source_status": str(frontmatter.get("source_status") or ""),
                "path": str(path.resolve()),
                "relative_path": str(path.relative_to(root)),
                "body": body,
                "confidence": numeric_confidence(frontmatter.get("confidence"), default=0.35),
                "freshness": frontmatter.get("freshness") if isinstance(frontmatter.get("freshness"), dict) else {},
            }
        )
    return projects


def main_project_claim(project: dict[str, Any], events: list[dict[str, Any]], *, built_at: str) -> dict[str, Any]:
    matched_events = [event for event in events if event_matches_project(event, project) and is_main_label(str(event.get("label") or ""))]
    keyword_hits = keyword_hits_for(project, MAIN_KEYWORDS)
    evidence = [
        vault_project_evidence(project, "Tracked project in OKF vault; every tracked project is a reviewable main-project candidate.")
    ]
    evidence.extend(keyword_evidence(project, keyword_hits, "main_project_marker"))
    evidence.extend(profile_event_evidence(event) for event in matched_events)
    confidence = clamp(0.36 + (project.get("confidence", 0.35) * 0.22) + (0.12 if keyword_hits else 0.0) + (0.18 * len(matched_events)))
    return claim_record(
        project,
        "main_project_candidate",
        f"Project `{project['title']}` is a main project candidate.",
        evidence,
        confidence,
        built_at=built_at,
    )


def resume_project_claim(project: dict[str, Any], events: list[dict[str, Any]], *, built_at: str) -> dict[str, Any] | None:
    matched_events = [event for event in events if event_matches_project(event, project) and is_resume_label(str(event.get("label") or ""))]
    keyword_hits = keyword_hits_for(project, RESUME_KEYWORDS)
    if not matched_events and not keyword_hits:
        return None
    evidence = keyword_evidence(project, keyword_hits, "resume_marker")
    evidence.extend(profile_event_evidence(event) for event in matched_events)
    confidence = clamp(0.42 + (project.get("confidence", 0.35) * 0.25) + (0.1 if keyword_hits else 0.0) + (0.16 * len(matched_events)))
    return claim_record(
        project,
        "resume_project_candidate",
        f"Project `{project['title']}` is a resume project candidate.",
        evidence,
        confidence,
        built_at=built_at,
    )


def negative_feedback_claims(
    projects: list[dict[str, Any]],
    events: list[dict[str, Any]],
    mirror_feedback: list[dict[str, Any]],
    *,
    built_at: str,
) -> list[dict[str, Any]]:
    project_by_id = {project["target_id"]: project for project in projects}
    evidence_by_target: dict[str, list[dict[str, Any]]] = {}

    for event in events:
        if not is_negative_label(str(event.get("label") or "")):
            continue
        target_id = resolve_event_target(event, projects)
        evidence_by_target.setdefault(target_id, []).append(profile_event_evidence(event))

    for record in mirror_feedback:
        if not is_negative_feedback(record):
            continue
        target_id = resolve_feedback_target(record, projects)
        if not target_id:
            continue
        evidence_by_target.setdefault(target_id, []).append(mirror_feedback_evidence(record))

    claims = []
    for target_id, evidence in sorted(evidence_by_target.items()):
        project = project_by_id.get(target_id) or event_only_project(target_id)
        confidence = clamp(0.52 + 0.12 * len(evidence))
        claims.append(
            claim_record(
                project,
                "negative_feedback_project",
                f"Project `{project['title']}` has negative feedback and should be reviewed before being promoted.",
                evidence,
                confidence,
                built_at=built_at,
            )
        )
    return claims


def claim_record(
    project: dict[str, Any],
    category: str,
    claim: str,
    evidence: list[dict[str, Any]],
    confidence: float,
    *,
    built_at: str,
) -> dict[str, Any]:
    claim_id = f"{category}:{project['target_id']}"
    return {
        "claim_id": claim_id,
        "target_id": project["target_id"],
        "target_title": project["title"],
        "category": category,
        "claim": claim,
        "evidence": evidence,
        "confidence": round(confidence, 3),
        "freshness": {
            "status": "current" if evidence else "weak",
            "checked_at": built_at,
            "source_count": len(evidence),
            "source_latest_at": latest_evidence_time(evidence),
        },
        "status": "proposed",
    }


def project_node(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "target_id": project["target_id"],
        "title": project["title"],
        "aliases": project.get("aliases", []),
        "tags": project.get("tags", []),
        "path": project.get("path", ""),
        "source_path": project.get("source_path", ""),
        "source_status": project.get("source_status", ""),
    }


def vault_project_evidence(project: dict[str, Any], detail: str) -> dict[str, Any]:
    return {
        "source": "vault_project",
        "path": project.get("path", ""),
        "target_id": project["target_id"],
        "detail": detail,
        "quote": compact(project.get("description") or project.get("body") or project["title"], limit=220),
        "created_at": checked_at_from_project(project),
    }


def keyword_evidence(project: dict[str, Any], hits: list[str], marker: str) -> list[dict[str, Any]]:
    if not hits:
        return []
    return [
        {
            "source": "vault_project",
            "path": project.get("path", ""),
            "target_id": project["target_id"],
            "field": "frontmatter/body",
            "detail": marker,
            "matched_terms": hits,
            "quote": compact(project_text(project), limit=220),
            "created_at": checked_at_from_project(project),
        }
    ]


def profile_event_evidence(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "profile_event",
        "target_id": str(event.get("target_id") or ""),
        "event_id": str(event.get("event_id") or ""),
        "label": str(event.get("label") or ""),
        "note": str(event.get("note") or ""),
        "origin": str(event.get("source") or "manual"),
        "created_at": str(event.get("created_at") or ""),
    }


def mirror_feedback_evidence(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "mirror_feedback",
        "target_id": str(record.get("target_id") or record.get("project_id") or record.get("concept_id") or ""),
        "rating": record.get("rating"),
        "label": str(record.get("label") or record.get("feedback") or record.get("action") or record.get("status") or ""),
        "reason": str(record.get("reason") or record.get("note") or ""),
        "created_at": str(record.get("created_at") or record.get("timestamp") or ""),
    }


def compare_graphs(previous: dict[str, Any] | None, proposed: dict[str, Any]) -> dict[str, Any]:
    previous_claims = {claim["claim_id"]: claim for claim in (previous or {}).get("claims", [])}
    proposed_claims = {claim["claim_id"]: claim for claim in proposed.get("claims", [])}
    added = sorted(set(proposed_claims) - set(previous_claims))
    removed = sorted(set(previous_claims) - set(proposed_claims))
    changed = sorted(
        claim_id
        for claim_id in set(previous_claims) & set(proposed_claims)
        if stable_claim_hash(previous_claims[claim_id]) != stable_claim_hash(proposed_claims[claim_id])
    )
    return {
        "previous_claim_count": len(previous_claims),
        "proposed_claim_count": len(proposed_claims),
        "added_claim_ids": added,
        "removed_claim_ids": removed,
        "changed_claim_ids": changed,
        "unchanged_claim_count": len(set(previous_claims) & set(proposed_claims)) - len(changed),
    }


def render_profile_diff(payload: dict[str, Any]) -> str:
    diff = payload["diff"]
    graph = payload["graph"]
    lines = [
        f"# Profile Diff: {payload['diff_id']}",
        "",
        "## Review Boundary",
        "",
        "- Canonical write: `false`",
        "- Source policy: local vault projects, mirror feedback, and profile events only",
        "- Approval target: `profiles/profile_graph.json` and `profiles/personal_profile.md`",
        "",
        "## Change Summary",
        "",
        f"- Previous claims: {diff['previous_claim_count']}",
        f"- Proposed claims: {diff['proposed_claim_count']}",
        f"- Added: {len(diff['added_claim_ids'])}",
        f"- Changed: {len(diff['changed_claim_ids'])}",
        f"- Removed: {len(diff['removed_claim_ids'])}",
        "",
    ]
    lines.extend(render_grouped_claims(graph.get("claims", [])))
    return "\n".join(lines).rstrip() + "\n"


def render_personal_profile(graph: dict[str, Any]) -> str:
    lines = [
        "# Personal Profile",
        "",
        f"- Profile graph version: `{graph['profile_graph_version']}`",
        f"- Approved diff: `{graph.get('approved_diff_id', '')}`",
        f"- Approved at: `{graph.get('approved_at', '')}`",
        "",
    ]
    for category, title in CLAIM_CATEGORY_LABELS.items():
        claims = [claim for claim in graph.get("claims", []) if claim.get("category") == category]
        lines.append(f"## {title}")
        lines.append("")
        if not claims:
            lines.append("- _No approved claims._")
            lines.append("")
            continue
        lines.extend(render_claim_markdown_lines(claims))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_grouped_claims(claims: list[dict[str, Any]]) -> list[str]:
    lines = []
    for category, title in CLAIM_CATEGORY_LABELS.items():
        lines.append(f"## {title}")
        lines.append("")
        category_claims = [claim for claim in claims if claim.get("category") == category]
        if not category_claims:
            lines.append("- _No proposed claims._")
            lines.append("")
            continue
        lines.extend(render_claim_markdown_lines(category_claims))
    return lines


def render_claim_markdown_lines(claims: list[dict[str, Any]]) -> list[str]:
    lines = []
    for claim in sorted(claims, key=claim_sort_key):
        lines.extend(
            [
                f"### {claim['target_title']}",
                "",
                f"- Claim: {claim['claim']}",
                f"- Confidence: `{claim['confidence']}`",
                f"- Status: `{claim['status']}`",
                f"- Freshness: `{claim['freshness'].get('status')}` ({claim['freshness'].get('source_count')} evidence records)",
                "- Evidence:",
            ]
        )
        for evidence in claim.get("evidence", []):
            detail = evidence.get("detail") or evidence.get("label") or evidence.get("reason") or evidence.get("source")
            path = evidence.get("path")
            suffix = f" `{path}`" if path else ""
            lines.append(f"  - `{evidence.get('source')}`: {detail}{suffix}")
        lines.append("")
    return lines


def load_canonical_graph(root: Path) -> dict[str, Any] | None:
    path = root / "profiles" / "profile_graph.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def group_claim_ids(claims: list[dict[str, Any]]) -> dict[str, list[str]]:
    groups = {category: [] for category in CLAIM_CATEGORY_LABELS}
    for claim in sorted(claims, key=claim_sort_key):
        groups.setdefault(claim["category"], []).append(claim["claim_id"])
    return groups


def event_only_project(target_id: str) -> dict[str, Any]:
    return {
        "target_id": target_id,
        "title": target_id,
        "description": f"Profile event target `{target_id}` without a matching vault project page.",
        "aliases": [],
        "tags": [],
        "source_path": "",
        "source_status": "profile_event_only",
        "path": "",
        "relative_path": "",
        "body": "",
        "confidence": 0.25,
        "freshness": {},
    }


def event_matches_project(event: dict[str, Any], project: dict[str, Any]) -> bool:
    raw = str(event.get("target_id") or "").strip()
    if not raw:
        return False
    keys = project_match_keys(project)
    return raw.lower() in keys


def project_match_keys(project: dict[str, Any]) -> set[str]:
    values = [
        project.get("target_id"),
        project.get("title"),
        project.get("source_path"),
        Path(str(project.get("source_path") or "")).name,
    ]
    values.extend(project.get("aliases") or [])
    return {str(value).strip().lower() for value in values if str(value).strip()}


def resolve_event_target(event: dict[str, Any], projects: list[dict[str, Any]]) -> str:
    raw = str(event.get("target_id") or "").strip()
    for project in projects:
        if raw.lower() in project_match_keys(project):
            return project["target_id"]
    return raw


def resolve_feedback_target(record: dict[str, Any], projects: list[dict[str, Any]]) -> str:
    explicit = first_text_value(record, ("target_id", "project_id", "concept_id", "source_id", "selected_source", "path", "source"))
    if explicit:
        resolved = resolve_raw_target(explicit, projects)
        if resolved:
            return resolved
    serialized = json.dumps(record, ensure_ascii=False, sort_keys=True).lower()
    for project in projects:
        for key in project_match_keys(project):
            if key and key in serialized:
                return project["target_id"]
    return explicit


def resolve_raw_target(value: str, projects: list[dict[str, Any]]) -> str:
    lower = value.lower()
    for project in projects:
        if lower in project_match_keys(project):
            return project["target_id"]
        source_path = str(project.get("source_path") or "").lower()
        if source_path and source_path in lower:
            return project["target_id"]
        if project["target_id"].lower() in lower:
            return project["target_id"]
    return value


def first_text_value(record: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = record.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def is_main_label(label: str) -> bool:
    return has_keyword(label, MAIN_KEYWORDS)


def is_resume_label(label: str) -> bool:
    return has_keyword(label, RESUME_KEYWORDS)


def is_negative_label(label: str) -> bool:
    return has_keyword(label, NEGATIVE_KEYWORDS)


def is_negative_feedback(record: dict[str, Any]) -> bool:
    rating = record.get("rating")
    if isinstance(rating, (int, float)):
        return rating <= 2
    if isinstance(rating, str):
        try:
            return float(rating) <= 2
        except ValueError:
            if is_negative_label(rating):
                return True
    text = " ".join(str(record.get(key) or "") for key in ("label", "feedback", "action", "status", "reason", "note"))
    if is_negative_label(text):
        return True
    if record.get("selected") is False:
        return True
    return False


def has_keyword(text: str, keywords: set[str]) -> bool:
    lower = text.lower()
    return any(keyword.lower() in lower for keyword in keywords)


def keyword_hits_for(project: dict[str, Any], keywords: set[str]) -> list[str]:
    text = project_text(project).lower()
    return sorted(keyword for keyword in keywords if keyword.lower() in text)


def project_text(project: dict[str, Any]) -> str:
    return " ".join(
        str(value)
        for value in (
            project.get("target_id"),
            project.get("title"),
            project.get("description"),
            " ".join(project.get("aliases") or []),
            " ".join(project.get("tags") or []),
            project.get("body"),
        )
        if value
    )


def checked_at_from_project(project: dict[str, Any]) -> str:
    freshness = project.get("freshness")
    if isinstance(freshness, dict):
        checked_at = str(freshness.get("checked_at") or "").strip()
        if checked_at:
            return checked_at
    return ""


def latest_evidence_time(evidence: list[dict[str, Any]]) -> str:
    values = sorted(str(item.get("created_at") or "").strip() for item in evidence if str(item.get("created_at") or "").strip())
    return values[-1] if values else ""


def claim_sort_key(claim: dict[str, Any]) -> tuple[int, float, str]:
    return (
        CLAIM_CATEGORY_ORDER.get(str(claim.get("category")), 99),
        -float(claim.get("confidence") or 0.0),
        str(claim.get("target_id") or ""),
    )


def stable_claim_hash(claim: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in claim.items()
        if key not in {"freshness", "status"}
    }
    freshness = dict(claim.get("freshness") or {})
    freshness.pop("checked_at", None)
    payload["freshness"] = freshness
    return stable_id("claim", json.dumps(payload, ensure_ascii=False, sort_keys=True))


def unique_diff_id(root: Path, created_at: str) -> str:
    compact_time = re.sub(r"\D", "", created_at)[:20]
    base = f"profile-diff-{compact_time}"
    candidate = base
    index = 2
    while (root / "profiles" / "diffs" / candidate).exists():
        candidate = f"{base}-{index}"
        index += 1
    return candidate


def stable_id(*parts: object) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def numeric_confidence(value: Any, *, default: float) -> float:
    try:
        return clamp(float(value))
    except (TypeError, ValueError):
        return default


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def list_field(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def first_paragraph(text: str) -> str:
    for block in re.split(r"\n\s*\n", text):
        compact_block = compact(block, limit=400)
        if compact_block and not compact_block.startswith("#"):
            return compact_block
    return ""


def compact(text: str, *, limit: int) -> str:
    value = " ".join(str(text).strip().split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."
