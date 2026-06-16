## ADDED Requirements

### Requirement: Doctor normalizes user intent before retrieval
The system SHALL provide a normalization phase that converts a raw user message into a clear task prompt, assumptions, constraints, and acceptance criteria without accessing Doctor indexes or local source contents.

#### Scenario: User submits a raw question
- **WHEN** a client sends a raw user question to the Doctor normalization phase
- **THEN** the system writes `normalized_task.md`
- **AND** the normalized task includes the clarified goal, assumptions, constraints, and proposed acceptance criteria
- **AND** the phase does not query cold indexes, read hot packs, or inspect local source files

#### Scenario: User rejects normalized intent
- **WHEN** the user marks the normalized task as incorrect
- **THEN** the system records intent feedback
- **AND** the system does not run context resolution until a corrected normalized task is approved or provided

### Requirement: Doctor resolves approved tasks into mounted context packs
The system SHALL resolve an approved normalized task through configured local providers and produce a bounded hot context pack with provenance and an explainable resolution plan.

#### Scenario: Approved task is resolved
- **WHEN** the user approves or provides a normalized task
- **THEN** the system selects relevant providers
- **AND** the system queries cold indexes and semantic indexes according to the selected provider scope
- **AND** the system writes `context.md`, `sources.jsonl`, `manifest.json`, and `resolution_plan.json`
- **AND** the context pack includes paths, summaries, short evidence snippets, limitations, and recommended next actions

#### Scenario: No good sources are found
- **WHEN** resolution cannot find enough relevant local sources
- **THEN** the system writes a context pack that states the evidence gap
- **AND** the system records which providers were searched
- **AND** the system recommends the next useful action instead of fabricating local evidence

### Requirement: Doctor separates answer generation from execution
The system SHALL let a client generate an answer from the mounted context pack before any local execution runs.

#### Scenario: Client generates an answer from context
- **WHEN** Codex++, Warp, or another client receives the context pack
- **THEN** it can generate an answer or candidate routes using the mounted context
- **AND** the answer output cites selected sources from `sources.jsonl`
- **AND** the system records the answer artifact for user review

#### Scenario: User rejects the answer route
- **WHEN** the user marks the answer or selected route as wrong
- **THEN** the system records answer feedback
- **AND** the system can request an alternative route without reusing explicitly rejected sources unless the user allows it

### Requirement: Doctor executes only after user approval
The system SHALL execute local commands, Python scripts, workflows, or app actions only after the user approves an execution plan derived from the accepted answer route.

#### Scenario: User approves local execution
- **WHEN** the user approves an execution plan
- **THEN** the system runs only the approved actions
- **AND** the system writes an execution log with command, working directory, exit status, touched outputs, and errors
- **AND** the system records produced artifacts in the Doctor run directory or declared output paths

#### Scenario: Execution requires risky access
- **WHEN** an execution plan would write outside approved output locations, access denied paths, run networked commands, or launch local apps
- **THEN** the system requests explicit permission before running that step
- **AND** the system records the permission decision in the audit log

### Requirement: Doctor captures feedback at every review gate
The system SHALL record structured feedback for normalization review, context/answer review, and execution review so later resolver and route selection can improve.

#### Scenario: User completes a Doctor run
- **WHEN** the user gives final feedback after execution
- **THEN** the system writes feedback records for the review gates that occurred
- **AND** the records identify whether the failure or success belonged to intent, context, answer, or execution
- **AND** the feedback can be compiled into ranking or route-selection priors without rewriting raw feedback logs
