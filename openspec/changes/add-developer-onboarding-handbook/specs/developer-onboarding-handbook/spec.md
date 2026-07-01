## ADDED Requirements

### Requirement: Handbook defines the Doctor/Mirror product boundary
The system SHALL provide newcomer documentation that defines Doctor, Mirror, OKF / LLM-Wiki, and delivery interfaces before describing implementation modules.

#### Scenario: New developer reads the onboarding entrypoint
- **WHEN** a developer opens `docs/DEVELOPER_ONBOARDING.md`
- **THEN** the document states that Doctor is the local context compiler
- **AND** the document states that Mirror is the personal ranking and review layer
- **AND** the document states that OKF / LLM-Wiki is the long-term knowledge representation layer
- **AND** the document states that MCP / CLI / Lab / review server are delivery interfaces

#### Scenario: New developer distinguishes Doctor from search
- **WHEN** the handbook explains the product purpose
- **THEN** it describes the core output as a reviewable context pack
- **AND** it does not describe Doctor as only a search engine or a generic RAG chat page

### Requirement: Handbook includes a complete architecture overview
The system SHALL include an architecture document that explains the major runtime layers, implementation language, storage formats, interfaces, and current frontend strategy.

#### Scenario: Developer reviews architecture
- **WHEN** a developer opens `docs/ARCHITECTURE.md`
- **THEN** the document shows the flow from local sources to extraction, cold index, resolver, hot pack, Mirror feedback, and agent clients
- **AND** it identifies Python as the main implementation language
- **AND** it identifies JSONL, Markdown, and SQLite as the primary local data formats
- **AND** it identifies FastMCP as the MCP framework
- **AND** it identifies Python-generated HTML/CSS/vanilla JavaScript served by stdlib HTTP servers as the current UI implementation

#### Scenario: Frontend engineer reviews the project
- **WHEN** a frontend engineer reads the architecture document
- **THEN** the document makes clear that there is no React, Vue, Next.js, Tauri, or Electron frontend in the current Doctor repository
- **AND** it explains which files generate Mirror Lab and runtime review pages

### Requirement: Handbook maps source modules to responsibilities
The system SHALL include a module map that lets a developer find the correct source file for a change without reading the whole repository.

#### Scenario: Developer needs to edit ingestion behavior
- **WHEN** a developer opens `docs/MODULE_MAP.md`
- **THEN** it points ingestion work to `src/agent_context/ingest.py` and `src/agent_context/policies.py`
- **AND** it points hot pack work to `src/agent_context/pack.py`
- **AND** it points resolver work to `src/agent_context/resolver.py`

#### Scenario: Developer needs to edit Mirror behavior
- **WHEN** a developer opens `docs/MODULE_MAP.md`
- **THEN** it points Mirror UI work to `src/agent_context/mirror_lab.py`
- **AND** it points ranking feedback work to `src/agent_context/mirror_ranker.py`
- **AND** it points personal profile work to `src/agent_context/profile_graph.py`

#### Scenario: Developer needs to edit integration behavior
- **WHEN** a developer opens `docs/MODULE_MAP.md`
- **THEN** it points CLI work to `src/agent_context/cli.py`
- **AND** it points MCP work to `src/agent_context/mcp_server.py`
- **AND** it points runtime review work to `src/agent_context/runtime_vm.py` and `src/agent_context/runtime_review_server.py`

### Requirement: Handbook documents data contracts and generated artifact boundaries
The system SHALL include a data contract document that explains source-owned files, generated local data, privacy boundaries, and task artifacts.

#### Scenario: Developer reviews generated data
- **WHEN** a developer opens `docs/DATA_CONTRACT.md`
- **THEN** it explains `manifests/*.jsonl`, `extracted/*.md`, `indexes/*.sqlite`, `packs/*/context.md`, `packs/*/sources.jsonl`, `packs/*/manifest.json`, `feedback/*.jsonl`, `feedback/*.json`, `vault/`, `reports/`, and `mirror_lab/`
- **AND** it labels which directories are generated local artifacts
- **AND** it states that original user files must not be modified by ingestion or context packing

#### Scenario: Developer prepares a public handoff
- **WHEN** the data contract explains release boundaries
- **THEN** it warns that generated paths can contain private local evidence
- **AND** it requires sanitized examples or fixtures for public documentation

### Requirement: Handbook provides runnable first-hour instructions
The system SHALL include onboarding instructions that take a new developer from a fresh checkout to a validated local run.

#### Scenario: Developer starts from a fresh checkout
- **WHEN** a developer opens `docs/DEVELOPER_ONBOARDING.md`
- **THEN** it lists setup commands using `uv sync`
- **AND** it lists targeted pytest commands for a quick verification run
- **AND** it lists how to start Mirror Lab with `uv run ./agent-context mirror-lab-server`
- **AND** it lists how to run a resolver or context-pack command without requiring private local data

#### Scenario: Developer validates the local UI path
- **WHEN** a developer follows the Mirror Lab instructions
- **THEN** the instructions identify the localhost URL
- **AND** they explain that `file://` output is an offline snapshot while `mirror-lab-server` is the interactive path

### Requirement: Handbook documents runtime flow and review gates
The system SHALL include a runtime flow document that explains the intended user journey and which artifacts exist at each stage.

#### Scenario: Developer traces a task through Doctor
- **WHEN** a developer opens `docs/RUNTIME_FLOW.md`
- **THEN** it explains normalize, resolve, answer/context review, execution review, and feedback stages
- **AND** it identifies the expected files for each stage
- **AND** it distinguishes cold index lookup from hot context pack delivery

#### Scenario: Developer integrates a client
- **WHEN** a developer reads the runtime flow
- **THEN** it explains how Codex++, Warp, Codex CLI, or MCP clients should consume Doctor artifacts without embedding resolver internals

### Requirement: Handbook exposes known gaps and maturity honestly
The system SHALL include a known gaps document that prevents new developers from mistaking prototypes for finished features.

#### Scenario: Developer reviews project maturity
- **WHEN** a developer opens `docs/KNOWN_GAPS.md`
- **THEN** it states that context activation quality is still the core product risk
- **AND** it states that Mirror personalization is not yet a finished recommendation system
- **AND** it states that OCR, audio/video transcription, full archive expansion, ANN vector search, and trained rerankers are incomplete or limited unless current evidence proves otherwise
- **AND** it states whether V1 acceptance is currently passing, failing, stale, or unknown

#### Scenario: Developer plans a ranking improvement
- **WHEN** the known gaps document discusses personalization
- **THEN** it calls out the need to reliably prioritize user-core projects such as PLM, Drama, Codex++, and Gugu for relevant tasks

### Requirement: Handbook is linked from the main README
The system SHALL make the newcomer path discoverable from the repository README.

#### Scenario: Developer opens repository root
- **WHEN** a developer reads `README.md`
- **THEN** it includes a visible "New Developer Start Here" or equivalent link to `docs/DEVELOPER_ONBOARDING.md`
- **AND** it does not require the developer to infer the onboarding path from the full documents list

### Requirement: Handbook can be validated
The system SHALL provide a lightweight verification path that checks the onboarding package exists and remains structurally complete.

#### Scenario: Maintainer validates docs structure
- **WHEN** the validation command or test runs
- **THEN** it verifies that required onboarding docs exist
- **AND** it verifies that each required doc contains its required top-level headings
- **AND** it fails if the README onboarding link is missing

#### Scenario: Maintainer validates command examples
- **WHEN** command examples are added to onboarding docs
- **THEN** at least the fast non-destructive commands are covered by tests, smoke checks, or documented manual verification steps
