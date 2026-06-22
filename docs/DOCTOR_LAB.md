# Doctor Lab

Doctor Lab is the manual evaluation console for Doctor's context router.

It is for the loop where a user gives a real task, optionally attaches images,
reviews the top sources, and records whether the result matched intent.

## Start

Interactive mode:

```bash
uv run ./agent-context lab --out /Users/gengrf/agent-context-system
```

macOS Terminal window:

```bash
open /Users/gengrf/agent-context-system/scripts/doctor-lab.command
```

One-shot mode:

```bash
uv run ./agent-context lab \
  --out /Users/gengrf/agent-context-system \
  --text "告诉我本地所有项目里如何构建个人推荐系统" \
  --image /absolute/path/to/image.png \
  --once
```

## Interactive Commands

```text
plain text        Add task text to the current prompt.
blank line        Run the current prompt.
/image <path>     Attach an image path to the current prompt.
/run <text>       Run immediately with this text.
/good <n>         Mark source n as useful.
/bad <n>          Mark source n as irrelevant.
/open <n>         Print source n's path.
/clear            Clear current text and images.
/quit             Exit.
```

## Outputs

Each run writes:

```text
lab/runs/<run-id>/input.md
lab/runs/<run-id>/attachments.jsonl
lab/runs/<run-id>/run.json
packs/<task-id>/context.md
packs/<task-id>/sources.jsonl
packs/<task-id>/resolution_plan.json
```

`context.md` starts with the exact Lab input. Image attachments are rendered as
Markdown image links so Codex can see which files were part of the task.

Attachment file names, hashes, and absolute paths are not injected into the
resolver query. They stay in `input.md` and `attachments.jsonl`; the resolver
only receives a coarse structured hint such as `attachment_hint: resume_image`.
This keeps random image hashes and temp paths from polluting local source
ranking.

`attachments.jsonl` records image path, hash, size, dimensions when available,
and provider metadata. `agent-context evidence-index` also scans Lab
attachments, so they can enter the unified evidence bus.

## Comparison Tasks

When the prompt looks like a comparison and includes a resume image, Lab uses a
two-slot comparison schema instead of one mixed resolver list:

```text
left_user_projects -> local Codex/Doctor/project evidence
right_resume       -> resume image OCR/KV evidence
```

The left slot is resolved from local project evidence. The right slot is built
by `doctor_resume_ocr`, which tries macOS Vision OCR first and falls back to
Tesseract when available. The resulting run may also write:

```text
lab/runs/<run-id>/resume.md
lab/runs/<run-id>/resume.json
lab/runs/<run-id>/resume_sources.jsonl
packs/<task-id>/comparison_plan.json
```

The context pack tells the model to compare the two evidence slots rather than
treat all retrieved sources as a single top-8 list.

## Feedback

`/good <n>` and `/bad <n>` write both:

```text
feedback/lab_feedback.jsonl
feedback/panel_feedback.jsonl
```

The panel feedback path is intentional: `feedback/model.json` already reads
panel feedback, so Lab choices immediately affect later resolver ranking.

## Limitations

Doctor Lab v0.1 records image metadata and inserts image links into the hot
context pack. Resume-image comparison tasks attempt OCR, but general visual
captioning and image embeddings still belong in the future MediaProvider.
