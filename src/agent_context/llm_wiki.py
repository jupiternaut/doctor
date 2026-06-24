from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .io import append_jsonl, ensure_dir, write_text


VAULT_VERSION = "0.1"
DEFAULT_DIFF_ID = "baseline-projects"
SKIP_DIRS = {
    ".bun",
    ".git",
    ".next",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}
ROOT_EVIDENCE_NAMES = (
    "README.md",
    "readme.md",
    "README.markdown",
    "AGENTS.md",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
)


@dataclass(frozen=True)
class ProjectSpec:
    concept_id: str
    title: str
    path: Path
    aliases: tuple[str, ...]
    tags: tuple[str, ...]
    why: str


@dataclass(frozen=True)
class SourceEvidence:
    path: Path
    sha256: str
    size_bytes: int
    citation: str


@dataclass(frozen=True)
class EntitySpec:
    entity_id: str
    title: str
    aliases: tuple[str, ...]
    tags: tuple[str, ...]
    description: str
    disambiguation: str


DEFAULT_ENTITY_SPECS = (
    EntitySpec(
        entity_id="entity-codex",
        title="Codex",
        aliases=("Codex", "Codex CLI", "OpenAI Codex"),
        tags=("entity", "agent", "tool"),
        description="Ambiguous agent/tool name. In Doctor context it usually means the local Codex coding agent or Codex CLI workflow.",
        disambiguation="Do not merge blindly with Codex++ or unrelated code-named projects.",
    ),
    EntitySpec(
        entity_id="entity-doctor",
        title="Doctor",
        aliases=("Doctor", "agent-context-system"),
        tags=("entity", "project", "context-runtime"),
        description="Doctor is the local-first context runtime and OKF/LLM-Wiki vault project.",
        disambiguation="Doctor is the knowledge/runtime project, not the user's Mirror preference layer.",
    ),
    EntitySpec(
        entity_id="entity-mirror",
        title="Mirror",
        aliases=("Mirror",),
        tags=("entity", "feedback", "personal-profile"),
        description="Mirror is the user's preference/profile/ranking layer, demoted from canonical knowledge store to feedback signal.",
        disambiguation="Mirror should not be treated as the source of truth for project facts.",
    ),
    EntitySpec(
        entity_id="entity-plm",
        title="PLM / PlotPilot / 墨枢",
        aliases=("PLM", "PlotPilot", "墨枢"),
        tags=("entity", "project", "writing"),
        description="User's long-running writing/product project and a primary resume candidate.",
        disambiguation="PLM, PlotPilot, and 墨枢 resolve to the same local project entity.",
    ),
    EntitySpec(
        entity_id="entity-gugu",
        title="Gugu / RoomLite",
        aliases=("Gugu", "RoomLite", "gugu-roomlite"),
        tags=("entity", "project", "game"),
        description="User's visual asset and room runtime project.",
        disambiguation="Do not merge Gugu with unrelated pet or image-generation artifacts unless source evidence connects them.",
    ),
)


def run_wiki_command(
    out_root: Path,
    *,
    action: str,
    diff_id: str = DEFAULT_DIFF_ID,
    approve: bool = False,
    reason: str = "",
    failure: bool = False,
    project_specs: list[ProjectSpec] | None = None,
    project_config: Path | None = None,
) -> dict:
    specs = list(project_specs) if project_specs is not None else load_project_specs(project_config or default_project_config_path(out_root))
    if action == "init":
        return init_vault(out_root)
    if action == "compile-baseline":
        return compile_baseline_diff(out_root, specs, diff_id=diff_id)
    if action == "approve":
        return approve_diff(out_root, diff_id=diff_id)
    if action == "reject":
        return reject_diff(out_root, diff_id=diff_id, reason=reason, write_failure=failure)
    if action == "seed-entities":
        return seed_entities(out_root)
    if action == "correct-entity":
        return record_entity_correction(out_root, diff_id=diff_id, reason=reason)
    if action == "contradiction":
        return write_contradiction_concept(out_root, diff_id=diff_id, reason=reason)
    if action == "baseline":
        init_result = init_vault(out_root)
        compile_result = compile_baseline_diff(out_root, specs, diff_id=diff_id)
        result = {"diff_id": diff_id, "init": init_result, "compile": compile_result, "approved": False}
        if approve:
            result["approve"] = approve_diff(out_root, diff_id=diff_id)
            result["approved"] = True
        result["report"] = write_baseline_report(out_root, result)
        return result
    raise ValueError(f"unknown wiki action: {action}")


