# Read a Telegram Desktop JSON export

AnyMessage 0.7.0 reads text from one explicitly selected Telegram Desktop JSON file.
It supports conversation listing, dated history, selected-chat search, and exact-message context through the existing seven MCP tools.
It does not need WeChat, SQLCipher, account keys, or saved WeChat configuration.
This workflow has been verified on macOS through the MCP SDK. Other platforms and desktop-host export configuration have not been verified.

## Export and start the server

In Telegram Desktop, open a chat and choose **Export chat history** from its menu.
Select JSON as the output format, then select the exported JSON file with AnyMessage.
A full-account export containing `chats.list`, `left_chats.list`, or both also works.
See Telegram's [export announcement](https://telegram.org/blog/export-and-more) and [JSON schema](https://core.telegram.org/import-export).

Install the latest source with [uv](https://docs.astral.sh/uv/getting-started/installation/):

```sh
uv tool install --from git+https://github.com/knchst/AnyMessage anymessage
anymessage --version
anymessage mcp --telegram-export "/absolute/path/to/result.json"
```

The version must be 0.7.0 or later.
The final command starts a stdio MCP server, which waits for a client rather than printing a conversation report.
One process serves one selected file. Restart it to load a changed file or choose another export.
An invalid explicit export fails startup with exit code 2 and a structural error on stderr.
It never falls back to WeChat.
`setup`, `doctor`, legacy `--status`, and `mcp add/config` remain WeChat commands.
Combining `--telegram-export` with `mcp add/config` is rejected so registration cannot silently lose the selected file.

The file must be a regular UTF-8 JSON file, optionally with a BOM, no larger than 16 MiB.
For a larger file, export one chat or a shorter period.
The entire JSON file is decoded in memory at startup, and its decoded tree can use substantially more memory than the file size.
Unrelated account sections pass through memory during parsing and are then discarded.
Only normalized chat records remain, with no retained file handle or plaintext database created by AnyMessage.

## Connect your AI

Find your executable with `command -v anymessage` and replace both absolute paths below.
Add a separate entry to a client that accepts JSON MCP configuration:

```json
{
  "mcpServers": {
    "telegram-export": {
      "command": "/absolute/path/to/anymessage",
      "args": ["mcp", "--telegram-export", "/absolute/path/to/result.json"]
    }
  }
}
```

For Codex, add this entry to its MCP TOML configuration:

```toml
[mcp_servers.telegram-export]
command = "/absolute/path/to/anymessage"
args = ["mcp", "--telegram-export", "/absolute/path/to/result.json"]
```

Restart or reconnect the client's MCP server, then call `status` and `list_conversations`.
The source must be `telegram_export`, with `state="ready"` and `snapshot_consistency="immutable_export"`.
Keep a separate default WeChat entry if you use both sources.
The MCPB bundle's default installed entry continues to launch WeChat; importing the bundle does not select a Telegram export.
These configurations describe the launch contract. They do not claim installation testing in a particular desktop host.

For a same-Mac client that requires HTTP, run a separate process:

```sh
anymessage mcp --telegram-export "/absolute/path/to/result.json" --transport streamable-http --port 8765
```

Connect to `http://127.0.0.1:8765/mcp` while that process runs.
The listener has no application authentication and is reachable by other local processes.
See [the security policy](../SECURITY.md) before configuring remote access.

## Follow a fictional search hit to its approval

Download the [fictional River export](../examples/telegram-fictional.json). It contains no real account data or media bytes.

```sh
curl --fail --location https://raw.githubusercontent.com/knchst/AnyMessage/main/examples/telegram-fictional.json --output telegram-fictional.json
```

Use that file in the client configuration above.
Ask your AI to use only this MCP connection:

> Call status and list_conversations. Search for "proposal" in the River project conversation. Pass that hit's conversation_id and id to get_message_context with before=1 and after=2. Cite the proposal and its approval separately. Report whether the drawing can be read and whether the supplier confirmed November 26.

The actual MCP tool calls are:

```json
{"name":"status","arguments":{}}
{"name":"list_conversations","arguments":{}}
{"name":"search_messages","arguments":{"query":"proposal","conversation_id":"telegram:private_group:42"}}
{"name":"get_message_context","arguments":{"conversation_id":"telegram:private_group:42","message_id":"2","before":1,"after":2}}
{"name":"list_attachments","arguments":{"conversation_id":"telegram:private_group:42","message_id":"5"}}
```

The context contains IDs `1`, `2`, `3`, and `4` in chronological order, with `anchor_index=1`.
ID `2` proposes the 500 mAh battery. ID `4` approves it and retains the quantity of 50.
ID `3` is a service record without invented text and still occupies a context position.
The supplier conversation has a different qualified ID, `telegram:personal_chat:42`, even though its numeric chat ID and message ID overlap the project chat.
Its ID `2` says the date remains unconfirmed.
Drawing message `5` has a searchable caption, but its media listing reports `metadata_state="unsupported"` with no attachment IDs.

To run the same evidence check directly through the MCP SDK, replace the executable path and run:

```sh
uv run --no-project --with mcp==1.30.0 python - /absolute/path/to/anymessage "$PWD/telegram-fictional.json" <<'PY'
import asyncio
import json
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(command=sys.argv[1], args=["mcp", "--telegram-export", sys.argv[2]])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            status = await client.call_tool("status", {})
            assert status.structuredContent["sources"][0]["source"] == "telegram_export"
            search = await client.call_tool("search_messages", {"query": "proposal", "conversation_id": "telegram:private_group:42"})
            hit = search.structuredContent["items"][0]
            context = await client.call_tool("get_message_context", {"conversation_id": hit["conversation_id"], "message_id": hit["id"], "before": 1, "after": 2})
            assert [row["id"] for row in context.structuredContent["items"]] == ["1", "2", "3", "4"]
            print(json.dumps(context.structuredContent, ensure_ascii=False, indent=2))

asyncio.run(main())
PY
```

## Source contract

Conversation IDs use `telegram:<chat-type>:<signed-decimal-id>`.
Supported types are `saved_messages`, `replies`, `verification_codes`, `personal_chat`, `bot_chat`, `private_group`, `private_supergroup`, `public_supergroup`, `private_channel`, and `public_channel`.
The writer's null or empty unknown type uses `unknown`. A new nonempty type fails startup instead of guessing its identity.
All chat and message IDs must be signed 64-bit JSON integers, never strings, booleans, or floats.
Returned message `id` and `server_id` are the same canonical decimal chat-local number, not globally unique server IDs.
Cite a hit with `source`, `conversation_id`, and `id`.
Public message `type` is Telegram's source-specific string `message`, `service`, or `unsupported`; WeChat retains integer types.
`chat_type` is returned separately on conversation items, and only group or supergroup types set `is_group=true`.

Ordinary messages require `text` as a string or flat array of strings and `{type, text}` entities.
The parts are concatenated exactly once, with no added whitespace or indexed entity metadata.
If `text_entities` also exists, it is ignored. A body supplied only as `text_entities` is not supported.
`date_unixtime` must be a signed 64-bit decimal Unix-second string; timezone-free display dates are ignored.
Normal sender fields come from `from_id` and `from`. Service senders come only from `actor_id` and `actor`.
Missing sender values stay null. Nonempty literal exported service text and media captions are searchable.
Service actions, poll questions, locations, contacts, venues, games, invoices, todo lists, giveaways, and rich-message blocks are not rendered or searched.
Empty plain text remains an empty string; unsupported bodies return `text=null` and `content_state="unsupported_type"`.
Current Telegram Desktop can emit opaque `rich_message` instead of classic text. These dated rows have `unsupported_reason="rich_message"` and consume context positions and scan budget.

History and search order dated records by `(timestamp, message_number, qualified_chat_id)` descending.
Context is chronological and contains the exact anchor once.
`before` and `after` count dated records, including unsupported records.
The remaining-record flags describe dated chronology even when either requested count is zero.
Compact `{id, type:"unsupported"}` records without timestamps cannot be placed in chronology.
They contribute to `undated_unsupported_count` in status and selected history, context, and search coverage, even under date bounds.
Their exact context lookup returns `message_undated`.

History and search support inclusive `start_time` and exclusive `end_time` with the same bounds as WeChat.
Search requires exactly one selector, with at most eight distinct chats, and casefolds literal text only as it is scanned.
One `scan_limit` covers all selected chats; unrelated chats are not scanned.
Unsupported dated records consume a scan unit. `decode_failures=0` because startup validates the source before serving any results.
One uninterpreted lookahead row establishes continuation without increasing counters.
The next cursor marks the last inspected record, including a non-match, so an empty page can continue.
`truncated` means a continuation remains. `incomplete` means continuation remains or the selected chats contain undated unsupported rows.
Other unsupported bodies are disclosed by counts and coverage even when `incomplete=false`.

Opaque `tg1` cursors bind exact accepted file bytes, canonical selection, both bounds, and the original query or history mode.
Changing selection order, `limit`, or `scan_limit` is safe. Changing query, bounds, source bytes, or history/search mode rejects the cursor.
Each token resolves to an actual dated record within the selected scope.
Byte-identical reloads intentionally accept old cursors; even unrelated byte changes invalidate them after reload.
The running reader remains unchanged if the original file changes or disappears.

Conversation, history, and context coverage is `telegram_desktop_json_export`; search coverage is `telegram_desktop_json_export_text`.
`snapshot_consistency="immutable_export"` describes the accepted file bytes, not an instant of Telegram's live state.
`shards_checked=0` means no WeChat shards were consulted.
Status counts distinguish dated messages from undated unsupported omissions and expose the input cap and excluded rich-message bodies.
No full selected path or unrelated account information is returned.
Exports can be time-filtered, and Telegram's schema states that public group and channel exports contain only the exporter's messages.
An exhausted search covers only searchable dated exported bodies, never complete cloud history.

## Media

Every exported message returns `attachments=[]`.
`attachment_state="unsupported"` indicates a recognized media marker or opaque rich body.
`attachment_state="none"` means no recognized media marker, not a verified filesystem state.
Recognized markers include `photo`, `file`, `thumbnail`, `contact_vcard`, and `media_type`; their values are not exposed or followed.

`list_attachments` first validates the exact message pair, then returns `items=[]`, `metadata_state="unsupported"`, and `media_declared`.
That indicator is true or false for classic records and null for opaque rich content.
`read_attachment` validates the same pair and returns a metadata-only MCP error with `state="unsupported"` and `content_verified=false`.
The supplied attachment ID is not echoed as a found descriptor.
Neither call resolves paths, checks file existence, reads local media, downloads content, or performs OCR.
Actual media support is tracked separately in [Issue #29](https://github.com/knchst/AnyMessage/issues/29).
