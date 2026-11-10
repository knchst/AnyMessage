# Read a Telegram Desktop JSON export

AnyMessage 0.9.0 reads messages and classic local attachments from one explicitly selected Telegram Desktop JSON export and generates client entries for it.
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

Text and context require 0.7.0 or later; attachment reading requires 0.8.0 or later.
The final command starts a stdio MCP server, which waits for a client rather than printing a conversation report.
One process serves one selected file. Restart it to load a changed file or choose another export.
An invalid explicit export fails startup with exit code 2 and a structural error on stderr.
It never falls back to WeChat.
`setup`, `doctor`, legacy `--status`, and `mcp add` remain WeChat commands.
`mcp add` rejects an export selection. Use `mcp config --telegram-export FILE` and merge its output manually instead.

The file must be a regular UTF-8 JSON file, optionally with a BOM, no larger than 16 MiB.
The selected JSON filename must not be a symlink; pass the target's real filename. Parent directory aliases are resolved once.
Keep exported media beside the JSON in the original relative folder layout.
For a larger file, export one chat or a shorter period.
The entire JSON file is decoded in memory at startup, and its decoded tree can use substantially more memory than the file size.
Unrelated account sections pass through memory during parsing and are then discarded.
Only normalized chat records remain, with no retained file handle or plaintext database created by AnyMessage.

## Connect your AI

With AnyMessage 0.9.0 or later, generate a separate entry for the selected export:

```sh
anymessage mcp config --telegram-export '東京 chat/result.json' --client cursor --format json
anymessage mcp --telegram-export '東京 chat/result.json' config --client codex --format toml
```

Both flag positions select the same export. Specify the format explicitly; the client selection does not infer JSON or TOML.
Copy the generated entry into the existing client configuration without replacing unrelated settings.
The entry is named `telegram-export` so it can coexist with `AnyMessage` for WeChat. For several exports, choose a distinct entry name for each.
`--server-only` omits the JSON `mcpServers` or TOML `mcp_servers` wrapper.
`--command 'relative or absolute/anymessage'` selects another executable; otherwise generation retains the invoked installation.
Cursor adds its required `type: "stdio"`; generic entries and the other local clients retain their existing shape.
Local Grok CLI is supported. `--client grokbot` retains its remote-host guidance and prints no local entry.

Generation makes the selected filename absolute from the current directory and expands `~`, without resolving aliases or `..`.
It does not read or validate the export, read WeChat settings, or change client files.
Missing, malformed, or symlink exports can produce entries; the launched server applies the validation above and fails without a WeChat fallback.
Select the export only once. Telegram configuration rejects HTTP transport or an explicitly supplied port; use the separate HTTP launch below.
`mcp add` does not register exports. Configuration without an export keeps the existing WeChat entry.

For example, a generic JSON entry looks like this:

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

Download the [fictional River export](../examples/telegram-fictional.json) and [specification TXT](../examples/river-spec-v2.txt) into the same folder. Both contain only fictional data.

```sh
curl --fail --location https://raw.githubusercontent.com/knchst/AnyMessage/main/examples/telegram-fictional.json --output telegram-fictional.json
curl --fail --location https://raw.githubusercontent.com/knchst/AnyMessage/main/examples/river-spec-v2.txt --output river-spec-v2.txt
```

Use that file in the client configuration above.
Ask your AI to use only this MCP connection:

> Call status and list_conversations. Search for "proposal" in the River project conversation. Pass that hit's conversation_id and id to get_message_context with before=1 and after=2. Cite the proposal and its approval separately. Then expand the same hit with after=4 to find specification message 6. List its attachments and read the returned file ID. Report the open thickness question, whether the drawing can be read, and whether the supplier confirmed November 26.

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
Drawing message `5` has a searchable caption and an attachment ID, but `state="attachment_missing"` because that PDF is deliberately absent.
Expanding the same hit with `after=4` includes message `6`; its specification TXT is readable and says the thickness awaits supplier confirmation.
Pass the ID returned by `list_attachments` for message `6` to `read_attachment`. Successful text is returned as native MCP text with `content_verified=false`.

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
            expanded = await client.call_tool("get_message_context", {"conversation_id": hit["conversation_id"], "message_id": hit["id"], "before": 0, "after": 4})
            spec = expanded.structuredContent["items"][-1]
            listing = await client.call_tool("list_attachments", {"conversation_id": spec["conversation_id"], "message_id": spec["id"]})
            attachment = await client.call_tool("read_attachment", {"conversation_id": spec["conversation_id"], "message_id": spec["id"], "attachment_id": listing.structuredContent["items"][0]["id"]})
            assert not attachment.isError and "Battery: 500 mAh" in attachment.content[0].text
            assert not attachment.structuredContent["content_verified"]
            print(attachment.content[0].text)
            print(json.dumps(attachment.structuredContent, ensure_ascii=False, indent=2))

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

