# Arena Evaluation

`agent-context arena` creates a lightweight LM Arena-style evaluation for local
context routes.

It does not assume a single correct answer. It generates three candidate
answers from three different context routes, randomizes their display order, and
records the user's preferred candidate as feedback. The feedback is stored as
winner/loser pairwise data, so future resolver runs can boost selected sources
and downweight rejected alternatives.

## Routes

```text
A: chunk pack
   direct extracted snippets and source quotes

B: graph context map
   folder/type/goal/document/chunk relationships for long-term memory candidates

C: explore diversity
   unusual folders, file types, metadata-only records, failures, and other blind spots
```

## Command

```bash
agent-context arena \
  --scope /Users/gengrf/Downloads \
  --goal "分析 Downloads 里哪些文件适合进入个人助手长期记忆"
```

To reuse existing manifests:

```bash
agent-context arena \
  --scope /Users/gengrf/Downloads \
  --goal "分析 Downloads 里哪些文件适合进入个人助手长期记忆" \
  --skip-ingest
```

## Output

```text
packs/<task-id>-arena-<timestamp>/
  slate.md
  slate.json
  slate_key.json
  feedback.jsonl
  retrieval_eval_cases.jsonl
  candidate-1/
    context.md
    answer.md
    sources.jsonl
    route.json
  candidate-2/
    context.md
    answer.md
    sources.jsonl
    route.json
  candidate-3/
    context.md
    answer.md
    sources.jsonl
    route.json
feedback/arena_feedback.jsonl
feedback/retrieval_eval_cases.jsonl
```

`slate.md` is what the user should read. It intentionally labels outputs as
`candidate-1`, `candidate-2`, and `candidate-3`.

`slate_key.json` preserves the route mapping for later analysis.

## Feedback

After the user picks a candidate:

```bash
agent-context feedback \
  --slate packs/<task-id>-arena-<timestamp>/slate.json \
  --winner candidate-2 \
  --reason "best matches my intent"
```

The feedback is appended to both:

```text
packs/<task-id>-arena-<timestamp>/feedback.jsonl
feedback/arena_feedback.jsonl
```

It also writes retrieval-eval cases to both:

```text
packs/<task-id>-arena-<timestamp>/retrieval_eval_cases.jsonl
feedback/retrieval_eval_cases.jsonl
```

Those cases use the arena goal as `query` and the selected candidate's source
ids/paths as `expected_sources`. For repeated use, curate the raw feedback first:

```bash
agent-context retrieval-eval-cases --out /Users/gengrf/agent-context-system
agent-context retrieval-eval --out /Users/gengrf/agent-context-system --source downloads
```

`retrieval-eval-cases` writes `feedback/retrieval_eval_cases.curated.jsonl`
without editing the raw log. `agent-context retrieval-eval` prefers that curated
file when it is non-empty, and later resolver runs use the resulting route
selector prior.

Feedback also refreshes:

```text
feedback/model.json
```

Each feedback event records:

```text
winner
winner_route
winner_sources
query_family
candidates[].source_keys
pairwise_comparisons[]
```

`feedback/model.json` then exposes `pairwise_stats`, `pairwise_elo`,
`query_family_pairwise_elo`, `pairwise_bradley_terry`,
`query_family_pairwise_bradley_terry`, `route_scores`, `source_scores`,
`query_family_route_scores`, `query_family_source_scores`, and
`replay_supervision_cases`. The resolver reads this model and applies it as a
bounded rerank prior: winner sources receive positive feedback, loser sources
receive negative feedback, Elo adds a small online pairwise rating signal,
Bradley-Terry adds a batch-fitted winner/loser preference signal, replay cases
with `expected_source` add a bounded same-family expected-source prior, and
same-family tasks receive a stronger scoped prior. The choice remains
explainable in `resolver_score_parts`.

## Replay Evaluation

Replay compares resolver ranking before and after feedback:

```bash
agent-context feedback-replay-cases \
  --out /Users/gengrf/agent-context-system
agent-context feedback-replay \
  --out /Users/gengrf/agent-context-system \
  --case "告诉我本地所有项目里如何构建个人推荐系统" \
  --source-scope gitProjects
agent-context feedback-replay-trend \
  --out /Users/gengrf/agent-context-system \
  --max-reports 20
```

`feedback-replay-cases` writes generated cases to:

```text
feedback/replay_cases.generated.jsonl
```

For a manual reusable set, create:

```text
feedback/replay_cases.jsonl
```

Each JSONL record accepts:

```json
{"goal":"告诉我本地所有项目里如何构建个人推荐系统","source_scope":"gitProjects","limit":8,"expected_source":"data/preference_state.json"}
```

`feedback-replay` reads both manual and generated cases when `--cases` is not
provided. The command writes `reports/feedback_replay_<timestamp>.json` and
`.md`, including baseline top sources, with-feedback top sources, top1 changes,
and expected-source rank changes. Case `limit` is capped by the command-line
`--limit`, so a generated case cannot silently exceed the requested replay
budget. `feedback-replay-trend` reads existing replay reports and writes
`reports/feedback_replay_trend_<timestamp>.json` and `.md`, flagging
expected-source loss, expected-rank regressions, and insufficient replay
history without mutating the feedback model.

## Current Limit

Arena v0.1 generates candidate answers locally from selected route sources. It
does not yet call Codex as a subprocess or an API. The generated answers are
usable for route comparison and selection, while future versions can replace
the local answer renderer with a Codex-backed answer generator.

The important contract is already stable:

```text
same goal
  -> three route-specific contexts
  -> three candidate answers
  -> user preference
  -> pairwise feedback event
  -> feedback/model.json
  -> later resolver rerank prior
```
