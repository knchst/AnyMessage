# WeChat database research

The first adapter targets the WeChat 4.x local database layout and was exercised with WeChat 4.1.15 on Apple Silicon.
Runtime schema checks remain necessary because the client and its database format can change independently of AnyMessage.
Local account inventories, message samples, key material, and machine-specific setup records are not distributed.

## Application inspection

[REA](https://github.com/morluto/rea) was used to inspect the application's Mach-O structure and code-signing metadata.
The installed 4.1.15 build had a small executable loader, linked through `libwxld.dylib`, with the larger native core in `Contents/Resources/wechat.dylib`.
The official application used hardened runtime and did not advertise `get-task-allow`.
These static observations explain the investigation route; they do not establish a stable binary hooking interface.

Accessibility inspection exposed window and menu controls without a usable conversation-body tree on the tested build.
The adapter therefore uses database reads, not UI text extraction.
REA is a research tool, not an AnyMessage runtime dependency.

## Reader contract

The public [GreenBubbles implementation](https://github.com/bojieli/greenbubbles/blob/main/Native/GreenBubbles/src/live_query.rs) and [database-format notes](https://github.com/bojieli/greenbubbles/blob/main/docs/WECHAT_DATABASE_FORMAT.md) informed the initial schema contract.
The required columns were subsequently checked against the tested client's local schemas.

- `session/session.db` contains `SessionTable(username, sort_timestamp, summary)`.
- `contact/contact.db` contains `contact(username, ...)`; display names prefer `remark`, `nick_name`, then `alias` when available.
- Conversation tables use `Msg_` followed by the conversation username's MD5 digest, distributed across `message/message_N.db` shards.
- Message columns include `local_id`, `server_id`, `local_type`, `sort_seq`, `real_sender_id`, `create_time`, and `message_content`.
- `real_sender_id` joins to `Name2Id.rowid`; `Name2Id.user_name` identifies the sender.
- Supported text bodies are UTF-8 or Zstandard-compressed bytes.

The reader verifies the table and column contract before querying rows.
Synthetic tests cover schema mismatch, missing sender lookup, compressed text, and multi-shard pagination.
Unknown message types are returned with an unsupported content state rather than invented text.

## Key acquisition

The separate [WeChat 4.1.15 research gist](https://gist.github.com/acmerfight/0a01249ef72970d07a2603dbb629e80f) describes the temporary-copy LLDB capture procedure.
It reports failed ordinary-user and root read-port probes against the officially signed application on its test machine.
That is evidence about those probes, not proof that every possible approach is unavailable.

The pinned optional scripts in this repository were exercised separately from the reader.
They match candidates to database salts and verify the encrypted first-page HMAC before writing keys.
Partial key coverage is possible; the MCP reader must still verify every supported shard it uses.
Read [the setup guide](wechat-setup.md) for the manual procedure and its client-session effects.
