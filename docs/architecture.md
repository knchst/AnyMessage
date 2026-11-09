# AnyMessage architecture

An agent calls a small MCP tool through stdio.
The tool selects the registered WeChat source and returns ordinary JSON results.
The WeChat source owns account discovery, key validation, schema checks, SQLCipher queries, message decoding, and cross-shard ordering.
SQLCipher receives fixed read-only SQL and the selected database key through stdin.

## Choose direct local database reads

| Candidate | Benefit | Constraint | Decision |
| --- | --- | --- | --- |
| Direct SQLCipher reads | Own stable MCP results, bounded queries, and explicit shard coverage. Read committed local WAL records. | Requires verified per-database keys and runtime schema checks. | Implemented. |
| [GreenBubbles CLI wrapper](https://github.com/bojieli/greenbubbles) | Reuses a mature JSON query CLI and existing WeChat research. | Adds another product's CLI and schema contracts to every tool call. Still requires key initialization. | Kept as public schema evidence, not a runtime dependency. |
| Accessibility tree | Could avoid database keys for visible content. | Local probing exposed menus and windows but no chat body on WeChat 4.1.15. Cannot establish history access. | Rejected for this build. |

There is one owner for the current database format in `wechat.py`.
The `sources` registry in `server.py` identifies the implemented source without introducing an unused adapter hierarchy.
Source status distinguishes account selection, installation prerequisites, key problems, schema incompatibility, and readiness.

## Preserve message ordering and coverage

The stable message identity is the message shard's relative path plus its `local_id`.
History order is descending `(create_time, sort_seq, shard_path, local_id)`.
The same tuple forms the cursor bound, so equal timestamps and local ids in different shards remain reachable.
Every discovered `message_N.db` is checked before returning history.
A missing key or unreadable shard fails the call.

Each SQLCipher connection opens the database with `-readonly` and sets `query_only=ON`.
Each request checks the external schema before reading its rows.
The process does not copy just the main database file, which would miss uncheckpointed WAL records.
Queries across several databases observe separate committed states.

Search scans a bounded newest-first history range and decodes text before matching.
It stops at the result or scan limit and returns the last processed message cursor.
This preserves unreturned matches for the next call.
Unsupported non-text records and decoding failures are counted explicitly.

## Keep secrets outside the protocol

Keys enter through a local manifest readable only by its owner.
Each entry is validated once when loading the manifest.
Relative database paths remain beneath the selected `db_storage` directory.
Only validated hexadecimal keys appear in SQLCipher stdin.
The executable runs without a shell, and user strings become UTF-8 hexadecimal SQL literals rather than SQL fragments.
The MCP boundary validates requested limits and string sizes.

The optional first-key scripts are separate manual tooling.
The MCP server never invokes a debugger, re-signs an application, or restarts WeChat.
