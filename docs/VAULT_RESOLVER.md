# Vault Resolver

The Vault Resolver is the second layer after the LLM-Wiki / OKF Vault baseline.

It does not feed the whole vault to the model. It builds derived indexes and
activates a bounded set of concepts for the current task.

```text
vault Markdown
  -> indexes/vault.sqlite
  -> FTS table
  -> local vector rows
  -> graph edges
  -> aliases/tags
  -> feedback-aware ranking
  -> bounded hot context
```

## Build The Derived Index

```bash
doctor vault-index \
  --out /Users/gengrf/agent-context-system
```

This writes:

```text
indexes/vault.sqlite
indexes/knowledge.sqlite
indexes/knowledge_edges.jsonl
reports/vault_index_report.md
```

The SQLite database contains:

- `concepts`
- `concepts_fts`
- `aliases`
- `graph_edges`
- `citations`
- `freshness`
- `claims`
- `score_features`
- `meta`

Validate the vault and index boundary:

```bash
doctor vault-check \
  --out /Users/gengrf/agent-context-system
```

Use `--rebuild` when the approved Markdown changed and the derived index should
be deleted and recreated first:

```bash
doctor vault-check \
  --out /Users/gengrf/agent-context-system \
  --rebuild
```

This writes `reports/vault_check_report.md` and checks required OKF frontmatter,
concept-count drift, and vector backend metadata drift.

## Baseline Evaluation

Run the baseline comparison report:

```bash
doctor wiki \
  --out /Users/gengrf/agent-context-system \
  --action baseline-report
```

This writes:

```text
reports/llm_wiki_baseline_eval_<timestamp>.json
reports/llm_wiki_baseline_eval_<timestamp>.md
reports/llm_wiki_baseline_eval_latest.md
```

The report compares raw grep/provider routing, existing context packs, vault
retrieval, and full-vault token estimates for the default baseline questions,
including the resume-packaging task and `开源往事如何在番茄爆火，面向的读者是谁`.

## Fast Answer

```bash
doctor vault-resolve \
  --out /Users/gengrf/agent-context-system \
  --goal "我要找适合简历包装的项目" \
  --mode fast \
  --limit 8
```

This writes a normal Doctor hot context pack:

```text
packs/<task-id>/
  context.md
  sources.jsonl
  manifest.json
  vault_resolution_plan.json
```

It also writes an anytime state file:

```text
vault/anytime/<task-id>/state.json
```

The general Doctor resolver can also use the vault as an explicit optional
provider without changing the default `all` route:

```bash
doctor resolve \
  --out /Users/gengrf/agent-context-system \
  --source-scope vault \
  --goal "我要去求职，请帮我看下我的电脑里适合简历包装的项目有哪些" \
  --limit 8
```

Use this when you want to inspect what the approved long-term knowledge layer
would feed to Codex before mixing in Downloads, sessions, or project-code search.

## Slow Answer / Anytime Step

The first implementation is a bounded worker step rather than a daemon. If the
user accepts the fast answer, stop the state:

```bash
doctor vault-anytime-step \
  --out /Users/gengrf/agent-context-system \
  --state vault/anytime/<task-id>/state.json \
  --feedback satisfied
```

If the user says the result is wrong:

```bash
doctor vault-anytime-step \
  --out /Users/gengrf/agent-context-system \
  --state vault/anytime/<task-id>/state.json \
  --feedback not_right \
  --limit 12 \
  --max-files-per-root 80
```

The step expands from selected concepts into their `source_path` directories,
scores README/docs/config/source files, and writes another bounded context pack:

```text
packs/<task-id>-vault-anytime-r<n>/
  context.md
  sources.jsonl
  manifest.json
  vault_anytime_step.json
```

It also updates the same `vault/anytime/<task-id>/state.json` with the latest
feedback, expansion round, and expanded source list.

## Ranking Channels

The resolver currently combines:

- catalog prior
- exact keyword / alias / tag hits
- SQLite FTS
- local vector score
- graph/tag prior
- freshness / failure-path penalties
- feedback model boost

For resume-style tasks, the baseline prior intentionally boosts the known main
projects:

- PLM / PlotPilot / 墨枢
- Drama / Zen Drama
- Codex++
- Gugu / RoomLite

Doctor itself remains available, but it is not forced above the user's primary
portfolio projects.

## Token Boundary

The resolver reports both:

- current vault Markdown token estimate
- projected source-backed token estimate if the vault were expanded from linked
  source paths

The current baseline vault is small enough to feed in one prompt. The useful
boundary is future-facing: once the vault is compiled from the full local source
world, it must be browsed through resolver-ranked context packs instead of
blindly pasted into the model.
