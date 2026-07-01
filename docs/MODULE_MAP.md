# Module Map

Use this file to find the right edit point before changing code.

## Entry Points

| File | Responsibility |
|---|---|
| `src/agent_context/cli.py` | Main `agent-context`, `doctor`, and `doctor-douyin` command parser and dispatch |
| `src/agent_context/mcp_server.py` | FastMCP server and MCP tool surface |
| `src/agent_context/lab.py` | Terminal-style Doctor Lab for text/image prompts and feedback |
| `src/agent_context/mirror_lab.py` | Localhost Mirror Lab review UI |
| `src/agent_context/douyin.py` | `doctor-douyin` provider CLI |

## Ingestion And Extraction

| File | Edit when |
|---|---|
| `src/agent_context/ingest.py` | File scanning, hashing, markdown extraction, manifests, chunk writing |
| `src/agent_context/policies.py` | File type policy, archive metadata-only behavior, parser routing |
| `src/agent_context/resume.py` | Resume image/OCR-style attachment extraction and redaction |
| `src/agent_context/douyin.py` | Douyin URL/media provider, Markdown KV output, local media records |
| `src/agent_context/io.py` | Shared read/write helpers |

## Indexes And Retrieval

| File | Edit when |
|---|---|
| `src/agent_context/cold_index.py` | SQLite cold index and FTS behavior |
| `src/agent_context/semantic.py` | Semantic backend primitives |
| `src/agent_context/semantic_index.py` | Semantic index schema/status |
| `src/agent_context/semantic_maintenance.py` | Background semantic refresh and maintenance reports |
| `src/agent_context/retrieval_backends.py` | Retrieval backend abstraction and scoring modes |
| `src/agent_context/grep_route.py` | Lexical route hints from grep-like signals |
| `src/agent_context/evidence_index.py` | Unified evidence index built from providers and packs |
| `src/agent_context/file_catalog.py` | Whole-machine metadata catalog and source-zone style discovery |

## Providers

| File | Edit when |
|---|---|
| `src/agent_context/providers.py` | Provider manifests for projects, sessions, workflows, and source families |
| `src/agent_context/project_index.py` | Project discovery and source/doc/code index generation |
| `src/agent_context/session_index.py` | Codex/Claude session transcript previews and indexes |
| `src/agent_context/codebase_memory.py` | Optional `codebase-memory-mcp` bridge |

## Resolver And Hot Packs

| File | Edit when |
|---|---|
| `src/agent_context/resolver.py` | Task-to-context activation, source-scope routing, fusion ranking |
| `src/agent_context/pack.py` | `context.md`, `sources.jsonl`, and `manifest.json` generation |
| `src/agent_context/alternatives.py` | Alternative route generation and rejected-source handling |
| `src/agent_context/arena.py` | Three-candidate context comparison |
| `src/agent_context/compare.py` | Route A/B comparison workflows |

## Mirror And Personal Ranking

| File | Edit when |
|---|---|
| `src/agent_context/mirror_lab.py` | Mirror Lab HTML, localhost API, send-to-Doctor flow |
| `src/agent_context/mirror_ranker.py` | Pairwise ranker, score parts, model file |
| `src/agent_context/profile_graph.py` | Personal profile claims, evidence, reviewable diffs |
| `src/agent_context/feedback_model.py` | Feedback model compilation |
| `src/agent_context/feedback_replay.py` | Replay feedback effects |
| `src/agent_context/feedback_replay_cases.py` | Replay-case generation |
| `src/agent_context/feedback_replay_trend.py` | Replay trend reports |
| `src/agent_context/retrieval_eval.py` | Retrieval quality evaluation |
| `src/agent_context/retrieval_eval_cases.py` | Labeled retrieval eval cases |
| `src/agent_context/route_selector.py` | Route selector prior from eval reports |

## Runtime And Review Gates

| File | Edit when |
|---|---|
| `src/agent_context/clarify.py` | No-index task normalization |
| `src/agent_context/runtime_vm.py` | Four-stage Doctor runtime state and artifacts |
| `src/agent_context/runtime_task.py` | One-shot runtime task entrypoint |
| `src/agent_context/agent_preflight.py` | Unified client preflight flow |
| `src/agent_context/context_review.py` | Context/model-input review gate |
| `src/agent_context/answer_review.py` | Answer review gate |
| `src/agent_context/execution_review.py` | Execution review gate |
| `src/agent_context/runtime_review_server.py` | Local review UI API server |
| `src/agent_context/runtime_review_client.py` | Exported review client files |
| `src/agent_context/runtime_adapters.py` | Codex++/Warp/Codex CLI/MCP adapter exports |

## LLM-Wiki / OKF Vault

| File | Edit when |
|---|---|
| `src/agent_context/llm_wiki.py` | Compile staged LLM-Wiki / OKF pages |
| `src/agent_context/vault_index.py` | Vault index, resolve, anytime expansion, concept graph behavior |

## Health, Acceptance, And Safety

| File | Edit when |
|---|---|
| `src/agent_context/runtime_health.py` | Runtime health matrix |
| `src/agent_context/acceptance.py` | V1 acceptance and follow-up reports |
| `src/agent_context/mcp_live_smoke.py` | Real stdio MCP smoke test |
| `src/agent_context/reproducibility.py` | Dirty worktree and reproducibility snapshot |
| `src/agent_context/access_policy.py` | Read/permission policy and audit behavior |
| `src/agent_context/launchd.py` | macOS LaunchAgent helpers |

## Integration And Smoke Tests

| File | Edit when |
|---|---|
| `src/agent_context/codex_hook.py` | Codex++/Codex hook behavior |
| `src/agent_context/codex_plus_smoke.py` | Codex++ smoke scripts and reports |
| `src/agent_context/panel.py` | Context panel status HTML/JSON |

## Test Map

| Behavior | Tests |
|---|---|
| Downloads ingestion and packs | `tests/test_downloads_context_pack.py` |
| Resolver | `tests/test_context_resolver.py` |
| Mirror Lab | `tests/test_mirror_lab.py` |
| Mirror ranker | `tests/test_mirror_ranker.py` |
| Profile graph | `tests/test_profile_graph.py` |
| Runtime VM and review | `tests/test_runtime_vm.py`, `tests/test_runtime_review_client.py`, `tests/test_runtime_task.py` |
| MCP and smoke | `tests/test_mcp...` when present, `tests/test_runtime_health.py`, `tests/test_codex_plus_smoke.py` |
| LLM-Wiki / vault | `tests/test_llm_wiki.py`, `tests/test_vault_index.py` |
| Semantic maintenance | `tests/test_semantic_maintenance.py`, `tests/test_semantic_launchd.py`, `tests/test_semantic_status.py` |

