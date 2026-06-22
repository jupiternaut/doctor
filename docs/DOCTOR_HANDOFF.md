# Doctor Handoff

Last updated: 2026-06-22 Asia/Shanghai

## Product Definition

Doctor is a local-first context runtime for personal agents. It is not a chat
UI and not another generic RAG page. Its job is to virtualize a macOS working
environment so agent clients such as Codex++, Warp, OpenClaw, or future local
assistants can discover, mount, review, and execute task-specific context.

User-facing contract:

```text
raw user question
  -> normalize intent without retrieval
  -> resolve through Doctor cold/hot context
  -> answer through Codex++ / Warp / another client
  -> execute approved local programs
  -> capture feedback at each review gate
```

The existing implementation still exposes the low-level command as
`agent-context`. The project and product name are Doctor; a `doctor` command
alias is provided for the same CLI entrypoint.

## Current Implementation Snapshot

Evidence from the latest local reports:

| Area | Current evidence |
| --- | --- |
| Downloads ingestion | 997 documents, 10933 chunks, 3 failures |
| Provider layer | 111 projects, 300 sessions, 13 workflows |
| Cold indexes | Downloads 10933 rows, projects 24485 rows, sessions 4949 rows |
| Semantic background | 1050 chunks, LaunchAgent ok, 1/2 trend days observed |
| Hot context pack | Latest pack has 12 sources |
| MCP surface | Live stdio smoke passed, 39 tools exposed |
| Feedback loop | Replay health ok, latest expected top1 rate 0.833333 |
| Codex++ integration | Default hook, Manager smoke, model-input review, and answer-review handoff passed |
| Safety | Access policy has 17 deny path patterns |
| V1 acceptance | 8/10 stages ok; remaining items are time-gated semantic evidence |

Latest status files are generated locally under `reports/` and intentionally
not committed, because they include machine-specific paths and timestamps.

## Repository Shape

Important tracked surfaces:

```text
README.md
agent-context              # legacy wrapper
doctor                     # product-name wrapper
src/agent_context/         # runtime implementation
tests/                     # fixture and runtime tests
docs/                      # architecture, handoff, roadmap, research
config/access_policy.json  # safe default local access policy
scripts/                   # local maintenance and benchmark helpers
openspec/changes/doctor-runtime-macos-context/
```

Generated local data is intentionally ignored:

```text
extracted/
feedback/
indexes/
manifests/
packs/
queries/
reports/*
logs/
panel/status.json
panel/context_panel.html
```

## Validation Commands

Baseline local checks:

```bash
uv sync
uv run pytest -q
uv run ./doctor runtime-health --out /Users/gengrf/agent-context-system
uv run ./doctor mcp-live-smoke --out /Users/gengrf/agent-context-system
```

Representative context-pack flow:

```bash
uv run ./doctor build \
  --scope fixtures/downloads_sample \
  --goal "分析 Downloads 里哪些文件适合进入个人助手长期记忆" \
  --with-index
```

Representative resolver flow:

```bash
uv run ./doctor resolve \
  --goal "告诉我本地所有项目里如何构建个人推荐系统" \
  --source-scope gitProjects \
  --limit 8
```

V1 follow-up:

```bash
uv run ./doctor v1-refresh \
  --out /Users/gengrf/agent-context-system \
  --codex-plus-root /Users/gengrf/Code/research/CodexPlusPlus-BigPizzaV3 \
  --with-manager-feedback-smoke
```

## Runtime Baseline

- Doctor now has a stable `doctor run` four-stage runtime shell with no-index
  clarification, reviewable context generation, answer review, and execution
  review.
- Doctor now has a default `runtime-task` CLI/MCP entrypoint so Codex++,
  Warp, Codex CLI, or MCP clients can start a no-index prompt review and export
  the review launch contract with one call. `agent-preflight` is now the
  unified gate advancer for `clarify`, `context`, `handoff`, `answer`, and
  `execution`, so wrappers can stay on one high-level interface.
- Doctor can export a runtime adapter package for Codex++, Warp, Codex CLI, and
  MCP clients under `runtime/sessions/<session-id>/adapters/`.
- Doctor's review server now exposes `GET /api/session` and `POST /api/action`
  for native Codex++/Warp panels, in addition to the local HTML review page.
- Doctor can export an embeddable `review_client/` HTML/JS client and API
  contract for Codex++/Warp native panels or webviews.
- Doctor can export `review_launch.json/md` with the review server URL, API
  URLs, generated client path, and exact start/open commands.
- Codex++ now exposes the second review gate in its Context Panel: after
  `runtime-task` creates a `refined_prompt.md`, the panel can advance the same
  session through `agent-preflight --advance context`, persist
  `model_input.md`, and open that exact model payload for user review before
  anything is sent to a model.
- Codex++'s live injected task flow now also recognizes an explicit user
  approval such as generating `model_input` context, advances the active Doctor
  session through `/agent-context/model-input-review`, and appends only the
  reviewable `model_input.md`/`context.md`/`sources.jsonl` paths back to the
  turn with a "do not answer until user review" instruction.
- Codex++'s live injected task flow now recognizes a second explicit approval
  such as approving `model_input` for answering. It calls
  `/agent-context/answer-review`, which approves Doctor's context gate, advances
  the active session through `agent-preflight --advance answer`, and appends the
  approved `model_input.md`, `agent_handoff.md`, and `answer_packet.md` paths to
  the turn. This is the first live path where Codex++ can show exactly what it is
  about to feed a model after the user has reviewed Doctor's context.
- Stage 4 now writes a unified `execution_artifacts.jsonl` and
  `execution_artifacts.md` with artifact paths, sizes, media types, and hashes.
- A synthetic end-to-end runtime session
  `session-agent-preflight-e2e-20260622` reached `complete` through
  `runtime-task -> agent-preflight context -> context approval ->
  agent-preflight answer -> answer approval -> agent-preflight execution ->
  execution approval`; `runtime-acceptance` reported `ready=true` and 15/15
  required checks ok.

## Known Gaps

- Warp interception for every task is still not wired as a native runtime
  entrypoint, and Codex++ still needs native execution/review controls embedded
  in the live task flow.
- User-facing UI is still weaker than OpenClaw, ChatGPT Projects, and Claude
  Code.
- Metadata model is not yet as mature as DataHub or OpenMetadata.
- OCR, audio/video transcription, complex archive expansion, and trained rerank
  models remain future work.
- V1 acceptance still requires multi-day semantic background evidence.

## Next Owner Instructions

1. Treat the current V1 implementation as the baseline. Do not widen scope
   until the stage-status or acceptance report is refreshed.
2. Implement Doctor as a thin runtime contract over existing lower-level
   commands before adding new parsing or model-heavy retrieval.
3. Keep all generated private data ignored by default.
4. Preserve the access-policy boundary before adding local execution.
5. Use the OpenSpec plan in `openspec/changes/doctor-runtime-macos-context/`
   as the implementation backlog.
