# Data Contract

Doctor is file-backed. Most state is stored as Markdown, JSONL, JSON, and SQLite under the output root.

## Source Versus Generated Data

Original local files are inputs. Doctor ingestion, indexing, resolving, and context packing must not modify them.

Generated local data may contain private paths, text snippets, project names, session content, and review decisions. Do not publish generated data unless it is sanitized or fixture-only.

## Important Directories

| Path | Type | Purpose | Public handoff guidance |
|---|---|---|---|
| `src/agent_context/` | Source | Python implementation | Commit |
| `tests/` | Source | Pytest coverage and fixtures | Commit |
| `docs/` | Source/docs | Human documentation | Commit after reviewing private paths |
| `openspec/changes/` | Planning | OpenSpec proposals/design/specs/tasks | Commit when relevant |
| `fixtures/` | Source/test data | Sanitized sample data | Commit |
| `config/` | Mixed | Policies and sample config | Commit sanitized examples only |
| `manifests/` | Generated | JSONL source and chunk inventories | Usually local/private |
| `extracted/` | Generated | Markdown extracted from source files | Usually local/private |
| `indexes/` | Generated | SQLite / semantic indexes | Usually local/private |
| `packs/` | Generated | Hot context packs per task | Usually local/private |
| `feedback/` | Generated | Feedback logs and learned priors | Usually local/private |
| `reports/` | Generated | Health, smoke, acceptance, eval reports | Usually local/private unless sanitized |
| `mirror_lab/` | Generated | Static Mirror Lab page and state snapshot | Usually local/private |
| `runtime/` | Generated | Doctor runtime sessions and review files | Usually local/private |
| `vault/` | Mixed/generated | OKF / LLM-Wiki pages and diffs | Review before publishing |
| `lab/` | Generated | Doctor Lab runs and attachments | Local/private |
| `providers/` | Generated | Provider-specific bridge state | Local/private |
| `catalog-shards/` | Generated | Whole-machine catalog shards | Local/private |

## Manifests

Common files:

```text
manifests/documents.jsonl
manifests/chunks.jsonl
manifests/failures.jsonl
manifests/projects.jsonl
manifests/sessions.jsonl
manifests/workflows.jsonl
```

Expected contract:

- JSONL, one object per line.
- Records include path, hash or identifier, parser/index status, and enough metadata to recover provenance.
- Failures are recorded instead of being silently dropped.
- Archive/package files can be metadata-only and should not be expanded unless a specific provider implements that behavior.

## Extracted Markdown

`extracted/*.md` contains best-effort Markdown generated from documents or providers.

Contract:

- Original files remain unchanged.
- Extraction can fail; failures belong in `manifests/failures.jsonl`.
- Extracted content can contain private user data and should not be public by default.

## Indexes

Common files:

```text
indexes/context.sqlite
indexes/projects.sqlite
indexes/sessions.sqlite
indexes/semantic.sqlite
indexes/vault.sqlite
indexes/knowledge.sqlite
```

Contract:

- SQLite indexes are derived from manifests, extracted Markdown, provider records, and vault pages.
- They can be rebuilt.
- Do not treat index files as canonical source of truth.
- Search quality and ranking can change without changing raw data.

## Hot Context Packs

Each task pack lives under `packs/<pack-id>/`.

Required files:

```text
context.md
sources.jsonl
manifest.json
resolution_plan.json
```

Contract:

- `context.md` is the model-readable bounded working set.
- `sources.jsonl` is the provenance trail.
- `manifest.json` records generation metadata, goal, mode, and pack properties.
- `resolution_plan.json` explains provider routing and scoring decisions when available.
- A pack is task-scoped. It is not a full memory dump.

## Feedback

Common files:

```text
feedback/*.jsonl
feedback/model.json
feedback/route_selector_model.json
```

Contract:

- Raw feedback is append-only.
- Compiled models are derived artifacts.
- User choices should influence future ranking only through auditable model or prior files.

## Vault

`vault/` stores OKF / LLM-Wiki style long-term knowledge artifacts.

Contract:

- Approved concept pages should include source provenance.
- AI-generated or inferred changes should pass through diffs/review before becoming canonical.
- Vault pages are a knowledge representation layer, not a replacement for raw provenance.

## Runtime Sessions

`runtime/sessions/<session-id>/` stores Doctor runtime artifacts.

Typical files:

```text
DOCTOR_SESSION.md
runtime_session.json
runtime_session.md
refined_prompt.md
model_input.md
review_client/
```

Contract:

- Runtime sessions are resumable.
- Review gates should identify the next allowed action.
- Client integrations should read generated files rather than reimplementing resolver logic.

## Privacy Boundary

Before public handoff:

- Remove or sanitize local absolute paths.
- Remove raw extracted text from personal documents.
- Remove private session transcripts.
- Remove personal feedback logs unless deliberately anonymized.
- Prefer `fixtures/` and docs examples over generated local evidence.

