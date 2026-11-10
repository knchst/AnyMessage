# AnyMessage architecture

An agent calls a small MCP tool through stdio or an explicitly started loopback Streamable HTTP server.
The tool selects the registered WeChat source and returns structured metadata.
Attachment reads also return native MCP text, image, or embedded binary content.
The WeChat source owns account discovery, key validation, schema checks, SQLCipher queries, message decoding, and cross-shard ordering.
The reader sends fixed read-only SQL and the selected database key to SQLCipher through stdin.

## Choose direct local database reads

| Candidate | Benefit | Constraint | Decision |
| --- | --- | --- | --- |
| Direct SQLCipher reads | Own stable MCP results, bounded queries, and explicit shard coverage. Read committed local WAL records. | Requires verified per-database keys and runtime schema checks. | Implemented. |
| [GreenBubbles CLI wrapper](https://github.com/bojieli/greenbubbles) | Reuses a mature JSON query CLI and existing WeChat research. | Adds another product's CLI and schema contracts to every tool call. Still requires key initialization. | Kept as public schema evidence, not a runtime dependency. |
| Accessibility tree | Could avoid database keys for visible content. | Local probing exposed menus and windows but no chat body on WeChat 4.1.15. Cannot establish history access. | Rejected for this build. |

`wechat.py` owns account and database access, history, and exact message rehydration.
`attachments.py` owns immutable `AttachmentRef` values, row-derived metadata, attachment indexes, file checks, image decoding, and document extraction.
`server.py` adapts attachment results to MCP content blocks.
The `sources` registry in `server.py` identifies the implemented source without introducing an unused adapter hierarchy.
Source status distinguishes account selection, installation prerequisites, key problems, schema incompatibility, and readiness.

## Preserve message ordering and coverage

The stable message identity is `(conversation_id, id)`, where `id` contains the shard's relative path and `local_id`.
Different conversation tables can reuse the same `local_id` in one shard.
History and search order is descending `(create_time, sort_seq, shard_path, local_id, conversation_rank)`.
Ranks enumerate the sorted explicit selection, including chats with no rows in the time range.
The same tuple forms the cursor bound, so ties across chats and shards remain reachable.
Every discovered `message_N.db` is checked before returning history or search.
A missing key or unreadable shard fails the call.
A selected chat with no table in any shard returns `conversation_not_found`; an existing table with no in-range rows returns an empty result.

The frozen `MessageScope` owns the canonical selection and optional UTC Unix-second bounds.
The source validates exactly one selector, 1–8 distinct ids, and strict signed 64-bit integer seconds before account access.
Date predicates include `start_time` and exclude `end_time`; when both are present, start must be less than end.
Each shard query combines only selected tables with `UNION ALL` and applies one ordered row limit.
`_read_window` merges at most `n+1` raw rows per shard and retains only the newest `n+1` candidates, with `n <= 200`.
This bounds returned row counts, not SQLite disk reads, bytes, or latency.
Schema checks run once per shard per call, independently of the number of search windows.

Dated or multi-chat calls use compact base64url JSON v2 cursors with a position and SHA-256 scope digest.
The digest covers the resolved account root, sorted ids, and bounds; it prevents accidental scope reuse and is not authentication.
Unbounded singleton calls, including one-ID arrays, emit and accept the legacy conversation-plus-four-field-position format.
Legacy cursors cannot bind the local account.
History and search accept position cursors for the same scope; search queries are not cursor-bound.
Continue with the original query to preserve its coverage. Changing the query leaves newer rows unsearched for that query.
Selection order, result limit, and scan limit can change between calls.

Each reader connection opens the database with `-readonly` and sets `query_only=ON`.
Each request checks the external schema before reading its rows.
The process does not copy just the main database file, which would miss uncheckpointed WAL records.
Queries across several databases observe separate committed states.

Search scans a bounded newest-first history range and decodes text before matching.
It stops at the result or scan limit and returns the last processed message cursor.
This preserves unreturned matches for the next call.
Unsupported non-text records and decoding failures are counted explicitly.
The shared `scan_limit` counts inspected records across the whole selection, rather than allocating a separate budget per chat.
Only inspected rows are decoded; raw lookahead rows establish continuation without incrementing counters.
Search results retain the singular coverage label for singleton scopes and use `decoded_local_text_in_selected_conversations` for multiple chats.
Results echo the canonical selection and date bounds, checked shards, and per-query consistency.
`truncated` means a cursor remains; `incomplete` also includes decoding failures.

## Keep secrets outside the protocol

Keys enter through a local manifest readable only by its owner.
Each entry is validated once when loading the manifest.
Relative database paths remain beneath the selected `db_storage` directory.
Only validated hexadecimal keys appear in SQLCipher stdin.
The executable runs without a shell, and user strings become UTF-8 hexadecimal SQL literals rather than SQL fragments.
The MCP boundary validates requested limits and string sizes.

`Settings` resolves explicit setup options, environment variables, saved path references, and defaults once.
It resolves the selected SQLCipher command or path to an absolute executable path while retaining symlinks.
An invalid configured selection remains unavailable instead of falling back to another installed binary.
`WeChat` consumes this typed configuration for CLI diagnostics and MCP calls.
The private reference file contains no key bytes.
`cli.py` owns interactive setup, read-only diagnostics, and the explicit capture subprocess boundary.
Capture uses installed resource files, not Python imports, and progress output is restricted to known event names with fixed messages.
Setup requires reader readiness before saving references and rejects `manual_restore_required` even when the helper exits zero.

`ClientSpec` owns each local client's path, format, and server section.
`RegistrationResult` distinguishes added, replaced, and unchanged entries.
Codex and Grok use TOMLKit to preserve unrelated tables and comments.
JSON registration validates a comment-aware token stream and replaces only the selected value or inserts one member.
It preserves unrelated bytes and rejects duplicate keys or unsupported syntax before writing.
A conflicting AnyMessage entry requires `--replace`; existing files get a mode-0600 backup, followed by an atomic private replacement.
Registration rejects symlink destinations before backup or writing, including dangling links and `--replace` requests.
Private reference writes also reject symlink destinations.
Managed configuration targets require manual edits.
Codex registration edits its TOML directly because delegating the write to its CLI can remove unrelated settings.
Claude Code registration edits only the user-level `mcpServers` object in `~/.claude.json`.

`DoctorReport` combines source readiness with read-only prerequisite checks.
It checks `xcode-select -p` before asking LLDB for its Python module, so missing developer tools do not trigger an installation dialog.
Doctor does not read message rows or start capture.

The first-key scripts are included unchanged in wheel resources and the source distribution.
Only explicit setup consent or manual invocation starts them.
The optional HTTP server binds to `127.0.0.1` and keeps the MCP SDK's default transport protections.
It has no authentication service and does not create a public endpoint.
Remote clients require a separately managed authenticated HTTPS proxy.
Grok Bot runs on a hosted computer, so generated local Grok CLI configuration cannot connect it to Mac databases.

The MCP server never invokes a debugger, re-signs an application, or restarts WeChat.

## Try the reader with fictional data

`anymessage demo` launches a separate stdio MCP demo server and prints evidence returned by the production tools.
`anymessage demo --mcp` exposes that server to a local AI client.
`demo.py` owns the temporary encrypted databases, generated keys, fictional project and supplier conversations, and cached attachment.
It constructs `Settings` directly, so saved configuration and real account selection do not participate.
`create_server(demo_settings=...)` labels the server and status as synthetic while using the same `WeChat` reader and attachment tools.
The default server still resolves the normal settings.
Both paths share executable resolution through `resolve_sqlcipher`.
The standard status probe may read the installed application's public version metadata; demo message queries stay inside the temporary data directory.
See [the demo](demo.md) for the evidence and lifecycle.

## Bind attachments to a message

`get_messages` derives attachment descriptors from its existing message rows.
It does not open an attachment index or inspect the filesystem.
File app messages use bounded XML decoding, preferring `compress_content` when present and removing a group sender prefix only for attachment XML.
The history and search text representation does not change.

Attachment calls validate the shard and local id, open only that shard, and re-read the exact row under the requested conversation table.
Attachment ids include the conversation, message identity, server id, metadata, and variant.
They grant no path access outside that rehydrated message.
File resolution matches the message's name, digest, and size in `file_hardlink_info_v4` and resolves `dir1` and `dir2` through `dir2id`.
It never searches a monthly directory by filename alone.
Every file read verifies actual byte size and MD5 before returning content.
MD5 identifies the cached WeChat file. It is not an authentication mechanism.

Image identities come from the message's packed protobuf metadata or a resource-index row bound to the conversation, local id, and server id.
Original, HD, and thumbnail cache paths are separate variants.
No missing original is replaced silently by a thumbnail.
A supported V2 cache uses an existing account-matched image configuration and strict AES padding validation, then Pillow verifies the image.
The account identity and derived key remain in memory and never enter MCP metadata, saved settings, or diagnostics.

Cache paths must resolve within the selected account's `msg` directory, including symlink targets.
Image cache and image-key configuration paths also reject symlink redirects between variants or accounts.
Availability checks report cache state without claiming content verification.
Document extraction runs in a subprocess with a 20-second timeout and returns fixed failure states instead of parser diagnostics.
It writes no plaintext archive, executes no macros or formulas, and follows no external relationship or download URL.
Office extraction spends its character budget while reading paragraphs and cells, so repeated shared strings cannot expand the output beyond the cap.
Text output has a separate extraction cap and character pagination.
See [the exact limits](attachments.md).
