## ADDED Requirements

### Requirement: Doctor stores curated knowledge in an OKF-compatible vault
The system SHALL maintain a canonical LLM-Wiki/OKF vault made of UTF-8 Markdown files with YAML frontmatter, directory indexes, update logs, and concept pages.

#### Scenario: Vault baseline is initialized
- **WHEN** a user initializes or refreshes the Doctor knowledge vault
- **THEN** the system creates a vault root with `index.md` and `log.md`
- **AND** the vault root declares the supported concept families
- **AND** the vault root can be read with ordinary Markdown tools without running a database service

#### Scenario: Concept page is written
- **WHEN** Doctor writes an approved concept page
- **THEN** the page includes frontmatter with `type`, `title`, `description`, `timestamp`, stable concept id, and source provenance fields
- **AND** the page body uses structured Markdown sections for summary, evidence, links, limitations, and citations

### Requirement: Doctor preserves raw local files as read-only evidence
The system SHALL NOT modify original user files when compiling knowledge into the vault.

#### Scenario: Source file becomes knowledge
- **WHEN** Doctor compiles a raw PDF, document, code file, session transcript, workflow note, or media-derived transcript into the vault
- **THEN** the canonical source file remains unchanged
- **AND** the concept page references the source by path, content hash, extraction timestamp, and parser identity
- **AND** the concept page records whether the source was fully extracted, metadata-only, or partially extracted

### Requirement: Doctor supports first-class project, entity, workflow, source, claim, contradiction, and failure concepts
The system SHALL model durable knowledge with explicit concept types instead of only file chunks.

#### Scenario: Existing project evidence is compiled
- **WHEN** Doctor compiles local project evidence such as PLM, Drama, Codex++, Gugu, or Doctor
- **THEN** it can create or update a project concept page
- **AND** the project page links to relevant entity, workflow, source, claim, contradiction, and failure concepts
- **AND** the project page distinguishes user-confirmed facts from inferred facts

#### Scenario: A source claim is extracted
- **WHEN** Doctor extracts a factual or preference claim from a source
- **THEN** it records the claim as a claim concept or a cited section of another concept
- **AND** the claim includes source citation, confidence, freshness, and last-reviewed metadata

### Requirement: Doctor exposes progressive disclosure indexes
The system SHALL expose vault navigation through directory-level indexes rather than requiring a full-vault prompt.

#### Scenario: Agent browses the vault
- **WHEN** an agent or user opens the vault top-level `index.md`
- **THEN** it shows only directory-level summaries and links to child indexes or concept pages
- **AND** the agent can navigate one layer at a time without loading the entire vault

#### Scenario: Full-vault context is requested
- **WHEN** a user or client asks whether the whole vault can be placed into one model context
- **THEN** the system reports estimated token size
- **AND** the system recommends bounded concept selection when the estimate exceeds the configured context budget
