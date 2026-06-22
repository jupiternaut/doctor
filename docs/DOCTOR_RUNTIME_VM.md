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

Write the acceptance handoff for the current session:

```bash
doctor runtime-acceptance \
  --out /Users/gengrf/agent-context-system \
  --session-id doctor-demo
```

Export the approved context for Codex++, Warp, or Doctor:

```bash
doctor runtime-handoff \
  --out /Users/gengrf/agent-context-system \
  --session-id doctor-demo
```

Open a clickable localhost review page:

```bash
doctor runtime-review-server \
  --out /Users/gengrf/agent-context-system \
  --session-id doctor-demo \
  --port 8765
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
  -> runtime-handoff exports approved model input for Codex++/Warp/Doctor
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
  agent_handoff.json
  agent_handoff.md
  answer_review.json
  answer_packet.md
  answer.md
  answer_review_events.jsonl
  execution_review.json
  execution_report.md
  execution_artifacts.jsonl
  execution_artifacts.md
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
- `doctor_runtime_acceptance`: write the session acceptance handoff
- `doctor_runtime_handoff`: export approved `model_input.md` for Codex++, Warp, or Doctor
- `doctor_context_review`: generate, regenerate, approve, or reject `model_input.md`
- `doctor_answer_review`: prepare, record, approve, or reject the answer packet
- `doctor_execution_review`: prepare, run, record, approve, or reject local artifacts
- existing resolver/search/read/build tools for the second stage

This keeps Doctor usable by Codex++, Warp, Claude, Cursor, or any other MCP
client without forcing every client to know the internal file layout.

## Acceptance Handoff

`runtime-acceptance` reads the current session files and writes:

```text
reports/runtime-vm-acceptance-<session-id>-<timestamp>.json
reports/runtime-vm-acceptance-<session-id>-<timestamp>.md
reports/runtime-vm-acceptance-latest.json
reports/runtime-vm-acceptance-latest.md
```

The report checks:

- `DOCTOR_SESSION.md` and `runtime_session.json` exist
- stage 1 has `doctor_access=false`, `resolver_called=false`, and `index_access=false`
- stage 2 has generated and approved `model_input.md`
- the approved context has an `agent_handoff.md` bridge for external agents
- stage 3 has recorded and approved an answer
- stage 4 has run or recorded an artifact and approved the execution output
- stage 4 has indexed produced files in `execution_artifacts.jsonl`
- MCP exposes the runtime tools needed by external agents

If a session is waiting for user review, the report stays incomplete and records
the exact next command instead of pretending the runtime is accepted.

## Panel Status Contract

`doctor panel --no-auto-context` reads `runtime-vm-acceptance-latest.json` and
adds a `runtime_vm` object to:

```text
panel/status.json
panel/context_panel.html
```

The object includes:

- `status`
- `ready`
- `session_id`
- `review_file`
- `agent_handoff_md_path`
- `next_message`
- `next_commands`
- `missing_required`
- `latest_md_path`

Codex++, Warp, or another shell can render this object without learning the
internal session layout. A user can see the current review gate and then run the
listed approve/reject command.

## Clickable Review Server

`runtime-review-server` binds to `127.0.0.1` by default and renders the current
session gate in a browser. It shows:

- current status
- active review file preview
- stage table
- missing acceptance checks
- approve/reject or prepare/record/run buttons for the current gate
- export the approved context handoff after context review passes

The server calls the same stage functions as the CLI:

- `context-review` for context approve/reject
- `answer-review` for answer prepare/record/approve/reject
- `execution-review` for execution prepare/run/approve/reject

It refreshes `DOCTOR_SESSION.md` and `runtime-vm-acceptance-latest.*` after each
button press. The server does not expose arbitrary file reads; the preview is
limited to files under the Doctor output root.

## Current Boundary

Implemented:

- no-index session start
- session status inspection
- review-gated four-stage file contract
- CLI alias through `doctor`
- MCP tools for all four review gates
- approved-context handoff export for Codex++/Warp/Doctor
- acceptance handoff reports
- `panel/status.json` runtime VM status for UI clients
- localhost clickable review server for the current gate
- unified execution artifact manifest/index for command outputs and recorded files

Still outside this shell:

- real automatic model answering
- default Codex++/Warp interception for every task
- richer execution adapters beyond explicit reviewed commands and recorded artifacts
- embedded Codex++/Warp native UI for approving each gate
