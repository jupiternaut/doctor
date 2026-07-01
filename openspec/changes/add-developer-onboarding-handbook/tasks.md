## 1. Baseline And Scope

- [x] 1.1 Inspect current `README.md`, `pyproject.toml`, `src/agent_context/`, `docs/`, and latest acceptance reports before writing onboarding docs.
- [x] 1.2 Record the current maturity boundary: Doctor runtime exists, Mirror Lab exists, V1 acceptance may be failed/stale, and generated local data is not a portable public artifact.
- [x] 1.3 Decide whether examples should use `/Users/gengrf/agent-context-system` for local handoff docs or generic `~/doctor-data` paths for public docs.

## 2. New Developer Entry Point

- [x] 2.1 Create `docs/DEVELOPER_ONBOARDING.md` with the product boundary: Doctor, Mirror, OKF / LLM-Wiki, MCP / CLI / Lab.
- [x] 2.2 Add a first-hour setup path with `uv sync`, a quick pytest command, a Mirror Lab launch command, and a resolver/context-pack command.
- [x] 2.3 Explain that `mirror-lab-server` is the interactive localhost path and `mirror-lab` / `file://` output is only an offline snapshot.
- [x] 2.4 Add a short "what to read next" path for Python/tooling developers, frontend developers, MCP/client developers, and product reviewers.

## 3. Architecture And Runtime Docs

- [x] 3.1 Create `docs/ARCHITECTURE.md` with a Mermaid diagram from local sources through extraction, cold index, resolver, hot pack, Mirror feedback, and agent clients.
- [x] 3.2 Document the technology stack: Python, `uv`, `argparse`, MarkItDown, JSONL, Markdown, SQLite/FTS, FastMCP, stdlib HTTP server, vanilla JavaScript, pytest, optional fastembed.
- [x] 3.3 Create `docs/RUNTIME_FLOW.md` that explains normalize, resolve, answer/context review, execution review, and feedback stages.
- [x] 3.4 Link runtime flow back to existing deeper docs such as `DOCTOR_RUNTIME_VM.md`, `DOCTOR_LAB.md`, `MCP_SERVER.md`, and `MIRROR_LAB_V0_2.md`.

## 4. Module Map And Data Contract

- [x] 4.1 Create `docs/MODULE_MAP.md` mapping major source files to responsibilities and likely edit points.
- [x] 4.2 Include explicit sections for ingestion, cold index, resolver, hot pack, MCP, runtime VM, Mirror Lab, Mirror ranker, profile graph, vault, semantic maintenance, and Douyin provider.
- [x] 4.3 Create `docs/DATA_CONTRACT.md` covering `manifests/`, `extracted/`, `indexes/`, `packs/`, `feedback/`, `vault/`, `reports/`, `mirror_lab/`, and runtime session artifacts.
- [x] 4.4 Mark which data is generated, which data may contain private local evidence, and which directories should be sanitized or excluded from public handoff.

## 5. Known Gaps And Roadmap

- [x] 5.1 Create `docs/KNOWN_GAPS.md` with current limitations around context activation quality, Mirror personalization, feedback learning, OCR/media parsing, archive expansion, ANN/rerank, UI productization, and reproducibility.
- [x] 5.2 Include the specific personalization acceptance gap: relevant tasks should reliably prioritize PLM, Drama, Codex++, and Gugu when appropriate.
- [x] 5.3 Create `docs/ROADMAP.md` with practical next milestones instead of broad vision language.
- [x] 5.4 Separate "must fix for developer handoff" from "future product research".

## 6. Discoverability And Validation

- [x] 6.1 Add a "New Developer Start Here" link to `README.md`.
- [x] 6.2 Add a lightweight docs validation test or script that verifies the required onboarding docs exist.
- [x] 6.3 Validate required headings in the onboarding docs so the package cannot silently become incomplete.
- [x] 6.4 Ensure examples use commands that are non-destructive and do not mutate original user files.

## 7. Verification

- [x] 7.1 Run the docs validation command or test.
- [x] 7.2 Run targeted tests for the current surfaces referenced by the docs, including Mirror Lab and resolver tests.
- [x] 7.3 Run `openspec status --change "add-developer-onboarding-handbook"` and confirm all planning artifacts are complete.
- [x] 7.4 Review the generated docs as a new developer: confirm they explain what to edit, how to run, what data is generated, and what is still unreliable.
