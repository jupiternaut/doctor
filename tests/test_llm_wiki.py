from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agent_context.llm_wiki import ProjectSpec, load_project_specs, run_wiki_command


def file_hashes(root: Path) -> dict[str, str]:
    hashes = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            hashes[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def make_project(root: Path, name: str, readme: str) -> Path:
    project = root / name
    (project / "docs").mkdir(parents=True)
    (project / "README.md").write_text(readme, encoding="utf-8")
    (project / "docs" / "workflow.md").write_text(f"# {name} workflow\n\nsource-backed workflow note\n", encoding="utf-8")
    return project


def test_init_writes_strict_okf_reserved_files(tmp_path: Path) -> None:
    out = tmp_path / "out"

    run_wiki_command(out, action="init")

    index = (out / "vault" / "index.md").read_text(encoding="utf-8")
    log = (out / "vault" / "log.md").read_text(encoding="utf-8")
    assert index.startswith('---\nokf_version: "0.1"\n---')
    assert 'type: "index"' not in index
    assert log.startswith("# Vault Log")
    assert "| Time | Event |" not in log


def test_project_specs_load_from_private_config_without_hardcoded_defaults(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    sample = make_project(source_root, "sample", "# Sample\n\nlocal evidence\n")
    config = tmp_path / "wiki_projects.json"
    config.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "concept_id": "project-sample",
                        "title": "Sample",
                        "path": str(sample),
                        "aliases": ["Sample"],
                        "tags": ["project", "example"],
                        "why": "sample project evidence",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    out = tmp_path / "out"

    specs = load_project_specs(config)
    result = run_wiki_command(out, action="baseline", diff_id="configured", approve=True, project_config=config)

    assert [spec.concept_id for spec in specs] == ["project-sample"]
    assert result["compile"]["concepts"] == 1
    concept = (out / "vault" / "projects" / "project-sample.md").read_text(encoding="utf-8")
    assert "Sample" in concept
    assert str(sample) in concept


def test_baseline_without_project_config_does_not_use_local_machine_defaults(tmp_path: Path) -> None:
    out = tmp_path / "out"

    result = run_wiki_command(out, action="baseline", diff_id="empty", approve=True)

    assert result["compile"]["concepts"] == 0
    assert not list((out / "vault" / "projects").glob("*.md"))


def test_compile_baseline_writes_diff_without_touching_canonical_or_sources(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    plm = make_project(source_root, "plm", "# PLM\n\nwriting system project\n")
    doctor = make_project(source_root, "doctor", "# Doctor\n\nlocal context runtime\n")
    before_hashes = file_hashes(source_root)
    out = tmp_path / "out"
    specs = [
        ProjectSpec("project-plm", "PLM", plm, ("PLM",), ("project", "resume"), "primary writing project"),
        ProjectSpec("project-doctor", "Doctor", doctor, ("Doctor",), ("project", "context"), "context runtime project"),
    ]

    result = run_wiki_command(out, action="compile-baseline", diff_id="test-diff", project_specs=specs)

    assert result["canonical_write"] is False
    assert before_hashes == file_hashes(source_root)
    assert (out / "vault" / "index.md").exists()
    assert (out / "vault" / "log.md").exists()
    assert (out / "vault" / "diffs" / "test-diff" / "projects" / "project-plm.md").exists()
    assert not (out / "vault" / "projects" / "project-plm.md").exists()

    manifest = json.loads((out / "vault" / "diffs" / "test-diff" / "diff_manifest.json").read_text(encoding="utf-8"))
    assert manifest["raw_files_read_only"] is True
    assert manifest["canonical_write"] is False
    assert len(manifest["concepts"]) == 2
    assert all(item["hashes"] for item in manifest["concepts"])

    concept = (out / "vault" / "diffs" / "test-diff" / "projects" / "project-plm.md").read_text(encoding="utf-8")
    assert 'type: "project"' in concept
    assert 'resource: "file://' in concept
    assert "## Source Evidence" in concept
    assert "# Citations" in concept
    assert "SHA-256" in concept
    assert "raw_files_read_only: true" in concept

    diff_summary = (out / "vault" / "diffs" / "test-diff" / "DIFF_SUMMARY.md").read_text(encoding="utf-8")
    assert 'type: "brain-diff"' in diff_summary


def test_approve_diff_promotes_staged_concepts_and_writes_report(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    gugu = make_project(source_root, "gugu", "# Gugu\n\nasset pipeline project\n")
    before_hashes = file_hashes(source_root)
    out = tmp_path / "out"
    specs = [
        ProjectSpec("project-gugu", "Gugu", gugu, ("Gugu",), ("project", "asset-pipeline"), "visual asset project"),
    ]

    run_wiki_command(out, action="compile-baseline", diff_id="reviewed", project_specs=specs)
    result = run_wiki_command(out, action="approve", diff_id="reviewed")

    assert result["approved"] == 1
    assert result["canonical_write"] is True
    assert before_hashes == file_hashes(source_root)
    assert (out / "vault" / "projects" / "project-gugu.md").exists()
    assert (out / "vault" / "approvals.jsonl").exists()
    assert (out / "reports" / "llm_wiki_baseline_report.md").exists()

    index = (out / "vault" / "index.md").read_text(encoding="utf-8")
    log = (out / "vault" / "log.md").read_text(encoding="utf-8")
    assert index.startswith('---\nokf_version: "0.1"\n---')
    assert 'type: "index"' not in index
    assert "Gugu" in index
    assert "## " in log
    assert "Approved diff `reviewed`" in log


def test_reject_diff_writes_review_artifacts_without_promoting_canonical_pages(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    drama = make_project(source_root, "drama", "# Drama\n\nagent browser runtime project\n")
    before_hashes = file_hashes(source_root)
    out = tmp_path / "out"
    specs = [
        ProjectSpec("project-drama", "Drama", drama, ("Drama",), ("project", "agent-os"), "agent browser project"),
    ]

    run_wiki_command(out, action="compile-baseline", diff_id="bad-route", project_specs=specs)
    result = run_wiki_command(
        out,
        action="reject",
        diff_id="bad-route",
        reason="This route used the wrong project evidence.",
        failure=True,
    )

    assert result["rejected"] is True
    assert result["canonical_write"] is False
    assert before_hashes == file_hashes(source_root)
    assert not (out / "vault" / "projects" / "project-drama.md").exists()
    assert (out / "vault" / "diffs" / "bad-route" / "REJECTION.md").exists()
    rejection = (out / "vault" / "diffs" / "bad-route" / "REJECTION.md").read_text(encoding="utf-8")
    assert 'type: "brain-diff-rejection"' in rejection
    assert (out / "vault" / "rejections.jsonl").exists()
    failure_path = out / "vault" / "failures" / "failure-bad-route.md"
    assert failure_path.exists()
    failure = failure_path.read_text(encoding="utf-8")
    assert 'type: "failure"' in failure
    assert "This route used the wrong project evidence." in failure


def test_entity_seed_correction_and_contradiction_governance(tmp_path: Path) -> None:
    out = tmp_path / "out"

    entities = run_wiki_command(out, action="seed-entities")
    correction = run_wiki_command(
        out,
        action="correct-entity",
        diff_id="split:entity-codex:project-codex-plus-plus",
        reason="Codex and Codex++ are related but not the same entity.",
    )
    contradiction = run_wiki_command(
        out,
        action="contradiction",
        diff_id="entity-doctor::entity-mirror::hard",
        reason="Doctor is canonical knowledge; Mirror is feedback, not source of truth.",
    )

    assert entities["entities"] >= 5
    for entity_id in ("entity-codex", "entity-doctor", "entity-mirror", "entity-plm", "entity-gugu"):
        path = out / "vault" / "entities" / f"{entity_id}.md"
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert f'id: "{entity_id}"' in text
        assert "## Disambiguation" in text

    corrections = (out / "vault" / "entity_corrections.jsonl").read_text(encoding="utf-8")
    assert "entity-codex" in corrections
    assert "project-codex-plus-plus" in corrections
    assert correction["correction"]["canonical_pages_rewritten"] is False

    contradiction_path = Path(contradiction["path"])
    assert contradiction_path.exists()
    contradiction_text = contradiction_path.read_text(encoding="utf-8")
    assert 'type: "contradiction"' in contradiction_text
    assert "Doctor is canonical knowledge" in contradiction_text
