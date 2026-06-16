## 1. Baseline And Naming

- [ ] 1.1 Run the current V1 refresh or stage-status command and record the latest evidence paths before changing Doctor behavior.
- [ ] 1.2 Decide whether the public command name is `doctor` or `agent-context doctor`, and document the temporary alias policy.
- [ ] 1.3 Add a short Doctor Runtime overview document that explains the Docker-like abstraction in user-facing language.
- [ ] 1.4 Add a run directory contract for `doctor/runs/<run-id>/` and list every required artifact file.

## 2. Doctor Run State

- [ ] 2.1 Implement a Doctor run model with run id, phase status, goal, client, timestamps, artifact paths, and current review gate.
- [ ] 2.2 Persist each run as local files without mutating original source files or existing indexes.
- [ ] 2.3 Add a read-only run summary command that reports completed phases, pending phases, evidence paths, and next allowed action.
- [ ] 2.4 Add tests proving an interrupted run can be resumed from the saved run directory.

## 3. Normalize Phase

- [ ] 3.1 Add a normalization command that writes `normalized_task.md` from a raw user message.
- [ ] 3.2 Ensure normalization does not query indexes, read source files, or inspect hot context packs.
- [ ] 3.3 Include goal, assumptions, constraints, acceptance criteria, and unresolved questions in `normalized_task.md`.
- [ ] 3.4 Add a review command or status field that marks the normalized task as approved, rejected, or replaced.
- [ ] 3.5 Add tests proving rejected normalization blocks resolve until corrected input is approved or provided.

## 4. Resolve And Mount Phase

- [ ] 4.1 Add a Doctor resolve command that consumes an approved normalized task and calls the existing resolver.
- [ ] 4.2 Write the resolved pack under the Doctor run directory while preserving existing pack outputs.
- [ ] 4.3 Include `context.md`, `sources.jsonl`, `manifest.json`, and `resolution_plan.json` in the mounted context contract.
- [ ] 4.4 Add a no-evidence path that records searched providers and evidence gaps instead of fabricating local evidence.
- [ ] 4.5 Add tests proving resolved sources are traceable to cold-index records or provider cards.

## 5. Answer Phase And Client Integration

- [ ] 5.1 Add a Doctor answer artifact contract that records the client-generated answer or candidate routes.
- [ ] 5.2 Update Codex++ integration to call Doctor phase commands instead of directly calling resolver/preflight when Doctor mode is enabled.
- [ ] 5.3 Add a client-neutral MCP surface for normalize, resolve, answer feedback, run summary, and phase status.
- [ ] 5.4 Keep existing low-level `agent-context` commands available for debugging and backwards compatibility.
- [ ] 5.5 Add smoke tests proving Codex++ can receive Doctor pack paths without embedding resolver internals.

## 6. Execute Phase And Safety

- [ ] 6.1 Add an execution plan artifact that lists proposed commands, working directories, expected outputs, and risk flags.
- [ ] 6.2 Require explicit approval before running any write, app launch, network, or shell execution step.
- [ ] 6.3 Execute only approved steps and write `execution_log.jsonl` with command, cwd, exit status, stdout/stderr summary, and produced artifact paths.
- [ ] 6.4 Apply existing access policy to execution inputs and outputs, including denied paths and require-consent paths.
- [ ] 6.5 Add tests proving denied execution steps are blocked and audited.

## 7. Feedback And Learning

- [ ] 7.1 Record structured feedback for normalization review, context/answer review, and execution review.
- [ ] 7.2 Compile feedback into separate intent, source-route, and execution-quality signals without rewriting raw feedback logs.
- [ ] 7.3 Feed context/answer review signals into the existing resolver prior or route selector model.
- [ ] 7.4 Add replay cases showing that rejected sources or routes are avoided in alternative resolutions.
- [ ] 7.5 Add a report that separates failure cause into intent, context, answer, or execution.

## 8. Verification And Release

- [ ] 8.1 Add unit tests for Doctor run state, normalize, resolve, feedback, and safety behavior.
- [ ] 8.2 Add an end-to-end fixture test for `normalize -> approve -> resolve -> answer record -> execution plan -> feedback`.
- [ ] 8.3 Run the existing `uv run pytest -q` suite and record the result.
- [ ] 8.4 Run a local realistic test with the goal `开源往事如何在番茄爆火，面向的读者是谁` and inspect the generated Doctor run artifacts.
- [ ] 8.5 Run V1 acceptance or stage-status after Doctor changes and confirm existing context runtime behavior did not regress.
- [ ] 8.6 Update handoff documentation with command examples, artifact paths, known limitations, and next follow-up.
- [ ] 8.7 Verify the OpenSpec change status is complete and ready for implementation archive after all tasks pass.
