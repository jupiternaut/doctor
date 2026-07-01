# Roadmap

This roadmap is practical. It prioritizes what makes Doctor/Mirror usable by another developer and then useful as a product.

## Milestone 1: Developer Handoff

Goal: another engineer can understand, run, and safely edit the project.

Required:

- Keep this onboarding docs set current.
- Add docs validation.
- Make README point to the newcomer path.
- Refresh V1 acceptance evidence.
- Commit or snapshot the intended worktree boundary.

Exit criteria:

- `docs/DEVELOPER_ONBOARDING.md` is enough to run the quick checks.
- `docs/MODULE_MAP.md` is enough to find edit points.
- `docs/DATA_CONTRACT.md` prevents private generated data from being treated as source.
- V1 acceptance status is fresh, even if it still fails.

## Milestone 2: Context Activation Quality

Goal: Doctor activates the right evidence more often than a generic search query.

Required:

- Add regression cases for real user tasks.
- Separate task intents such as resume, research, writing, coding, product review, and media/profile analysis.
- Improve source priors by task type.
- Make failures explainable through `resolution_plan.json`.
- Ensure relevant resume/project tasks prioritize PLM, Drama, Codex++, and Gugu when appropriate.

Exit criteria:

- A curated test set shows expected top sources for key tasks.
- Rejected sources become replay cases.
- Context packs explain why each top source was selected.

## Milestone 3: Mirror Personalization

Goal: Mirror becomes a trustworthy personal ranking layer, not only a UI.

Required:

- Strengthen profile graph claims and review diffs.
- Convert feedback into stable pairwise examples.
- Add task-aware ranking features.
- Show score parts in the UI.
- Make user feedback visibly change future ranking.

Exit criteria:

- The same query before/after feedback changes ranking in a predictable way.
- Profile claims have evidence and review status.
- Mirror can explain why a source is "important to this user."

## Milestone 4: Product UI Boundary

Goal: move from generated validation pages to a deliberate client surface.

Options:

- Keep Python-generated pages for internal use and build a separate web client.
- Build a Tauri/Electron shell around local APIs.
- Build a macOS native shell if local-first desktop integration matters most.

Required before UI rewrite:

- Stable localhost API contracts.
- Stable review and feedback schemas.
- Clear generated artifact locations.
- No hidden dependency on private local paths.

Exit criteria:

- A user can enter text/images, inspect sources, approve/reject context, and record feedback without using the terminal.

## Milestone 5: Media And Whole-Machine Providers

Goal: make non-text local evidence useful without polluting context.

Required:

- Strong metadata-first indexing for full-disk scope.
- OCR and ASR pipelines for selected media zones.
- Douyin/video Markdown KV extraction.
- Source-zone weighting and privacy boundaries.
- Clear "metadata-only" versus "content-extracted" status.

Exit criteria:

- Media sources can appear in context packs with transcript/summary/provenance.
- The resolver can explain why it chose or skipped media evidence.

## Milestone 6: Retrieval Engine Upgrade

Goal: move beyond rule-heavy retrieval without losing auditability.

Required:

- ANN vector search behind the existing semantic backend boundary.
- Lightweight reranker or learning-to-rank model.
- More labeled eval cases.
- Multi-objective reranking by task type.
- Bandit-style exploration slots only after deterministic quality is acceptable.

Exit criteria:

- Offline eval improves without hiding source provenance.
- The hot context pack contract stays stable.

## Milestone 7: Public Release Boundary

Goal: release a usable open-source subset.

Required:

- Remove or sanitize private local data.
- Keep fixtures small and representative.
- Make default commands work on a fresh checkout.
- Document what is local-only and what is public-safe.
- Refresh acceptance and smoke reports.

Exit criteria:

- A developer can clone, run tests, build sample packs, and understand what is not included.
- Public docs do not depend on `/Users/gengrf/...` except where explicitly labeled as local-handoff examples.

