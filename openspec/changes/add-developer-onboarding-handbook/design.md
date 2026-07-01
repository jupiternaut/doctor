## Context

Doctor/Mirror has grown from a single Downloads context-pack experiment into a local-first context runtime with ingestion, provider manifests, SQLite/FTS indexes, semantic maintenance, MCP tools, runtime review gates, LLM-Wiki / OKF vault work, Mirror Lab, profile graph, and feedback/ranker modules.

The repository already contains many useful documents, but they are optimized for project history and specific experiments. A new developer needs a directed entry path that answers:

- What is the product?
- What is the architecture?
- What language and framework choices are intentional?
- Which module owns which responsibility?
- What files are generated data versus source code?
- How do I run the system locally?
- What is currently incomplete or misleading?

The handbook must also preserve the accepted product boundary:

```text
Doctor = local context compiler
Mirror = personal ranking and review layer
OKF / LLM-Wiki = long-term knowledge representation layer
MCP / CLI / Lab = delivery interfaces
```

## Goals / Non-Goals

**Goals:**

- Create a short newcomer path through the repository.
- Make the architecture understandable without reading chat history.
- Separate source code, generated local data, and release/handoff artifacts.
- Explain the real implementation stack: Python CLI, JSONL/Markdown/SQLite, FastMCP, stdlib HTTP pages, and optional embedding backends.
- Give developers exact first-run commands and expected outputs.
- Make known gaps explicit, including V1 acceptance status, Mirror personalization limits, OCR/media gaps, and rerank/ANN limitations.
- Keep the handbook grounded in file paths that exist in the repository.

**Non-Goals:**

- Do not redesign Doctor/Mirror.
- Do not migrate the frontend to React or another UI framework in this change.
- Do not hide experimental or failed areas to make the project look more mature.
- Do not require generated private local data to be committed.
- Do not make this handbook a marketing site.

## Decisions

### Decision 1: Use a small handbook set instead of one huge document

The onboarding package should include multiple focused files:

```text
docs/DEVELOPER_ONBOARDING.md
docs/ARCHITECTURE.md
docs/MODULE_MAP.md
docs/DATA_CONTRACT.md
docs/RUNTIME_FLOW.md
docs/KNOWN_GAPS.md
docs/ROADMAP.md
```

This keeps each file easy to review and lets different developers enter through architecture, data contracts, runtime flow, or tasks.

Alternative considered: one long `HANDOFF.md`. Rejected because this project already has several handoff documents, and another large one would not solve navigation.

### Decision 2: Treat generated local data as evidence, not source

The handbook should explain generated directories such as `manifests/`, `indexes/`, `packs/`, `reports/`, `mirror_lab/`, `feedback/`, and `vault/`, but it should not require them to be committed or portable by default.

Alternative considered: include sample generated data in the handbook. Rejected because local user paths and private evidence can leak, and public developers need contracts plus fixture commands first.

### Decision 3: Teach the product boundary before the module list

New developers must understand that Doctor is not a generic search UI and Mirror is not yet a finished personal assistant. The handbook should define these roles before pointing to `resolver.py`, `mirror_ranker.py`, or MCP tools.

Alternative considered: start from CLI command inventory. Rejected because the command surface is large and will confuse product ownership if presented first.

### Decision 4: Keep frontend instructions honest

The handbook should state that current UI surfaces are Python-generated HTML/CSS/vanilla JavaScript served by `ThreadingHTTPServer`, not a standalone modern frontend app. If a frontend engineer is onboarded, the first task is productizing or extracting the UI, not modifying a React codebase.

Alternative considered: describe Mirror Lab as "the frontend." Rejected because it overstates maturity and hides the current implementation boundary.

### Decision 5: Make validation commands part of the docs contract

Each onboarding artifact should include a way to verify that it is still current, either by command, test, or file existence check. At minimum, the handbook should reference:

```bash
uv sync
uv run pytest ...
uv run ./agent-context mirror-lab-server ...
uv run ./agent-context resolve ...
uv run ./agent-context v1-stage-status ...
```

Alternative considered: leave validation to maintainers. Rejected because this project changes quickly and stale docs are one of the main failure modes.

## Risks / Trade-offs

- [Risk] The handbook becomes stale as commands change. -> Mitigation: include a doc maintenance checklist and test commands in `tasks.md`.
- [Risk] New developers mistake generated private data for required source. -> Mitigation: explicitly label generated directories and privacy boundaries in `DATA_CONTRACT.md`.
- [Risk] The handbook hides real quality gaps. -> Mitigation: require `KNOWN_GAPS.md` to include current V1 acceptance, Mirror personalization, retrieval quality, and media parsing status.
- [Risk] Too much architecture language delays implementation. -> Mitigation: `DEVELOPER_ONBOARDING.md` must include a first-hour path with exact commands.
- [Risk] Existing docs duplicate new docs. -> Mitigation: the new docs should route to existing deeper docs instead of copying them wholesale.

## Migration Plan

1. Add the seven onboarding documents under `docs/`.
2. Update `README.md` with a short "New Developer Start Here" entry.
3. Add a lightweight docs validation check that verifies required files and key headings exist.
4. Run targeted tests and OpenSpec validation/status after documentation is added.
5. Keep generated personal data out of git unless it is a sanitized fixture.

Rollback strategy: remove the new onboarding docs and README link. This change does not alter runtime behavior.

## Open Questions

- Should the handbook include screenshots of Mirror Lab, or only commands and artifact paths?
- Should docs validation be a standalone script, a pytest test, or both?
- Should public GitHub docs use `~/doctor-data` examples instead of `/Users/gengrf/...` paths?
- Should the current `docs/HANDOFF.md` be deprecated after the onboarding package lands?
