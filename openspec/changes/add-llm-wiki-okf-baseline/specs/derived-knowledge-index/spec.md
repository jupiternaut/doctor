## ADDED Requirements

### Requirement: Doctor builds disposable indexes from the canonical vault
The system SHALL build search, vector, graph, and route-prior indexes from approved vault Markdown pages as rebuildable derived state.

#### Scenario: Knowledge index is built
- **WHEN** Doctor builds the derived knowledge index
- **THEN** it reads approved vault pages
- **AND** it writes machine indexes such as SQLite/FTS, vector metadata, graph edges, and route priors outside the canonical vault
- **AND** it records the source vault path, content hash, parser version, index schema version, and embedding model identity for each indexed unit

#### Scenario: Derived index is deleted
- **WHEN** a derived index is deleted or corrupted
- **THEN** Doctor can rebuild it from approved vault Markdown pages
- **AND** no canonical knowledge is lost

### Requirement: Doctor supports hybrid retrieval over the vault
The system SHALL support lexical, semantic, graph, and feedback-aware retrieval over vault concepts.

#### Scenario: User asks a knowledge question
- **WHEN** Doctor receives an approved task for context resolution
- **THEN** it can query the derived knowledge index using exact text, tags, aliases, entity ids, vector similarity, graph neighbors, and feedback priors
- **AND** it returns bounded concepts with source paths, citations, freshness, confidence, and score parts

#### Scenario: Exact term routing is strong
- **WHEN** a query contains file names, paths, project names, entity aliases, error messages, or workflow IDs
- **THEN** lexical and identifier matches are preserved as first-class ranking features
- **AND** semantic retrieval does not hide exact evidence matches

### Requirement: Doctor keeps derived indexes aligned with vault content
The system SHALL detect stale or inconsistent derived index records and provide local repair paths.

#### Scenario: Vault page changes
- **WHEN** an approved vault Markdown page changes
- **THEN** Doctor detects the content-hash change
- **AND** it updates the affected lexical, vector, graph, and route-prior entries without requiring a full rebuild when possible

#### Scenario: Embedding model changes
- **WHEN** the configured embedding model identity or vector dimensions change
- **THEN** Doctor marks incompatible vector rows stale
- **AND** it refuses to mix old and new vector spaces in the same active semantic index

#### Scenario: Index cannot be trusted
- **WHEN** Doctor detects missing rows, stale hashes, incompatible models, or unresolved deletion drift
- **THEN** it reports the integrity issue
- **AND** it offers a rebuild command or fallback retrieval path from the canonical vault

### Requirement: Doctor produces baseline retrieval reports
The system SHALL measure whether the vault/index baseline is better than raw search and existing context packs for selected tasks.

#### Scenario: Baseline evaluation runs
- **WHEN** a user runs the LLM-Wiki/OKF baseline evaluation
- **THEN** Doctor compares raw file search, existing context packs, vault retrieval, and full-vault context-size estimates
- **AND** it writes a report with result paths, token estimates, top concepts, evidence gaps, and whether each route is suitable for model input
