## ADDED Requirements

### Requirement: Doctor stages AI knowledge writes for review
The system SHALL stage AI-generated vault changes as reviewable diffs before they become canonical knowledge.

#### Scenario: AI compiles new knowledge
- **WHEN** Doctor uses an agent or model to compile source evidence into vault pages
- **THEN** it writes proposed pages and patches under a run-specific diff directory
- **AND** it does not update approved canonical vault pages until the diff is approved
- **AND** the diff records source files, prompts or tool inputs, generated pages, and expected index updates

#### Scenario: User rejects a knowledge diff
- **WHEN** the user rejects a proposed knowledge diff
- **THEN** Doctor records the rejection reason
- **AND** it does not apply the proposed canonical vault changes
- **AND** it can preserve the rejected route as failure-path memory if the user requests it

### Requirement: Doctor maintains stable entity identity
The system SHALL distinguish stable entity IDs from display names, aliases, and filenames.

#### Scenario: Entity is created
- **WHEN** Doctor creates an entity concept
- **THEN** it assigns a stable entity id
- **AND** it records aliases, display names, source citations, and disambiguation notes

#### Scenario: Ambiguous name is encountered
- **WHEN** Doctor sees an ambiguous name such as `Codex`, `Doctor`, `Mirror`, or `Gugu`
- **THEN** it resolves the name to an existing entity id when evidence is sufficient
- **AND** it creates a reviewable ambiguity or split request when evidence is insufficient

#### Scenario: User corrects an entity merge or split
- **WHEN** the user marks two concepts as incorrectly merged or split
- **THEN** Doctor records a merge/split correction
- **AND** the correction influences future entity resolution without rewriting historical source citations

### Requirement: Doctor treats contradictions as first-class knowledge
The system SHALL preserve important conflicts as contradiction concepts rather than silently overwriting old knowledge.

#### Scenario: Hard contradiction is detected
- **WHEN** a new proposed claim conflicts with an existing approved claim on a material fact
- **THEN** Doctor creates or updates a contradiction concept
- **AND** it records both sides, sources, severity, status, and required review action
- **AND** it blocks automatic approval of affected pages until the contradiction is resolved or explicitly accepted

#### Scenario: Soft contradiction is detected
- **WHEN** a new claim differs by scope, time, confidence, or interpretation rather than direct factual conflict
- **THEN** Doctor records the tension with citations
- **AND** it allows the knowledge diff to proceed only if the uncertainty is visible in the affected page

### Requirement: Doctor tracks drift, freshness, and failure paths
The system SHALL score knowledge age, source freshness, review status, and prior failed routes so stale or repeatedly rejected knowledge is not overused.

#### Scenario: Knowledge becomes stale
- **WHEN** a concept or claim passes its configured freshness window or points to a missing source
- **THEN** Doctor marks it stale or lower-confidence in derived index metadata
- **AND** resolver output exposes the freshness limitation instead of presenting the claim as current

#### Scenario: User rejects a route as a dead end
- **WHEN** the user marks a search route, source, concept, or action plan as a dead end
- **THEN** Doctor writes a failure concept with goal, attempted route, evidence, reason, and timestamp
- **AND** future retrieval can downrank or avoid that route for similar tasks

### Requirement: Doctor imports feedback without making it canonical truth
The system SHALL treat Mirror-style labels, arena choices, thumbs-up/down, and UI behavior as ranking signals rather than source facts.

#### Scenario: Feedback is imported
- **WHEN** Doctor imports feedback from Mirror, Arena, MCP feedback, or runtime review gates
- **THEN** it stores the feedback as a signal with task context, target, label, timestamp, and source
- **AND** it does not rewrite project or entity facts unless an approved knowledge diff cites evidence for the fact change
