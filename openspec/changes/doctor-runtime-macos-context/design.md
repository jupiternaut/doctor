## Context

`agent-context-system` already has most of the lower-level machinery: file ingestion, provider discovery, SQLite/FTS indexes, semantic background refresh, context packs, MCP tools, Codex++ panel/hook work, access policy, and feedback reports. The missing product boundary is a stable user-facing runtime contract.

The target product shape is Doctor: a Docker-like local component for macOS context. Doctor virtualizes local files, projects, sessions, workflows, indexes, and executable tools so Codex++ or Warp can mount the right task context and execute approved local actions without making the user manually choose indexes or commands.

The desired interaction has four user-visible stages:

1. The user asks a natural-language question in Codex++ or Warp.
2. The model normalizes the request into a better prompt without accessing Doctor.
3. Doctor resolves that normalized prompt through cold indexes and hot context packs.
4. Codex++/Warp answers with mounted context, then approved answers can execute local programs and produce artifacts.

Each review gate is a product feature, not friction:

- Review 1: confirm the task was understood.
- Review 2: confirm the answer/context route is acceptable.
- Review 3: confirm local execution produced useful output.

## Goals / Non-Goals

**Goals:**

- Define Doctor as a runtime contract with stable phases: `normalize`, `resolve`, `answer`, `execute`, and `feedback`.
- Preserve Codex++ and Warp as possible clients instead of coupling the product to one UI.
- Treat cold indexes and hot packs as internal runtime layers, not the user-facing abstraction.
- Make the first implementation useful with existing local assets before adding more parsers or model-heavy rerankers.
- Keep every phase resumable and auditable through local files.
- Keep local execution permission-gated and artifact-oriented.

**Non-Goals:**

- Do not replace Codex++ or Warp in the first implementation.
- Do not build a generic "chat with documents" web UI.
- Do not require all local files to be converted into Markdown KV before the runtime is usable.
- Do not make local command execution automatic without user approval.
- Do not block this runtime contract on OCR, audio transcription, full archive expansion, or a perfect ANN backend.

## Decisions

### Decision 1: Doctor is a runtime contract, not a new chat app

Doctor should expose stable commands and artifacts that clients can call:

```text
doctor normalize
doctor resolve
doctor answer
doctor execute
doctor feedback
```

Codex++ and Warp remain clients. They can render the review gates and call Doctor through CLI/MCP/bridge hooks. This avoids binding the architecture to one frontend.

Alternative considered: build a Doctor-native chat UI first. Rejected because it would duplicate Codex++/Warp before the runtime contract is stable.

### Decision 2: Normalization must not access Doctor indexes

The first model pass only turns the user's raw message into a clear task prompt, assumptions, constraints, and acceptance criteria. It does not retrieve local documents. This makes the first user review about intent, not evidence.

Alternative considered: immediately retrieve context from the raw user message. Rejected because it mixes intent clarification with evidence selection and makes bad retrieval look like bad understanding.

### Decision 3: Resolve mounts a hot context pack instead of exposing raw search results

Doctor should convert cold-index hits into a bounded hot pack:

```text
context.md
sources.jsonl
manifest.json
resolution_plan.json
```

The agent reads the pack as a mounted working set. Users and tools can still inspect `sources.jsonl` for provenance.

Alternative considered: return ranked search hits directly to the agent. Rejected because agents need task-shaped context, limits, and next actions, not only "similar files".

### Decision 4: Execution is a separate phase after answer review

The answer phase can propose commands or scripts, but execution happens only after the user accepts the route. Execution creates `execution_plan.md`, logs, and produced artifacts.

Alternative considered: let the agent execute during answer generation. Rejected because local filesystem and app execution need a stronger permission and audit boundary.

### Decision 5: Feedback is captured at all three review gates

Doctor records:

- intent feedback from normalization review;
- context/answer feedback from answer review;
- artifact feedback from execution review.

This feedback should later feed resolver routing, source priors, answer mode selection, and execution-plan quality checks.

Alternative considered: only record final thumbs-up/down. Rejected because failures in this workflow have different causes: misunderstood intent, bad context, bad answer, or bad execution.

## Risks / Trade-offs

- [Risk] The runtime wrapper may add process overhead before value is visible. -> Mitigation: implement the first slice as file-backed CLI phases that reuse existing `agent-context` commands.
- [Risk] Users may confuse Doctor with Codex++ if both have UI controls. -> Mitigation: label Doctor as "local context runtime" and keep Codex++/Warp as client surfaces.
- [Risk] Normalization can over-constrain the user request. -> Mitigation: show `normalized_task.md` for review and allow editing before resolve.
- [Risk] Resolver may still select weak sources. -> Mitigation: keep `resolution_plan.json`, score parts, and feedback logs visible; improve rerank later with labeled cases.
- [Risk] Local execution can mutate files unexpectedly. -> Mitigation: default to dry-run/plan, require explicit approval for writes, and log all commands and touched paths.
- [Risk] Adding too many capabilities before V1 acceptance will destabilize current work. -> Mitigation: freeze current V1 acceptance path, then implement Doctor as a thin orchestration layer on top.

## Migration Plan

1. Finish or snapshot current V1 acceptance evidence so the existing system has a clean baseline.
2. Add Doctor artifact directories under the existing output root:
   - `doctor/runs/<run-id>/normalized_task.md`
   - `doctor/runs/<run-id>/context/`
   - `doctor/runs/<run-id>/answer.md`
   - `doctor/runs/<run-id>/execution_plan.md`
   - `doctor/runs/<run-id>/execution_log.jsonl`
   - `doctor/runs/<run-id>/feedback.jsonl`
3. Add CLI commands as aliases or wrappers around existing lower-level commands.
4. Add MCP tools for each Doctor phase after CLI behavior is stable.
5. Update Codex++ panel/hook to call Doctor phases instead of directly calling resolver/preflight.
6. Keep existing `agent-context` commands available for low-level debugging.

Rollback strategy: because Doctor phases write additive run directories and call existing lower-level commands, rollback is deleting or disabling the Doctor wrapper while preserving indexes, packs, and reports.

## Open Questions

- Should the public command name be `doctor` or remain `agent-context doctor` until product naming is final?
- Should the answer phase call a model directly, or should it only prepare payloads for Codex++/Warp to submit?
- Which execution actions require hard confirmation: writes, network, app launch, shell commands, long-running jobs, or all of them?
- Should a Doctor run be one directory per user task or one directory per conversation turn?
