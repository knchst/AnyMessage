# Changelog

## 0.8.0

- Read classic Telegram exported files, photos, thumbnails, and vCards through message-, snapshot-, and variant-bound attachment IDs. Return supported document text, native images, or explicitly requested original bytes with existing limits and unchanged tool inputs.
- Keep originals and thumbnails independent. Distinguish export omissions, missing local files, unsafe paths, size mismatches, and unsupported formats without downloading or searching for files.
- Confine media to the selected export folder and reject symlinks, including the selected JSON filename. Existing JSON-symlink users must pass the target's real filename; parent directory aliases remain supported.
- Report current local media, observed sizes, and local SHA-256 values without claiming Telegram authenticity. `content_verified` stays false and JSON retains its immutable snapshot semantics.
- Add a downloadable fictional River specification and exercise actual attachment content in installed-wheel and version-aware MCPB checks. Preserve historical bundle checks and WeChat behavior.

## 0.7.0

- Add explicit `mcp --telegram-export PATH` startup for key-free, text-only Telegram Desktop JSON snapshots without reading saved WeChat settings or discovering accounts or SQLCipher.
- Read single-chat and joined/left account chat lists, classic text and entity arrays, captions, and literal service text. Preserve type-qualified chat IDs, signed 64-bit message IDs, unsupported dated rows, and explicit counts for undated omissions.
- Add snapshot-, query-, and selection-bound cursors, shared bounded search, and exact chronological context with the existing seven MCP input schemas.
- Return explicit unsupported Telegram media results without resolving or opening referenced paths. Add a downloadable fictional export and complete manual client configuration.
- Bound JSON input at 16 MiB and document in-memory loading, restart behavior, and incomplete export coverage. Keep WeChat and the installed MCPB launch as defaults.

## 0.6.0

- Add `get_message_context` to read an exact conversation/message pair with up to 50 nearest older and 50 nearest newer local records in chronological order.
- Return the anchor once, its index, and remaining-record flags for each side, including zero-count requests. Preserve sender, decoding states, attachment IDs, and local per-query coverage.
- Extend the fictional demo from a selected-chat search hit to the adjacent approval and verified specification attachment.
- Verify seven read-only tools in current MCPB bundles while retaining the six-tool runtime check for historical releases before 0.6.0.

## 0.5.0

- Add UTC Unix-second date bounds to history and text search, and search 1–8 explicitly selected conversations with a shared scan budget.
- Bind dated and multi-chat continuation to the local account, selection, and bounds while retaining legacy unbounded single-chat cursors and all six MCP tools.
- Extend the fictional demo with a supplier chat and actual MCP date-range and selected-chat calls.
- Add source MCPB build and verification commands and manual Registry publication using GitHub OIDC.

## 0.4.0

- Include extracted attachment pages in structured MCP results so clients such as Claude Code can read the document text.
- Add `anymessage demo` and `anymessage demo --mcp` with temporary encrypted fictional messages and attachments read through the production MCP tools.
- Distinguish demo data in the server name, instructions, and status without loading saved account configuration.
- Add a reproducible walkthrough covering an approved change, an unconfirmed proposal, a missing drawing, and incomplete search coverage.
- Correct the security and module descriptions for explicit loopback Streamable HTTP support.
- Add English and Chinese onboarding, three investigation recipes, a public trial form, and a maintainer command to record GitHub growth snapshots.

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