def default_project_config_path(out_root: Path) -> Path:
    return out_root / "config" / "wiki_projects.json"


def load_project_specs(config_path: Path) -> list[ProjectSpec]:
    path = config_path.expanduser()
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_projects = payload.get("projects") if isinstance(payload, dict) else payload
    if not isinstance(raw_projects, list):
        raise ValueError(f"project config must be a list or an object with a projects list: {path}")
    specs = []
    for index, item in enumerate(raw_projects):
        if not isinstance(item, dict):
            raise ValueError(f"project config entry {index} must be an object: {path}")
        concept_id = str(item.get("concept_id") or "").strip()
        title = str(item.get("title") or "").strip()
        source_path = str(item.get("path") or "").strip()
        why = str(item.get("why") or "").strip()
        if not concept_id or not title or not source_path or not why:
            raise ValueError(f"project config entry {index} must include concept_id, title, path, and why: {path}")
        specs.append(
            ProjectSpec(
                concept_id=concept_id,
                title=title,
                path=resolve_config_path(source_path, path.parent),
                aliases=tuple(str(value) for value in item.get("aliases", []) if str(value).strip()),
                tags=tuple(str(value) for value in item.get("tags", []) if str(value).strip()),
                why=why,
            )
        )
    return specs


def resolve_config_path(value: str, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def init_vault(out_root: Path) -> dict:
    vault = out_root / "vault"
    for name in (
        "projects",
        "entities",
        "workflows",
        "claims",
        "contradictions",
        "failures",
        "sources",
        "diffs",
        "templates",
    ):
        ensure_dir(vault / name)

    templates = {
        "project.md": _template("project", "Project Concept"),
        "entity.md": _template("entity", "Entity Concept"),
        "workflow.md": _template("workflow", "Workflow Concept"),
        "source.md": _template("source", "Source Concept"),
        "claim.md": _template("claim", "Claim Concept"),
        "contradiction.md": _template("contradiction", "Contradiction Concept"),
        "failure.md": _template("failure", "Failure Concept"),
    }
    for filename, text in templates.items():
        path = vault / "templates" / filename
        if not path.exists():
            write_text(path, text)

    index_path = vault / "index.md"
    if not index_path.exists():
        write_text(index_path, _render_index([], now_iso()))

    log_path = vault / "log.md"
    if not log_path.exists():
        timestamp = now_iso()
        write_text(log_path, f"# Vault Log\n\n## {timestamp[:10]}\n* **init**: Created LLM-Wiki / OKF vault shell. ({timestamp})\n")

    return {
        "status": "ok",
        "vault": str(vault),
        "index": str(index_path),
        "log": str(log_path),
        "templates": len(templates),
    }


def compile_baseline_diff(out_root: Path, project_specs: list[ProjectSpec], *, diff_id: str) -> dict:
    init_vault(out_root)
    vault = out_root / "vault"
    diff_root = vault / "diffs" / diff_id
    projects_dir = diff_root / "projects"
    ensure_dir(projects_dir)

    concepts = []
    for spec in project_specs:
        evidence = collect_project_evidence(spec.path)
        concept = build_project_concept(spec, evidence)
        concept_path = projects_dir / f"{spec.concept_id}.md"
        write_text(concept_path, concept)
        concepts.append(
            {
                "concept_id": spec.concept_id,
                "title": spec.title,
                "path": str(concept_path),
                "proposed_page_path": str(concept_path),
                "source_path": str(spec.path),
                "source_status": "available" if spec.path.exists() else "missing",
                "evidence_count": len(evidence),
                "hashes": [item.sha256 for item in evidence],
                "source_files": [
                    {
                        "path": str(item.path),
                        "sha256": item.sha256,
                        "size_bytes": item.size_bytes,
                        "citation": item.citation,
                    }
                    for item in evidence
                ],
            }
        )

    manifest = {
        "vault_version": VAULT_VERSION,
        "diff_id": diff_id,
        "created_at": now_iso(),
        "operation": "compile-baseline",
        "canonical_write": False,
        "raw_files_read_only": True,
        "tool_inputs": {
            "command": "doctor wiki --action compile-baseline",
            "diff_id": diff_id,
            "project_specs": [
                {
                    "concept_id": spec.concept_id,
                    "title": spec.title,
                    "path": str(spec.path),
                    "aliases": list(spec.aliases),
                    "tags": list(spec.tags),
                    "why": spec.why,
                }
                for spec in project_specs
            ],
        },
        "expected_derived_index_updates": [
            "rebuild indexes/vault.sqlite",
            "refresh vault concept FTS",
            "refresh alias rows",
            "refresh graph_edges for tags, source paths, and source hashes",
        ],
        "concepts": concepts,
    }
    write_text(diff_root / "diff_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    write_text(diff_root / "DIFF_SUMMARY.md", _render_diff_summary(manifest))
    return {
        "status": "ok",
        "diff_id": diff_id,
        "diff_root": str(diff_root),
        "concepts": len(concepts),
        "canonical_write": False,
        "raw_files_read_only": True,
    }


def approve_diff(out_root: Path, *, diff_id: str) -> dict:
    vault = out_root / "vault"
    diff_root = vault / "diffs" / diff_id
    manifest_path = diff_root / "diff_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"diff manifest not found: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    timestamp = now_iso()
    approved = []
    for staged in sorted((diff_root / "projects").glob("*.md")):
        target = vault / "projects" / staged.name
        ensure_dir(target.parent)
        write_text(target, _promote_concept_text(staged.read_text(encoding="utf-8"), diff_id=diff_id, timestamp=timestamp))
        approved.append(
            {
                "concept_id": staged.stem,
                "staged_path": str(staged),
                "canonical_path": str(target),
            }
        )

    append_jsonl(
        vault / "approvals.jsonl",
        {
            "approved_at": timestamp,
            "diff_id": diff_id,
            "concept_count": len(approved),
            "raw_files_read_only": True,
        },
    )
    write_text(vault / "index.md", _render_index(load_project_concepts(vault), timestamp))
    _append_log(vault / "log.md", timestamp, "approve", f"Approved diff `{diff_id}` with {len(approved)} project concepts.")
    result = {
        "status": "ok",
        "diff_id": diff_id,
        "approved": len(approved),
        "canonical_write": True,
        "approved_paths": [item["canonical_path"] for item in approved],
        "manifest_concepts": len(manifest.get("concepts", [])),
    }
    write_baseline_report(out_root, {"approve": result, "diff_id": diff_id})
    return result


def seed_entities(out_root: Path, entity_specs: list[EntitySpec] | None = None) -> dict:
    init_vault(out_root)
    vault = out_root / "vault"
    timestamp = now_iso()
    specs = entity_specs or list(DEFAULT_ENTITY_SPECS)
    written = []
    for spec in specs:
        path = vault / "entities" / f"{spec.entity_id}.md"
        write_text(path, build_entity_concept(spec, timestamp))
        written.append(str(path))
    _append_log(vault / "log.md", timestamp, "seed-entities", f"Seeded {len(written)} entity concepts.")
    write_text(vault / "index.md", _render_index(load_project_concepts(vault), timestamp))
    return {"status": "ok", "entities": len(written), "paths": written}


def record_entity_correction(out_root: Path, *, diff_id: str, reason: str) -> dict:
    init_vault(out_root)
    vault = out_root / "vault"
    timestamp = now_iso()
    correction = parse_entity_correction(diff_id)
    record = {
        "recorded_at": timestamp,
        "correction": correction["correction"],
        "entity_id": correction["entity_id"],
        "target_id": correction["target_id"],
        "reason": reason.strip() or "No correction reason was provided.",
        "canonical_pages_rewritten": False,
    }
    append_jsonl(vault / "entity_corrections.jsonl", record)
    _append_log(
        vault / "log.md",
        timestamp,
        "entity-correction",
        f"{record['correction']} `{record['entity_id']}` -> `{record['target_id']}`.",
    )
    return {"status": "ok", "correction": record, "path": str(vault / "entity_corrections.jsonl")}


def write_contradiction_concept(out_root: Path, *, diff_id: str, reason: str) -> dict:
    init_vault(out_root)
    vault = out_root / "vault"
    timestamp = now_iso()
    payload = parse_contradiction_payload(diff_id, reason)
    contradiction_id = stable_contradiction_id(payload["left"], payload["right"], payload["reason"])
    path = vault / "contradictions" / f"{contradiction_id}.md"
    write_text(path, build_contradiction_concept(contradiction_id, payload, timestamp))
    _append_log(vault / "log.md", timestamp, "contradiction", f"Wrote contradiction `{contradiction_id}`.")
    return {"status": "ok", "contradiction_id": contradiction_id, "path": str(path)}


def reject_diff(out_root: Path, *, diff_id: str, reason: str = "", write_failure: bool = False) -> dict:
    vault = out_root / "vault"
    diff_root = vault / "diffs" / diff_id
    manifest_path = diff_root / "diff_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"diff manifest not found: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    timestamp = now_iso()
    reason = reason.strip() or "No rejection reason was provided."
    rejection = {
        "rejected_at": timestamp,
        "diff_id": diff_id,
        "reason": reason,
        "concept_count": len(manifest.get("concepts", [])),
        "canonical_write": False,
        "failure_concept_written": bool(write_failure),
    }
    append_jsonl(vault / "rejections.jsonl", rejection)
    write_text(diff_root / "REJECTION.md", _render_rejection_summary(rejection, manifest))
    failure_path = None
    if write_failure:
        failure_path = vault / "failures" / f"{failure_id_for_diff(diff_id)}.md"
        write_text(failure_path, build_failure_concept(diff_id=diff_id, reason=reason, manifest=manifest, timestamp=timestamp))
    _append_log(vault / "log.md", timestamp, "reject", f"Rejected diff `{diff_id}`. Reason: {escape_table(reason)}")
    return {
        "status": "ok",
        "diff_id": diff_id,
        "rejected": True,
        "canonical_write": False,
        "reason": reason,
        "failure_concept_path": str(failure_path) if failure_path else None,
        "rejection_path": str(diff_root / "REJECTION.md"),
    }


def collect_project_evidence(project_path: Path, *, max_files: int = 8) -> list[SourceEvidence]:
    if not project_path.exists():
        return []

    candidates: list[Path] = []
    if project_path.is_file():
        candidates.append(project_path)
    else:
        for name in ROOT_EVIDENCE_NAMES:
            candidate = project_path / name
            if candidate.is_file():
                candidates.append(candidate)
        docs_dir = project_path / "docs"
        if docs_dir.is_dir():
            for path in sorted(docs_dir.rglob("*.md")):
                if len(candidates) >= max_files:
                    break
                if _should_skip(path):
                    continue
                candidates.append(path)

    seen: set[tuple[int, int]] = set()
    evidence = []
    for path in candidates:
        if not path.is_file():
            continue
        stat = path.stat()
        identity = (stat.st_dev, stat.st_ino)
        if identity in seen:
            continue
        seen.add(identity)
        evidence.append(
            SourceEvidence(
                path=path.resolve(),
                sha256=sha256_file(path),
                size_bytes=stat.st_size,
                citation=extract_citation(path),
            )
        )
        if len(evidence) >= max_files:
            break
    return evidence


def build_project_concept(spec: ProjectSpec, evidence: list[SourceEvidence]) -> str:
    timestamp = now_iso()
    source_status = "available" if spec.path.exists() else "missing"
    summary = summarize_project(spec, evidence)
    citations = [
        {
            "path": str(item.path),
            "sha256": item.sha256,
            "citation": item.citation,
        }
        for item in evidence
    ]
    frontmatter = {
        "type": "project",
        "title": spec.title,
        "description": summary,
        "resource": file_resource_uri(spec.path),
        "timestamp": timestamp,
        "id": spec.concept_id,
        "aliases": list(spec.aliases),
        "tags": list(spec.tags),
        "citations": citations,
        "freshness": {"status": "fresh_at_compile", "checked_at": timestamp},
        "confidence": 0.62 if evidence else 0.28,
        "source_hashes": [item.sha256 for item in evidence],
        "source_path": str(spec.path),
        "source_status": source_status,
        "raw_files_read_only": True,
        "canonical": False,
    }
    rows = "\n".join(
        f"| `{item.path}` | `{item.sha256}` | {item.size_bytes} | {escape_table(item.citation)} |"
        for item in evidence
    )
    if not rows:
        rows = "| _none_ | _none_ | 0 | Source path was missing or no readable evidence file was found. |"

    tags = ", ".join(f"`{tag}`" for tag in spec.tags)
    aliases = ", ".join(spec.aliases)
    citation_lines = "\n".join(
        f"[{index}] [{item.path.name}]({file_resource_uri(item.path)}) — sha256 `{item.sha256}`"
        for index, item in enumerate(evidence, start=1)
    )
    if not citation_lines:
        citation_lines = "[1] Source path was missing or no readable evidence file was found."
    return (
        "---\n"
        + "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in frontmatter.items())
        + "\n---\n\n"
        f"# {spec.title}\n\n"
        "## Summary\n\n"
        f"{summary}\n\n"
        "## Why This Exists\n\n"
        f"{spec.why}\n\n"
        "## Source Identity\n\n"
        f"- Concept ID: `{spec.concept_id}`\n"
        f"- Source path: `{spec.path}`\n"
        f"- Source status: `{source_status}`\n"
        f"- Aliases: {aliases}\n"
        f"- Tags: {tags}\n\n"
        "## Source Evidence\n\n"
        "| Path | SHA-256 | Bytes | Citation |\n"
        "|---|---:|---:|---|\n"
        f"{rows}\n\n"
        "## Claims\n\n"
        f"- This project is a candidate for long-term personal memory because: {spec.why}\n\n"
        "## Limitations\n\n"
        "- This baseline concept is generated from repository-level evidence files only.\n"
        "- It does not claim full semantic understanding of all source files yet.\n"
        "- Changes entered the Brain Diff first; canonical approval is a separate step.\n\n"
        "# Citations\n\n"
        f"{citation_lines}\n"
    )


def build_failure_concept(*, diff_id: str, reason: str, manifest: dict, timestamp: str) -> str:
    failure_id = failure_id_for_diff(diff_id)
    frontmatter = {
        "type": "failure",
        "title": f"Rejected Brain Diff: {diff_id}",
        "description": reason,
        "timestamp": timestamp,
        "id": failure_id,
        "aliases": [diff_id],
        "tags": ["failure", "rejection", "brain-diff"],
        "citations": [{"path": f"vault/diffs/{diff_id}/diff_manifest.json", "sha256": "", "citation": reason}],
        "freshness": {"status": "fresh_at_review", "checked_at": timestamp},
        "confidence": 1.0,
        "source_hashes": sorted({source_hash for concept in manifest.get("concepts", []) for source_hash in concept.get("hashes", [])}),
        "diff_id": diff_id,
        "canonical": True,
        "raw_files_read_only": True,
    }
    concepts = manifest.get("concepts") or []
    concept_lines = "\n".join(f"- `{item.get('concept_id')}` — {item.get('title')}" for item in concepts) or "- _none_"
    return (
        "---\n"
        + "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in frontmatter.items())
        + "\n---\n\n"
        f"# Rejected Brain Diff: {diff_id}\n\n"
        "## Reason\n\n"
        f"{reason}\n\n"
        "## Rejected Concepts\n\n"
        f"{concept_lines}\n\n"
        "## Retrieval Effect\n\n"
        "- This concept records a route that should be downranked or reviewed before reuse.\n"
        "- It is a governance artifact, not a claim that the original source files are wrong.\n\n"
        "# Citations\n\n"
        f"[1] [Diff manifest](/diffs/{diff_id}/diff_manifest.json)\n"
    )


def build_entity_concept(spec: EntitySpec, timestamp: str) -> str:
    frontmatter = {
        "type": "entity",
        "title": spec.title,
        "description": spec.description,
        "timestamp": timestamp,
        "id": spec.entity_id,
        "aliases": list(spec.aliases),
        "tags": list(spec.tags),
        "citations": [],
        "freshness": {"status": "seeded_identity", "checked_at": timestamp},
        "confidence": 0.7,
        "source_hashes": [],
        "canonical": True,
        "raw_files_read_only": True,
    }
    aliases = ", ".join(spec.aliases)
    tags = ", ".join(f"`{tag}`" for tag in spec.tags)
    return (
        "---\n"
        + "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in frontmatter.items())
        + "\n---\n\n"
        f"# {spec.title}\n\n"
        "## Summary\n\n"
        f"{spec.description}\n\n"
        "## Identity\n\n"
        f"- Entity ID: `{spec.entity_id}`\n"
        f"- Aliases: {aliases}\n"
        f"- Tags: {tags}\n\n"
        "## Disambiguation\n\n"
        f"{spec.disambiguation}\n\n"
        "## Limitations\n\n"
        "- This is a seeded identity concept. Factual project claims must still cite source pages or files.\n"
    )


def build_contradiction_concept(contradiction_id: str, payload: dict, timestamp: str) -> str:
    frontmatter = {
        "type": "contradiction",
        "title": f"Contradiction: {payload['left']} vs {payload['right']}",
        "description": payload["reason"],
        "timestamp": timestamp,
        "id": contradiction_id,
        "aliases": [payload["left"], payload["right"]],
        "tags": ["contradiction", payload["severity"]],
        "citations": [],
        "freshness": {"status": "needs_review", "checked_at": timestamp},
        "confidence": 0.8,
        "source_hashes": [],
        "severity": payload["severity"],
        "status": "open",
        "canonical": True,
        "raw_files_read_only": True,
    }
    return (
        "---\n"
        + "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in frontmatter.items())
        + "\n---\n\n"
        f"# Contradiction: {payload['left']} vs {payload['right']}\n\n"
        "## Sides\n\n"
        f"- Left: `{payload['left']}`\n"
        f"- Right: `{payload['right']}`\n"
        f"- Severity: `{payload['severity']}`\n\n"
        "## Reason\n\n"
        f"{payload['reason']}\n\n"
        "## Required Review\n\n"
        "- Decide whether this is a hard conflict, a scope/time difference, or a naming ambiguity.\n"
        "- Do not silently overwrite either side until a reviewed correction exists.\n"
    )


def summarize_project(spec: ProjectSpec, evidence: list[SourceEvidence]) -> str:
    if not evidence:
        return f"{spec.title} is a tracked project concept, but the source path was not available during this compile."
    citation = evidence[0].citation.strip()
    if citation:
        return f"{spec.title} is a tracked project concept. Representative evidence: {citation[:240]}"
    return f"{spec.title} is a tracked project concept with {len(evidence)} hashed local evidence files."


def load_project_concepts(vault: Path) -> list[dict]:
    concepts = []
    for path in sorted((vault / "projects").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        concepts.append(
            {
                "path": path,
                "title": _frontmatter_value(text, "title") or path.stem,
                "description": _frontmatter_value(text, "description") or "",
                "tags": _frontmatter_value(text, "tags") or "[]",
                "source_path": _frontmatter_value(text, "source_path") or "",
            }
        )
    return concepts


def write_baseline_report(out_root: Path, result: dict) -> str:
    report_path = out_root / "reports" / "llm_wiki_baseline_report.md"
    vault = out_root / "vault"
    projects = load_project_concepts(vault)
    diff_root = vault / "diffs" / result.get("diff_id", DEFAULT_DIFF_ID)
    text = (
        "# LLM-Wiki / OKF Vault Baseline Report\n\n"
        "## Boundary\n\n"
        "- Raw source files are read-only.\n"
        "- AI-generated concept writes go to `vault/diffs/` before canonical approval.\n"
        "- Canonical project concepts live under `vault/projects/` only after approval.\n\n"
        "## Outputs\n\n"
        f"- Vault index: `{vault / 'index.md'}`\n"
        f"- Vault log: `{vault / 'log.md'}`\n"
        f"- Latest diff: `{diff_root}`\n"
        f"- Canonical project concepts: {len(projects)}\n\n"
        "## Baseline Concepts\n\n"
        + "".join(f"- [{item['title']}]({item['path']}) from `{item['source_path']}`\n" for item in projects)
        + "\n## Current Result\n\n"
        f"```json\n{json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)}\n```\n"
    )
    write_text(report_path, text)
    return str(report_path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_citation(path: Path, *, limit: int = 220) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = []
    for raw in text.splitlines():
        line = " ".join(raw.strip().split())
        if not line:
            continue
        if line.startswith("!"):
            continue
        lines.append(line)
        if sum(len(item) for item in lines) >= limit:
            break
    return " ".join(lines)[:limit]


def _render_index(projects: list[dict], timestamp: str) -> str:
    project_lines = "".join(
        f"- [{item['title']}]({item['path'].relative_to(item['path'].parents[1])}) — `{item['source_path']}`\n"
        for item in projects
    )
    if not project_lines:
        project_lines = "- No canonical project concepts yet. Review `vault/diffs/` and approve one first.\n"
    return (
        "---\n"
        f'okf_version: "{VAULT_VERSION}"\n'
        "---\n\n"
        "# Doctor LLM-Wiki / OKF Vault\n\n"
        f"Last generated: `{timestamp}`\n\n"
        "This vault is the long-term compiled knowledge layer above raw files and below Doctor retrieval.\n\n"
        "## Canonical Concepts\n\n"
        "### Projects\n\n"
        f"{project_lines}\n"
        "### Other Concept Types\n\n"
        "- Entities: `vault/entities/`\n"
        "- Workflows: `vault/workflows/`\n"
        "- Claims: `vault/claims/`\n"
        "- Contradictions: `vault/contradictions/`\n"
        "- Failures: `vault/failures/`\n\n"
        "## Brain Diff\n\n"
        "AI-generated changes must first be written to `vault/diffs/<diff-id>/` and then explicitly approved.\n"
    )


def _render_diff_summary(manifest: dict) -> str:
    frontmatter = {
        "type": "brain-diff",
        "title": f"Brain Diff: {manifest['diff_id']}",
        "description": "Staged AI-generated concept changes awaiting human approval or rejection.",
        "resource": f"/diffs/{manifest['diff_id']}/diff_manifest.json",
        "tags": ["brain-diff", "governance", "review"],
        "timestamp": manifest["created_at"],
        "id": f"brain-diff-{manifest['diff_id']}",
        "aliases": [manifest["diff_id"]],
        "citations": [{"path": f"vault/diffs/{manifest['diff_id']}/diff_manifest.json", "sha256": "", "citation": "Brain Diff manifest"}],
        "freshness": {"status": "pending_review", "checked_at": manifest["created_at"]},
        "confidence": 1.0,
        "source_hashes": sorted({source_hash for concept in manifest.get("concepts", []) for source_hash in concept.get("hashes", [])}),
        "canonical": False,
        "raw_files_read_only": True,
    }
    lines = [
        "---",
        *[f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in frontmatter.items()],
        "---",
        "",
        "# Brain Diff Summary",
        "",
        f"- Diff ID: `{manifest['diff_id']}`",
        f"- Created: `{manifest['created_at']}`",
        f"- Canonical write: `{manifest['canonical_write']}`",
        f"- Raw files read-only: `{manifest['raw_files_read_only']}`",
        "",
        "## Staged Concepts",
        "",
    ]
    for item in manifest["concepts"]:
        lines.append(
            f"- `{item['concept_id']}` — {item['title']} "
            f"({item['source_status']}, evidence files: {item['evidence_count']})"
        )
    lines.extend(
        [
            "",
            "## Approval",
            "",
            "Run `doctor wiki --action approve --diff-id "
            f"{manifest['diff_id']} --out <doctor-root>` after human review.",
            "",
            "## Rejection",
            "",
            "Run `doctor wiki --action reject --diff-id "
            f"{manifest['diff_id']} --reason <reason> --failure --out <doctor-root>` if this route is a dead end.",
            "",
            "# Citations",
            "",
            f"[1] [Diff manifest](/diffs/{manifest['diff_id']}/diff_manifest.json)",
            "",
        ]
    )
    return "\n".join(lines)


def _render_rejection_summary(rejection: dict, manifest: dict) -> str:
    frontmatter = {
        "type": "brain-diff-rejection",
        "title": f"Brain Diff Rejection: {rejection['diff_id']}",
        "description": rejection["reason"],
        "resource": f"/diffs/{rejection['diff_id']}/diff_manifest.json",
        "tags": ["brain-diff", "rejection", "governance"],
        "timestamp": rejection["rejected_at"],
        "id": f"brain-diff-rejection-{rejection['diff_id']}",
        "aliases": [rejection["diff_id"]],
        "citations": [{"path": f"vault/diffs/{rejection['diff_id']}/diff_manifest.json", "sha256": "", "citation": rejection["reason"]}],
        "freshness": {"status": "reviewed", "checked_at": rejection["rejected_at"]},
        "confidence": 1.0,
        "source_hashes": sorted({source_hash for concept in manifest.get("concepts", []) for source_hash in concept.get("hashes", [])}),
        "canonical": False,
        "raw_files_read_only": True,
    }
    lines = [
        "---",
        *[f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in frontmatter.items()],
        "---",
        "",
        "# Brain Diff Rejection",
        "",
        f"- Diff ID: `{rejection['diff_id']}`",
        f"- Rejected: `{rejection['rejected_at']}`",
        f"- Canonical write: `{rejection['canonical_write']}`",
        f"- Failure concept written: `{rejection['failure_concept_written']}`",
        "",
        "## Reason",
        "",
        rejection["reason"],
        "",
        "## Rejected Concepts",
        "",
    ]
    for item in manifest.get("concepts", []):
        lines.append(f"- `{item.get('concept_id')}` — {item.get('title')}")
    lines.extend(
        [
            "",
            "# Citations",
            "",
            f"[1] [Diff manifest](/diffs/{rejection['diff_id']}/diff_manifest.json)",
        ]
    )
    return "\n".join(lines) + "\n"


def _template(concept_type: str, title: str) -> str:
    timestamp = now_iso()
    concept_id = f"template-{concept_type}"
    return (
        "---\n"
        f'type: "{concept_type}"\n'
        f'title: "{title}"\n'
        'description: ""\n'
        f'timestamp: "{timestamp}"\n'
        f'id: "{concept_id}"\n'
        "aliases: []\n"
        "tags: []\n"
        "citations: []\n"
        f'freshness: {json.dumps({"status": "template", "checked_at": timestamp}, ensure_ascii=False)}\n'
        "confidence: 0.0\n"
        "source_hashes: []\n"
        "---\n\n"
        f"# {title}\n\n"
        "## Summary\n\n"
        "## Source Evidence\n\n"
        "## Claims\n\n"
        "## Limitations\n"
    )


def _append_log(path: Path, timestamp: str, event: str, detail: str) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    date = timestamp[:10]
    entry = f"* **{event}**: {detail} ({timestamp})"
    if not existing.startswith("# Vault Log") or not re.search(r"^## \d{4}-\d{2}-\d{2}$", existing, flags=re.MULTILINE):
        write_text(path, f"# Vault Log\n\n## {date}\n{entry}\n")
        return
    write_text(path, existing.rstrip() + f"\n\n## {date}\n{entry}\n")


def _frontmatter_value(text: str, key: str) -> str:
    if not text.startswith("---\n"):
        return ""
    for line in text.splitlines()[1:]:
        if line == "---":
            break
        prefix = f"{key}: "
        if line.startswith(prefix):
            raw = line[len(prefix) :]
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                return raw.strip().strip('"')
            if isinstance(value, str):
                return value
            return json.dumps(value, ensure_ascii=False)
    return ""


def parse_entity_correction(diff_id: str) -> dict:
    parts = [part.strip() for part in diff_id.split(":", 2)]
    if len(parts) == 3 and parts[0] in {"merge", "split"}:
        return {"correction": parts[0], "entity_id": parts[1], "target_id": parts[2]}
    return {"correction": "split", "entity_id": diff_id.strip() or "unknown", "target_id": ""}


def parse_contradiction_payload(diff_id: str, reason: str) -> dict:
    parts = [part.strip() for part in diff_id.split("::")]
    left = parts[0] if parts and parts[0] else "left"
    right = parts[1] if len(parts) > 1 and parts[1] else "right"
    severity = parts[2] if len(parts) > 2 and parts[2] else "soft"
    if severity not in {"soft", "hard"}:
        severity = "soft"
    return {
        "left": left,
        "right": right,
        "severity": severity,
        "reason": reason.strip() or "No contradiction reason was provided.",
    }


def stable_contradiction_id(left: str, right: str, reason: str) -> str:
    digest = hashlib.sha256(f"{left}|{right}|{reason}".encode("utf-8")).hexdigest()[:12]
    return f"contradiction-{digest}"


def _promote_concept_text(text: str, *, diff_id: str, timestamp: str) -> str:
    if not text.startswith("---\n"):
        return text
    lines = text.splitlines()
    output = [lines[0]]
    inserted = False
    for line in lines[1:]:
        if line.startswith("canonical: "):
            output.append("canonical: true")
            continue
        if line == "---" and not inserted:
            output.append(f"approved_from_diff: {json.dumps(diff_id, ensure_ascii=False)}")
            output.append(f"approved_at: {json.dumps(timestamp, ensure_ascii=False)}")
            inserted = True
        output.append(line)
    return "\n".join(output) + ("\n" if text.endswith("\n") else "")


def _should_skip(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def failure_id_for_diff(diff_id: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", diff_id.strip()).strip("-").lower() or "diff"
    return f"failure-{slug}"


def escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def file_resource_uri(path: Path) -> str:
    expanded = path.expanduser()
    try:
        absolute = expanded.resolve(strict=False)
        return absolute.as_uri()
    except ValueError:
        return f"file://{expanded}"
