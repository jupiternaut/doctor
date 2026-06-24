# LLM-Wiki / OKF Vault Baseline

Doctor now has a first-pass long-term knowledge layer above raw files and below
the resolver.

```text
raw files (read-only)
  -> evidence files with SHA-256 hashes
  -> Brain Diff under vault/diffs/
  -> human approval
  -> canonical concept pages under vault/
```

## Directory Contract

```text
vault/
  index.md
  log.md
  approvals.jsonl
  rejections.jsonl
  projects/
  entities/
  workflows/
  claims/
  contradictions/
  failures/
  sources/
  templates/
  diffs/<diff-id>/
    DIFF_SUMMARY.md
    diff_manifest.json
    projects/*.md
```

`index.md` is the OKF bundle entrypoint. The root `index.md` may declare only
`okf_version: "0.1"` in frontmatter; other index files must not use
frontmatter. `log.md` is the human-readable event log and uses OKF ISO date
headings (`## YYYY-MM-DD`).
`approvals.jsonl` records every canonical promotion. `rejections.jsonl` records
human rejection decisions.

## Brain Diff Rule

Project concepts are driven by a private local inventory:

```bash
cp config/wiki_projects.example.json config/wiki_projects.json
```

Edit `config/wiki_projects.json` before compiling real local projects. The
private config is ignored by git.

AI-generated concept pages must be staged first:

```bash
doctor wiki \
  --out /Users/gengrf/agent-context-system \
  --action compile-baseline \
  --diff-id baseline-projects \
  --project-config /Users/gengrf/agent-context-system/config/wiki_projects.json
```

Review:

```text
vault/diffs/baseline-projects/DIFF_SUMMARY.md
vault/diffs/baseline-projects/projects/*.md
```

Only after review should the diff be promoted:

```bash
doctor wiki \
  --out /Users/gengrf/agent-context-system \
  --action approve \
  --diff-id baseline-projects
```

For a local bootstrap run that still preserves the staging boundary:

```bash
doctor wiki \
  --out /Users/gengrf/agent-context-system \
  --action baseline \
  --diff-id baseline-projects \
  --approve \
  --project-config /Users/gengrf/agent-context-system/config/wiki_projects.json
```

The command stages the diff first, then explicitly approves that staged diff.

If review finds that a route is wrong, reject it instead of approving it:

```bash
doctor wiki \
  --out /Users/gengrf/agent-context-system \
  --action reject \
  --diff-id baseline-projects \
  --reason "wrong route or weak evidence" \
  --failure
```

This writes:

```text
vault/diffs/<diff-id>/REJECTION.md
vault/rejections.jsonl
vault/failures/failure-<diff-id>.md
```

The failure concept is a governance record. It should downrank or warn about a
bad route later, but it does not modify source files or approved project pages.

## Governance Concepts

Seed ambiguous entity concepts:

```bash
doctor wiki \
  --out /Users/gengrf/agent-context-system \
  --action seed-entities
```

Record a merge/split correction without rewriting historical citations:

```bash
doctor wiki \
  --out /Users/gengrf/agent-context-system \
  --action correct-entity \
  --diff-id "split:entity-codex:project-codex-plus-plus" \
  --reason "Codex and Codex++ are related but not the same entity"
```

Write a contradiction concept:

```bash
doctor wiki \
  --out /Users/gengrf/agent-context-system \
  --action contradiction \
  --diff-id "entity-doctor::entity-mirror::hard" \
  --reason "Doctor is canonical knowledge; Mirror is feedback, not source of truth"
```

These actions write `vault/entities/*.md`, `vault/entity_corrections.jsonl`, and
`vault/contradictions/*.md`. They are governance records, not raw-source edits.

## Local Baseline Concepts

The private local baseline inventory can create project concepts for the user's
chosen projects, for example:

- PLM / PlotPilot / 墨枢
- Drama / Zen Drama
- Codex++
- Gugu / RoomLite
- Doctor / agent-context-system

Each project concept includes:

- OKF v0.1 frontmatter with required `type`
- OKF recommended fields: `title`, `description`, `resource`, `tags`, and
  `timestamp`
- Doctor extension fields: `id`, `aliases`, `citations`, `freshness`,
  `confidence`, and `source_hashes`
- source path
- source status
- aliases and tags
- source evidence table
- SHA-256 hashes for representative evidence files
- short citations from README/docs/config files
- `# Citations` section with source links
- baseline limitations

## OKF Conformance

Doctor treats the Vault as an OKF v0.1 bundle:

- every non-reserved `.md` file has parseable YAML frontmatter
- every concept document has a non-empty `type`
- `index.md` and `log.md` follow the OKF reserved-file structures
- `DIFF_SUMMARY.md` and `REJECTION.md` are also OKF concept documents when they
  live inside `vault/`

Run:

```bash
doctor vault-check \
  --out /Users/gengrf/agent-context-system \
  --rebuild
```

## Current Limitation

This is not yet a full graph compiler. It only creates the first project concept
layer. Entity, workflow, claim, contradiction, and failure templates exist so
later compilers can write into the same vault contract.
