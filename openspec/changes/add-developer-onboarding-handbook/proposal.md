## Why

Doctor/Mirror now spans many Python modules, generated reports, local indexes, runtime surfaces, and product concepts. A new developer cannot safely continue the project by reading the README and scattered experiment notes because the core product boundary, architecture, data contracts, and known gaps are spread across too many files.

This change creates a developer onboarding handbook contract so another engineer can understand Doctor/Mirror from zero, run the system locally, identify the right module to edit, and avoid confusing Doctor with a generic search engine or Mirror with a finished recommendation model.

## What Changes

- Add a required developer-facing handbook set under `docs/` that explains:
  - Doctor as the local context compiler.
  - Mirror as the personal ranking and review layer.
  - OKF / LLM-Wiki as the long-term knowledge representation layer.
  - MCP / CLI / Lab / review server as delivery interfaces.
- Define the minimum handoff package needed for another developer to work on the project without reading every report or chat thread.
- Standardize architecture, module map, data contract, runtime flow, known gaps, and first-run instructions.
- Add acceptance checks that prove the handbook is grounded in current source files and runnable commands.
- Non-goals:
  - Do not rewrite the product architecture in this change.
  - Do not replace existing detailed docs; create a newcomer path through them.
  - Do not claim Mirror personalization or V1 acceptance is complete when current evidence says otherwise.

## Capabilities

### New Capabilities

- `developer-onboarding-handbook`: Defines the required documentation package, content boundaries, diagrams, source references, and validation criteria for onboarding a new Doctor/Mirror developer.

### Modified Capabilities

None.

## Impact

- Affected local project: `/Users/gengrf/agent-context-system`
- Affected docs:
  - `docs/DEVELOPER_ONBOARDING.md`
  - `docs/ARCHITECTURE.md`
  - `docs/MODULE_MAP.md`
  - `docs/DATA_CONTRACT.md`
  - `docs/RUNTIME_FLOW.md`
  - `docs/KNOWN_GAPS.md`
  - `docs/ROADMAP.md`
- Affected commands:
  - `uv sync`
  - `uv run pytest ...`
  - `uv run ./agent-context mirror-lab-server ...`
  - `uv run ./agent-context resolve ...`
  - `uv run ./agent-context v1-stage-status ...`
- Affected audiences:
  - New Python/tooling engineers.
  - Frontend engineers evaluating whether to productize Mirror Lab.
  - Agent/MCP integration engineers.
  - Future maintainers receiving a GitHub handoff.
