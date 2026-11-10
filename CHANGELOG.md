# Changelog

## Unreleased

- Include extracted attachment pages in structured MCP results so clients such as Claude Code can read the document text.

## 0.3.1

- Add `missing_reason` and fixed manual recovery guidance for attachment index queries with no match and unavailable cache files, while preserving `attachment_missing` and attachment ids.

## 0.3.0

- Add row-only attachment metadata to message history and two read-only MCP tools, `list_attachments` and `read_attachment`.
- Verify message-bound local file identities and actual digest and size before reading content.
- Read ANSI-colored Unicode logs with explicit text-normalization metadata while preserving original binary bytes.
- Extract bounded PDF, DOCX, XLSX, PPTX, and Unicode text, with text continuation and explicit failure states.
- Return native MCP image content for supported raw images and V2 caches using existing account-matched configuration, with separate original, HD, and thumbnail variants.
- Return bounded original file bytes as embedded MCP resources and preserve metadata in both structured and text content.
- Cover attachment lookup, document formats, cache failures, V2 decoding, and stdio and HTTP content round trips with synthetic fixtures.

## 0.2.0

- Homebrew installation with guided `setup`, existing-key reuse, explicit first-time capture consent, and private saved references.
- Read-only `doctor` diagnostics and JSON output.
- Client registration for Codex, Cursor, Claude Desktop, Claude Code, and Grok CLI, with configuration preservation, private backups, conflict checks, and idempotent writes.
- Generic stdio configuration output and optional loopback Streamable HTTP, with the hosted Grok Bot boundary documented.
- Byte-identical bootstrap resources and license included in the wheel.
- Isolated CLI, client configuration, HTTP protocol, and installed-wheel smoke checks.

## 0.1.0

- Initial read-only stdio MCP server with WeChat as its first source.
- Conversation listing, paginated cross-shard text history, and bounded conversation search.
- SQLCipher key and schema readiness checks, UTF-8 and Zstandard text decoding, and explicit incomplete-search coverage.
- Synthetic encrypted-database, WAL, and MCP protocol tests.
- Optional upstream Apple Silicon first-key tools, kept separate from the reader.
