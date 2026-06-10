# GitHub Reuse Report

Checked on 2026-06-10 using `gh repo view`.

## Summary

v0.1 uses MarkItDown directly for document conversion and keeps Docling as an
optional fallback when installed. Tika is rejected for v0.1 because it adds a
heavier Java service boundary. Repomix and MCP servers are design references
for future context packaging and MCP integration.

## Candidates

| Repository | License | Current signal | Decision | Reason |
| --- | --- | --- | --- | --- |
| https://github.com/microsoft/markitdown | MIT | Python tool for converting files and Office documents to Markdown; 149525 stars; updated 2026-06-10 | Used directly | Best fit for v0.1 Markdown extraction with small Python surface. Archives remain metadata-only despite MarkItDown archive support. |
| https://github.com/docling-project/docling | MIT | Document conversion for gen-AI workflows; 61281 stars; updated 2026-06-10 | Optional fallback/design reference | Stronger document/layout parser, but heavier than v0.1 needs as a required dependency. Import only if installed. |
| https://github.com/apache/tika | Apache-2.0 | Detects and extracts metadata/text from many file types; 3800 stars; updated 2026-06-09 | Rejected for v0.1 | Broad and mature, but Java/service complexity is not justified for the first local CLI. |
| https://github.com/yamadashy/repomix | MIT | Packs repositories into AI-friendly files and supports MCP; 26131 stars; updated 2026-06-10 | Design reference | Useful model for AI-readable packs; not used directly because this project scans arbitrary personal documents, not only repos. |
| https://github.com/modelcontextprotocol/servers | Other | Reference MCP server implementations; 86981 stars; updated 2026-06-10 | Future reference | v0.1 is CLI and file outputs only; v0.2 can expose manifests and packs through MCP. |
| https://github.com/basicmachines-co/basic-memory | AGPL-3.0 | Local-first Markdown memory and MCP; 3179 stars; updated 2026-06-10 | Design/local POC reference | Useful memory layer, but AGPL and Markdown-first assumptions make it unsuitable as the core document ingestion implementation. |

## Implementation Notes

- MarkItDown is the only required third-party runtime parser in v0.1.
- The dependency intentionally avoids `markitdown[all]` and uses only
  `docx`, `pdf`, `pptx`, `xls`, and `xlsx` extras so v0.1 covers required
  document formats without pulling audio, Azure, or YouTube dependencies.
- Docling fallback is attempted only when available in the environment.
- Archive/package expansion is intentionally disabled even if a parser supports it.
- The storage contract stays simple: extracted Markdown plus JSONL manifests.
