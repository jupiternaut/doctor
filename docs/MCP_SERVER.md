# MCP Server

`agent-context mcp` exposes the local context system through the Model Context
Protocol over stdio.

The MCP layer is intentionally thin. It does not replace the cold index, RAG
query packs, or hot context packs. It lets MCP clients call those existing
contracts.

## Tools

```text
search_context(query, limit=12)
  Query the cold index and write a RAG context pack.

index_context()
  Rebuild indexes/context.sqlite from manifests/*.jsonl.

build_hot_pack(scope, goal, with_index=false)
  Run ingestion and write a Codex-readable hot context pack.

read_source(identifier, max_chars=4000)
  Read a source by path, source_id, source_chunk_id, chunk_id, doc_id, or relative path.

record_feedback(query_id, selected_source, reason="", rating=null)
  Append user feedback to feedback/mcp_feedback.jsonl.
```

## Start Manually

```bash
cd /Users/gengrf/agent-context-system
uv run agent-context mcp --out /Users/gengrf/agent-context-system
```

The server uses stdio, so this command is normally launched by an MCP client
rather than run directly in a human terminal.

## Client Config

Use this shape for clients that accept MCP server JSON:

```json
{
  "mcpServers": {
    "agent-context": {
      "command": "uv",
      "args": [
        "run",
        "--project",
        "/Users/gengrf/agent-context-system",
        "agent-context",
        "mcp",
        "--out",
        "/Users/gengrf/agent-context-system"
      ]
    }
  }
}
```

The `--out` directory must contain the generated project data:

```text
manifests/documents.jsonl
manifests/chunks.jsonl
manifests/failures.jsonl
indexes/context.sqlite
```

If `indexes/context.sqlite` is missing, `search_context` will try to rebuild it
from the manifests.

## Typical Flow

```text
1. search_context("哪些文件适合进入个人助手长期记忆")
2. read_source("<path or source_chunk_id from top_sources>")
3. record_feedback("<query_id>", "<selected source>", "useful")
```

For a fresh folder:

```text
1. build_hot_pack("/Users/gengrf/Downloads", "分析 Downloads 里哪些文件适合进入个人助手长期记忆", true)
2. search_context("哪些文件适合进入个人助手长期记忆")
```

## Security Boundary

The MCP server can read source files that are returned by the index and can write
only generated outputs under the configured `--out` root. It does not modify the
scanned source scope. Keep `--out` pointed at a trusted local checkout.

The first version is local stdio only. It does not expose HTTP, authentication,
or remote access.