Classic dated messages expose independent descriptors for declared `photo`, `file`, `thumbnail`, and `contact_vcard` fields.
A vCard is read as ordinary text or original bytes; its contacts are not parsed.
`file_name` and `mime_type` are display declarations, never path or decoder authority.
Opaque rich-message bodies and unsupported rows expose no IDs, including any top-level media sibling. A marker-only `media_type` remains unsupported.
Search covers only message text and captions, not attachment contents.

History, search, and context expose the same IDs without accessing media.
`attachment_state="available"` means descriptors exist; inspect each descriptor's `state` for availability.
A safe relative locator starts at `not_checked`. Omission, unsafe-path, and unsupported-locator states are already known from JSON.
No recognized media gives `attachment_state="none"` and an empty list.
`list_attachments` validates the exact pair and checks current filesystem metadata, without reading file contents.
Its `metadata_state` is `available` when descriptors exist, `unsupported` for opaque or marker-only media, and `none` for no media.
`media_declared` is true or false for classic rows and null for opaque rich bodies.

An ID is the first 32 lowercase hex characters of SHA-256 over the UTF-8 canonical JSON array `["telegram-export-attachment-v1", snapshot_sha256, qualified_chat_id, canonical_message_id, variant]`.
Serialization uses `ensure_ascii=False` and compact separators. The canonical message ID is its decimal string.
The ID is a descriptor identity, not a file hash. It binds every field through the JSON snapshot and resolves only within its exact message pair and variant.
Byte-identical reloads preserve IDs. Changed JSON bytes invalidate old IDs after reload; copied identical JSON observes media in the newly selected folder.
Unknown IDs return `attachment_not_found` before touching media, including on messages without descriptors.

Paths must be nonempty relative POSIX paths under the selected JSON's resolved parent directory, with no empty, dot, or dot-dot components.
Absolute paths, URLs, Windows drives, backslashes, controls, and invalid Unicode are refused.
The reader performs no path expansion, percent decoding, basename search, guessed thumbnail lookup, or external fetch.
Every component uses nonfollowing metadata checks. Symlinks, including internal or broken ones, and nonregular final objects are refused before opening.
The folder must stay under your control during each read; there is no hostile concurrent-replacement guarantee.
Originals and thumbnails have separate IDs and availability, and a missing original never returns thumbnail bytes.

| State | Meaning |
| --- | --- |
| `not_checked` | JSON describes a safe relative locator; media has not been inspected. |
| `available` | Listing found a regular local file; bytes have not been verified. |
| `not_exported` | The classic writer emitted a known omission warning; `omission_reason` is `unavailable`, `file_type`, or `file_size`. No filesystem access occurs. |
| `attachment_missing` | A referenced local component is absent. |
| `attachment_path_invalid` | The locator is unsafe, a component is a symlink, an intermediate is not a directory, or the final object is not regular. |
| `attachment_access_denied` | Local permissions or a read error prevent access. |
| `unsupported` | A locator representation or warning variant is unsupported. Marker-only and opaque messages have no descriptors. |
| `attachment_size_mismatch` | Complete observed bytes differ from a valid exported size, including zero. No content is returned. |
| `attachment_too_large` | The source or requested output exceeds its byte limit. No partial bytes are returned. |

Only the writer's three complete English warnings are recognized as omissions. Other `(File ...` warning forms remain unsupported nonlocators.
Optional exported sizes accept nonnegative JSON integers; malformed values, including booleans, become unknown.
A complete read within the source cap reports `local_size` and `local_sha256`, including on a declared-size mismatch.
Oversized prefix reads report neither a complete local size nor a checksum.
`declared_size` and `declared_mime` remain separate from those observations. MIME routing uses byte signatures, not the declaration or extension.
Telegram supplies no trusted digest for these classic fields, so every result has `content_verified=false`, even with a matching size, valid image, or successful extraction.
`snapshot_consistency="immutable_export"` refers only to loaded JSON. Status and attachment results use `media_consistency="current_local_files"`.
Media is reopened on each read; a changed file can produce new text or a new local hash without changing its descriptor ID.

`read_attachment` returns supported UTF text, PDF, DOCX, XLSX, and PPTX text through the existing bounded document worker.
Text uses Unicode-character `offset` and `limit`, `total_characters`, `next_offset`, `truncated`, and `extraction_truncated`.
Images with supported signatures return native MCP ImageContent after image verification. Photo and thumbnail variants require valid image bytes.
Set `as_binary=true` to return complete original bytes as an embedded MCP resource, including for images and unsupported audio or video formats.
Images and binary output require offset zero. Unrecognized binary text extraction returns `format_unsupported`; parser failures retain the [existing attachment states](attachments.md).
Failed reads of a listed attachment return metadata, without attachment text, image, or blob content.
Request-validation and unknown-ID failures use standard MCP errors.

Limits are 16 MiB source bytes, 8 MiB image or binary output, 25 million image pixels, and a 20-second document worker timeout.
Document extraction retains its 2,000,000-character cap, XML, ZIP, and PDF limits described in [the attachment reference](attachments.md#limits-and-states).
These bounds are not an operating-system memory quota.
There is no OCR, audio or video decoding, macro execution, formula evaluation, ordinary archive expansion, download, or media write.
