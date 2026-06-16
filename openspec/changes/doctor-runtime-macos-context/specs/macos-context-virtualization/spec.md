## ADDED Requirements

### Requirement: Doctor models local data as providers instead of raw full-context injection
The system SHALL represent local macOS files, projects, sessions, workflows, and generated artifacts as providers with metadata, permissions, freshness, and index status.

#### Scenario: Provider registry is refreshed
- **WHEN** the Doctor provider registry refreshes
- **THEN** each provider entry records its source family, scope, permissions, last indexed time, index location, strengths, and known limitations
- **AND** the registry does not require converting the whole disk into a single Markdown file or hot context package

### Requirement: Doctor exposes hot context packs as mounted working sets
The system SHALL treat each generated hot context pack as a mounted working set for one task rather than as a permanent global memory dump.

#### Scenario: A task pack is mounted for an agent
- **WHEN** a client asks Doctor to mount context for a task
- **THEN** the system exposes the task's `context.md`, `sources.jsonl`, `manifest.json`, and `resolution_plan.json`
- **AND** the mounted pack is limited by task relevance and context budget
- **AND** the pack remains traceable back to cold-index records and source paths

### Requirement: Doctor exposes a client-neutral interface
The system SHALL expose Doctor phases through stable CLI and MCP contracts so Codex++, Warp, and other agent clients can integrate without embedding resolver internals.

#### Scenario: Codex++ calls Doctor
- **WHEN** Codex++ needs task context
- **THEN** it calls a Doctor phase command or MCP tool
- **AND** it receives file paths and structured metadata instead of duplicating provider selection or retrieval logic

#### Scenario: Warp calls Doctor
- **WHEN** Warp needs task context
- **THEN** it can use the same Doctor phase contract as Codex++
- **AND** Doctor preserves the same artifact layout and feedback schema

### Requirement: Doctor enforces local access policy during read and execution phases
The system SHALL apply access policy and audit logging to both source reads and execution actions.

#### Scenario: Source path is denied by policy
- **WHEN** a context pack or read request targets a denied provider or path pattern
- **THEN** the system excludes or blocks that source
- **AND** the system writes an audit event explaining the block

#### Scenario: Execution touches local files
- **WHEN** an approved execution step reads or writes local files
- **THEN** the system records the paths it accessed when detectable
- **AND** the system marks unexpected writes as warnings or failures in the run summary

### Requirement: Doctor provides runtime health and resume state
The system SHALL expose runtime health, latest gates, generated artifacts, and next actions so a user or agent can resume a task without reconstructing state from chat history.

#### Scenario: Doctor run is interrupted
- **WHEN** a Doctor run stops after normalization, resolution, answer generation, or execution
- **THEN** the system preserves the current run directory
- **AND** the run summary identifies completed phases, pending phases, evidence paths, and the next allowed action

#### Scenario: Runtime is not ready
- **WHEN** background semantic evidence, MCP smoke, provider indexes, or access policy checks are not ready
- **THEN** the system reports the blocking or waiting condition
- **AND** the system avoids presenting the runtime as fully accepted until the relevant gate passes
