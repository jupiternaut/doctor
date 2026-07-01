# Mirror Lab v0.2

Mirror Lab is the reviewable preference layer above Doctor.

```text
Doctor = local files, OKF vault, cold indexes, hot context packs
Mirror = personal profile graph, pairwise ranking feedback, review UI
```

## Goals

Mirror Lab v0.2 adds three pieces:

1. Profile Graph: a reviewable model of important projects, resume fit,
   negative signals, freshness, and evidence.
2. Pairwise Ranker: a lightweight recommendation loop that turns choices and
   rejections into ranking weights.
3. Chinese Lab UI: a local HTML review surface for the four-stage workflow.

## Profile Graph

Profile changes are append-only and reviewable.

```bash
uv run ./agent-context profile-event \
  --target-id project-plm \
  --label main_project \
  --note "primary long-term project"

uv run ./agent-context profile-diff --out .
uv run ./agent-context profile-approve --out . --diff-id <diff-id>
```

Generated files:

```text
profiles/profile_events.jsonl
profiles/profile_graph.json
profiles/personal_profile.md
profiles/diffs/<diff-id>/PROFILE_DIFF.md
profiles/diffs/<diff-id>/diff.json
```

Every profile claim must have evidence, confidence, freshness, and review
status.

## Pairwise Ranker

The ranker records user choices as pairwise training examples.

```bash
uv run ./agent-context ranker-feedback \
  --goal "我要去求职，请帮我看下我的电脑里适合简历包装的项目有哪些" \
  --winner-json '{"id":"project-plm","profile_prior":1,"bm25":0.7}' \
  --loser-json '{"id":"random-doc","profile_prior":0,"bm25":0.6}' \
  --reason "PLM is my real project"

uv run ./agent-context ranker-train --out .
```

Generated files:

```text
feedback/training_examples.jsonl
feedback/ranker_model.json
reports/ranker_eval_latest.md
```

Scoring returns `score_parts` so the ranking can be audited.

When `feedback/training_examples.jsonl` exists, the generic resolver applies the
ranker during candidate fusion and writes the contribution to:

```text
resolver_score_parts.mirror_ranker
resolver_score_parts.mirror_ranker_raw
resolver_score_parts.mirror_ranker_explanation
```

## Chinese Lab UI

```bash
uv run ./agent-context mirror-lab-server \
  --out . \
  --goal "我要去求职，请帮我看下我的电脑里适合简历包装的项目有哪些" \
  --mode fast
```

Generated files:

```text
mirror_lab/index.html
mirror_lab/state.json
```

Open the printed localhost URL, usually:

```text
http://127.0.0.1:8787/
```

The localhost UI is the interactive path. Clicking `发送到 Doctor` calls the local
Doctor backend, generates a hot context pack, and shows:

- generated `context.md`
- generated `sources.jsonl`
- generated `manifest.json`
- top source paths and scores

`mirror-lab` still exists as an offline snapshot builder, but a `file://` page
cannot directly call Doctor. Use `mirror-lab-server` when you want a real input
box and send button.

The UI shows:

- 需求归一化
- Doctor 上下文注入
- 模型回答审查
- 本机执行审查
- fast / deep / arena context lanes
- feedback buttons for main project, resume fit, stale source, privacy, and
  downranking

## Acceptance Prompts

Use these prompts for manual evaluation:

```text
我要去求职，请帮我看下我的电脑里适合简历包装的项目有哪些
我 codex 的项目和这个人的简历比起来有什么区别
开源往事如何在番茄爆火，面向的读者是谁
```

Passing means the user can inspect what would be sent to the model, record a
preference correction, and see ranking artifacts change on disk.
