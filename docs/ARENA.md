# Arena Evaluation

`agent-context arena` creates a lightweight LM Arena-style evaluation for local
context routes.

It does not assume a single correct answer. It generates three candidate
answers from three different context routes, randomizes their display order, and
records the user's preferred candidate as feedback.

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
  -> feedback event
```
