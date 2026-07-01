from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


REQUIRED_DOC_HEADINGS = {
    "docs/DEVELOPER_ONBOARDING.md": [
        "# Developer Onboarding",
        "## Product Boundary",
        "## First Hour",
        "## What To Read Next",
        "## Developer Roles",
        "## Safety Rules",
    ],
    "docs/ARCHITECTURE.md": [
        "# Architecture",
        "## System View",
        "## Layers",
        "## Technology Stack",
        "## Frontend Boundary",
        "## Main Runtime Flow",
    ],
    "docs/MODULE_MAP.md": [
        "# Module Map",
        "## Entry Points",
        "## Ingestion And Extraction",
        "## Resolver And Hot Packs",
        "## Mirror And Personal Ranking",
        "## Runtime And Review Gates",
    ],
    "docs/DATA_CONTRACT.md": [
        "# Data Contract",
        "## Source Versus Generated Data",
        "## Important Directories",
        "## Hot Context Packs",
        "## Privacy Boundary",
    ],
    "docs/RUNTIME_FLOW.md": [
        "# Runtime Flow",
        "## Stage Overview",
        "## Phase 1: Normalize",
        "## Phase 2: Resolve",
        "## Phase 3: Context And Answer Review",
        "## Phase 4: Execution Review",
        "## Phase 5: Feedback",
    ],
    "docs/KNOWN_GAPS.md": [
        "# Known Gaps",
        "## Current Maturity Boundary",
        "## Core Product Risk",
        "## Mirror Personalization Is Incomplete",
        "## What Not To Claim Yet",
    ],
    "docs/ROADMAP.md": [
        "# Roadmap",
        "## Milestone 1: Developer Handoff",
        "## Milestone 2: Context Activation Quality",
        "## Milestone 3: Mirror Personalization",
        "## Milestone 7: Public Release Boundary",
    ],
}


def test_developer_onboarding_docs_exist_with_required_headings() -> None:
    for relative_path, headings in REQUIRED_DOC_HEADINGS.items():
        path = ROOT / relative_path
        assert path.exists(), relative_path
        text = path.read_text(encoding="utf-8")
        for heading in headings:
            assert heading in text, f"{relative_path} missing {heading}"


def test_readme_links_new_developer_entrypoint() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "New Developer Start Here" in readme
    assert "docs/DEVELOPER_ONBOARDING.md" in readme


def test_docs_capture_product_boundary_and_frontend_reality() -> None:
    onboarding = (ROOT / "docs" / "DEVELOPER_ONBOARDING.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    gaps = (ROOT / "docs" / "KNOWN_GAPS.md").read_text(encoding="utf-8")

    assert "Doctor = local context compiler" in onboarding
    assert "Mirror = personal ranking and review layer" in onboarding
    assert "OKF / LLM-Wiki = long-term knowledge representation layer" in onboarding
    assert "MCP / CLI / Lab = delivery interfaces" in onboarding
    assert "mirror-lab-server" in onboarding
    assert "file://" in onboarding

    assert "There is no React, Vue, Next.js, Tauri, or Electron app" in architecture
    assert "Python-generated HTML/CSS/vanilla JavaScript" in architecture
    assert "FastMCP" in architecture

    assert "context activation quality" in gaps
    assert "PLM, Drama, Codex++, and Gugu" in gaps
    assert "V1 as not release-accepted" in gaps
