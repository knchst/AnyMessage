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

## Attachment records and local caches

The direct file app-message shape is `appmsg/type=6` with a title, `appattach/totallen`, and `appattach/md5` or a parent `appmsg/md5` fallback.
The nested digest takes precedence when both exist.
Group XML can have a sender prefix before the document, and `compress_content` takes precedence over the body column.
Nested forwarded app messages are not treated as attachments of the outer message.

On the tested schema, `hardlink/hardlink.db` contains `file_hardlink_info_v4(md5, file_name, file_size, dir1, dir2, ...)`.
The directory ids refer to `dir2id.rowid`, whose `username` values form cache path components beneath `msg/file`.
Private read-only checks matched file messages to this index and verified the cached bytes' size and digest.
Those checks establish the sampled format, not universal compatibility with other WeChat builds.

Image message `packed_info_data` field 3, nested field 4, contains a hexadecimal cache basename on the tested schema.
The fallback `MessageResourceInfo.packed_info` field 2, nested field 1, agrees for the sampled records.
The fallback is joined through `ChatName2Id` and constrained by message local and server ids.
Image paths use `msg/attach/<MD5 conversation>/<local YYYY-MM>/Img/<basename>.dat`, `_h.dat`, or `_t.dat`.
The sampled available image was a V2 JPEG thumbnail. Original and HD cache availability is independent.

The public [wx-cli V2 format description and tests](https://github.com/pandorafuture/wx-cli/blob/2abe708f55bfe135539a385df856fdc58f97fc74/crates/wx-media/src/dat.rs) and [account key derivation](https://github.com/pandorafuture/wx-cli/blob/2abe708f55bfe135539a385df856fdc58f97fc74/crates/wx-media/src/key.rs) informed an independent format implementation.
No source from that GPL project is bundled or translated into AnyMessage.
The 15-byte V2 header declares AES and XOR regions.
AnyMessage validates region bounds and PKCS#7 padding, keeps the middle region, and infers the JPEG XOR byte only when both footer bytes agree.
An existing suffix-matched `app_data/radium/ilink` configuration supplies the account identity for image key derivation.
A private in-memory probe independently decoded and verified the sampled thumbnail with Pillow.
This does not establish V1, legacy XOR, WXGF, or arbitrary encrypted-image support.

Document reading uses [pypdf text extraction](https://pypdf.readthedocs.io/en/stable/user/extract-text.html) and bounded Office ZIP/XML parsing.
PDF extraction can require substantially more memory than the source bytes, so source-size limits and a worker timeout are not a strict memory guarantee.
Image validation uses [Pillow image verification](https://pillow.readthedocs.io/en/stable/reference/Image.html).
Private messages, attachment bytes, keys, and local validation locators remain outside the repository and distributions.
