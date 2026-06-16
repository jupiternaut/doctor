## Why

The current agent-context work can index local files and generate context packs, but its product boundary is still tool-shaped: users must know when to invoke resolver, indexes, MCP tools, or Codex++ hooks. The next step is to package it as a Docker-like local Doctor Runtime that virtualizes the macOS file system for agent work while preserving explicit user review gates.

This solves the real user problem: a person with many local files, projects, sessions, and workflows wants to talk to Codex++ or Warp naturally, have the system find and mount the right local context, and then safely execute local programs to produce useful artifacts.

## What Changes

- Introduce a four-stage Doctor workflow:
  - Normalize the user request into a clear task prompt without touching Doctor indexes.
  - Resolve the normalized task through Doctor cold/hot context layers and produce a context pack.
  - Feed the context pack to Codex++/Warp or a Doctor-hosted agent surface for answer generation and user review.
  - Execute approved local commands, Python scripts, workflows, or app actions with permission gates and artifact capture.
- Define Doctor as a macOS context virtualization runtime, not a chat UI or generic RAG page.
- Add explicit user review gates after normalization, answer/context generation, and local execution.
- Standardize runtime artifacts: `normalized_task.md`, `context.md`, `sources.jsonl`, `manifest.json`, `execution_plan.md`, run logs, produced artifacts, and feedback records.
- Keep existing cold indexes, hot context packs, MCP tools, semantic refresh, Codex++ panel, and feedback loop as implementation components under the new runtime contract.
- Non-goals:
  - Do not replace Codex++ or Warp as user-facing agent clients in the first slice.
  - Do not attempt full-disk OCR/audio/video/zip expansion before the runtime contract is stable.
  - Do not make unsafe local execution automatic; execution remains review-gated.

## Capabilities

### New Capabilities
- `doctor-runtime-pipeline`: Defines the staged user interaction model from natural-language request to normalized task, resolved context, agent answer, approved execution, and feedback.
- `macos-context-virtualization`: Defines the Docker-like Doctor abstraction over local files, indexes, providers, mounted context packs, execution surfaces, permissions, and audit logs.

### Modified Capabilities

None.

## Impact

- Affected local project: `/Users/gengrf/agent-context-system`
- Affected integration surface: `/Users/gengrf/Code/research/CodexPlusPlus-BigPizzaV3`
- Affected user workflows: Codex++/Warp-style chat, resolver/MCP usage, local command execution, artifact review, and feedback capture
- Affected artifacts:
  - CLI commands around `doctor normalize`, `doctor resolve`, `doctor answer`, `doctor execute`, and `doctor feedback`
  - Existing `agent-context` commands may remain as lower-level implementation commands or aliases
  - Doctor runtime reports, packs, execution logs, and acceptance reports
- Dependencies:
  - Existing SQLite/FTS/hash-vector/semantic indexes
  - Existing MCP server and Codex++ integration
  - Existing access policy and audit infrastructure
  - Future optional ANN/rerank improvements after the runtime contract is accepted
