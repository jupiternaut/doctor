## 1. Baseline Scope And Contracts

- [x] 1.1 Add a `docs/LLM_WIKI_OKF_BASELINE.md` handoff that explains the raw-files, vault, derived-index, resolver, and hot-context layers.
- [x] 1.2 Decide and document the first canonical vault root path, including whether it is `vault/` or `okf/doctor-vault/`.
- [x] 1.3 Add the vault directory contract for `index.md`, `log.md`, `projects/`, `entities/`, `workflows/`, `sources/`, `claims/`, `contradictions/`, `failures/`, and `diffs/`.
- [x] 1.4 Add a context-size rule that explicitly rejects full-vault model injection and requires bounded concept selection.

## 2. OKF Concept Templates

- [x] 2.1 Add OKF-compatible templates for project, entity, workflow, source, claim, contradiction, and failure concepts.
- [x] 2.2 Include required frontmatter fields for type, title, description, timestamp, stable id, aliases, citations, freshness, confidence, and source hashes where applicable.
- [x] 2.3 Add template examples for PLM, Drama, Codex++, Gugu, and Doctor using existing local evidence paths.
- [x] 2.4 Add an OKF compatibility check that validates generated concept frontmatter and reports missing required fields.

## 3. Baseline Compiler And Diff Staging

- [x] 3.1 Implement a baseline compiler command that reads selected existing Doctor evidence and writes proposed vault pages into a run-specific diff directory.
- [x] 3.2 Ensure the compiler does not mutate original source files or approved vault pages before user approval.
- [x] 3.3 Write a diff manifest that lists proposed pages, source files, hashes, prompts or tool inputs, and expected derived-index updates.
- [x] 3.4 Add an approve/reject command for baseline diffs and record rejection reasons as review artifacts.
- [x] 3.5 Add a failure concept path for rejected routes that the user marks as a dead end.

## 4. Entity Identity And Governance

- [x] 4.1 Add stable entity id generation and alias mapping for project and named-entity concepts.
- [x] 4.2 Add merge/split correction records so user corrections influence future entity resolution without rewriting source citations.
- [x] 4.3 Add contradiction concept generation for hard conflicts and soft tensions.
- [x] 4.4 Add stale and freshness metadata to concepts and expose stale warnings during retrieval.
- [x] 4.5 Add tests for ambiguous names such as Codex, Doctor, Mirror, PLM, and Gugu.

## 5. Derived Knowledge Index

- [x] 5.1 Add a derived `indexes/knowledge.sqlite` schema for vault concepts, aliases, citations, claims, freshness, and score features.
- [x] 5.2 Add lexical FTS over approved vault concepts and preserve exact path/name/entity matches as ranking features.
- [x] 5.3 Add vector metadata boundaries with embedding model identity and stale-vector detection, even if the first implementation uses an existing semantic backend.
- [x] 5.4 Add graph edge export for concept links, entity relations, contradiction links, and source citations.
- [x] 5.5 Add rebuild and integrity-check commands that can delete/recreate derived knowledge indexes from approved vault pages.

## 6. Retrieval And Resolver Integration

- [x] 6.1 Add a vault query command that returns bounded concept results with score parts, citations, freshness, and confidence.
- [x] 6.2 Add the vault/index as an optional Doctor resolver provider without changing the default resolver path.
- [x] 6.3 Generate a hot context pack from vault query results and include links back to concept pages and original source paths.
- [x] 6.4 Import Mirror-style feedback and arena choices as ranking priors without treating them as canonical facts.
- [x] 6.5 Add replay cases proving rejected sources or failure concepts can downrank future routes.

## 7. Baseline Evaluation

- [x] 7.1 Add a `doctor wiki baseline` report that compares raw file search, existing context packs, vault retrieval, and full-vault token estimates.
- [x] 7.2 Run the baseline on the task `我要去求职，请帮我看下我的电脑里适合简历包装的项目有哪些`.
- [x] 7.3 Run the baseline on the task `开源往事如何在番茄爆火，面向的读者是谁`.
- [x] 7.4 Record whether PLM, Drama, Codex++, Gugu, and Doctor appear as project concepts with evidence-backed citations.
- [x] 7.5 Record token sizes for top-level index, selected concepts, generated hot context, and whole-vault estimates.

## 8. Verification And Documentation

- [x] 8.1 Add unit tests for OKF template validation, diff staging, approval/rejection, entity identity, contradiction concepts, and index rebuild.
- [x] 8.2 Add an end-to-end fixture test for `compile -> diff -> approve -> index -> query -> hot context`.
- [x] 8.3 Run the existing project test suite and record the result.
- [x] 8.4 Update README and handoff docs with the LLM-Wiki/OKF baseline path and commands.
- [x] 8.5 Verify OpenSpec status is complete before implementation begins.
- [x] 8.6 Keep the existing Doctor Runtime OpenSpec change linked but separate from this knowledge-layer baseline.
