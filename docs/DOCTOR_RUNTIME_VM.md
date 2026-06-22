# Doctor Runtime VM

Doctor's product boundary is a Docker-like local context runtime for macOS. It
does not virtualize processes or the file system like Docker. It virtualizes the
agent-facing view of the local machine: what the model should read, what the
user has approved, and what local artifacts were produced.

## Runtime Contract

Start a session with no Doctor index access:

```bash
doctor run \
  --out /Users/gengrf/agent-context-system \
  --session-id doctor-demo \
  --goal "我想比较我的 Codex 项目和一份 AI 应用实习生简历"
```

Inspect the session at any time:

```bash
doctor session \
  --out /Users/gengrf/agent-context-system \
  --session-id doctor-demo
```

The session entrypoint is:

```text
runtime/sessions/<session-id>/DOCTOR_SESSION.md
```

That file tells the user or an agent:

- current stage
- next review file
- next command
- whether the session is ready to advance
- where the model input, answer packet, and execution artifacts live

## Four Review Gates

```text
user task
  -> doctor run
  -> clarify/refine
  -> user reviews refined_prompt.md
  -> context-review generate
  -> user reviews model_input.md
  -> answer-review prepare/record
  -> user reviews answer.md
  -> execution-review prepare/run
  -> user reviews artifacts
```

Doctor only calls the resolver in the second gate. The first gate is deliberately
no-index so the user can inspect the normalized prompt before any local sources
are activated.

## Directory Layout

```text
runtime/sessions/<session-id>/
  DOCTOR_SESSION.md
  runtime_session.json
  runtime_session.md
  clarify.json
  refined_prompt.md
  context_review.json
  context_review.md
  context_review_events.jsonl
  answer_review.json
  answer_packet.md
  answer.md
  answer_review_events.jsonl
  execution_review.json
  execution_report.md
  execution_review_events.jsonl
  artifacts/
```

Generated context packs still live under:

```text
packs/<task-id>/
  context.md
  sources.jsonl
  manifest.json
  model_input.md
```

The runtime session stores pointers to those pack files instead of copying them.

## MCP Tools

The MCP server exposes:

- `doctor_run`: create a no-index runtime session
- `doctor_session`: inspect and refresh `DOCTOR_SESSION.md`
- existing resolver/search/read/build tools for the second stage

This keeps Doctor usable by Codex++, Warp, Claude, Cursor, or any other MCP
client without forcing every client to know the internal file layout.

## Current Boundary

Implemented:

- no-index session start
- session status inspection
- review-gated four-stage file contract
- CLI alias through `doctor`
- MCP session tools

Still outside this shell:

- real automatic model answering
- default Codex++/Warp interception for every task
- unified execution runtime beyond explicit reviewed commands
- full UI for approving each gate
