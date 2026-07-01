# Developer Onboarding

This is the shortest path for a new developer to understand and run Doctor/Mirror.

## Product Boundary

Doctor/Mirror is not a generic search page and not a replacement chat client.

```text
Doctor = local context compiler
Mirror = personal ranking and review layer
OKF / LLM-Wiki = long-term knowledge representation layer
MCP / CLI / Lab = delivery interfaces
```

Doctor turns local files, projects, sessions, workflows, generated indexes, and approved execution artifacts into bounded context packs that agents can read.

Mirror sits above Doctor. It helps review what Doctor selected and records preference signals so future context activation can become more personal.

OKF / LLM-Wiki is the long-term knowledge representation layer. It is where stable project, workflow, source, claim, contradiction, and entity pages can live after review.

MCP, CLI, Doctor Lab, Mirror Lab, and runtime review pages are interfaces into the same local runtime.

## First Hour

Start from the repository root:

```bash
cd /Users/gengrf/agent-context-system
uv sync
```

Run a fast sanity check:

```bash
uv run pytest tests/test_mirror_lab.py tests/test_context_resolver.py::test_mirror_ranker_feedback_affects_resolver_fusion -q
```

Open the interactive Mirror Lab:

```bash
uv run ./agent-context mirror-lab-server \
  --out /Users/gengrf/agent-context-system \
  --goal "我要去求职，请帮我看下我的电脑里适合简历包装的项目有哪些" \
  --mode fast \
  --host 127.0.0.1 \
  --port 8787
```

Open:

```text
http://127.0.0.1:8787/
```

Use `mirror-lab-server` for the real interactive path. The older `mirror-lab` command writes an offline HTML snapshot; a `file://` page cannot call the local Doctor backend directly.

Run a resolver pack without changing source files:

```bash
uv run ./agent-context resolve \
  --out /Users/gengrf/agent-context-system \
  --goal "我要去求职，请帮我看下我的电脑里适合简历包装的项目有哪些" \
  --source-scope all \
  --limit 8
```

The expected output is a pack under `packs/` containing:

```text
context.md
sources.jsonl
manifest.json
resolution_plan.json
```

## What To Read Next

For architecture:

- [ARCHITECTURE.md](ARCHITECTURE.md)
- [RUNTIME_FLOW.md](RUNTIME_FLOW.md)

For source ownership:

- [MODULE_MAP.md](MODULE_MAP.md)

For generated files and privacy boundaries:

- [DATA_CONTRACT.md](DATA_CONTRACT.md)

For honest maturity and current risks:

- [KNOWN_GAPS.md](KNOWN_GAPS.md)
- [ROADMAP.md](ROADMAP.md)

For deeper existing documents:

- [DOCTOR_RUNTIME_VM.md](DOCTOR_RUNTIME_VM.md)
- [DOCTOR_LAB.md](DOCTOR_LAB.md)
- [MIRROR_LAB_V0_2.md](MIRROR_LAB_V0_2.md)
- [MCP_SERVER.md](MCP_SERVER.md)
- [LLM_WIKI_OKF_BASELINE.md](LLM_WIKI_OKF_BASELINE.md)

## Developer Roles

Python/tooling developer:

- Start with `src/agent_context/cli.py`, `resolver.py`, `pack.py`, and `ingest.py`.
- Use `tests/test_downloads_context_pack.py`, `tests/test_context_resolver.py`, and `tests/test_lab.py` as behavior examples.

Frontend developer:

- Current UI is not React, Vue, Next.js, Tauri, or Electron.
- Start with `src/agent_context/mirror_lab.py` and `src/agent_context/runtime_review_server.py`.
- The first productization task is extracting or replacing Python-generated HTML with a real client shell.

MCP/client integration developer:

- Start with `src/agent_context/mcp_server.py`, `runtime_adapters.py`, `agent_preflight.py`, and `runtime_review_client.py`.
- Keep clients consuming file paths and metadata. Do not duplicate resolver internals inside clients.

Product reviewer:

- Start with [KNOWN_GAPS.md](KNOWN_GAPS.md) before assuming product maturity.
- The core KPI is context activation quality: whether Doctor/Mirror activates the right local evidence for a task.

## Safety Rules

- Do not mutate original user files during ingestion, indexing, resolving, or context packing.
- Treat `manifests/`, `indexes/`, `packs/`, `reports/`, `mirror_lab/`, `feedback/`, `runtime/`, and `vault/` as generated or review artifacts unless a file is explicitly documented as source.
- Do not commit private generated evidence unless it is sanitized or a fixture.
- Keep claims conservative until tests and reports prove the behavior.

