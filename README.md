# AnyMessage

Read local macOS messages from an AI agent through MCP.
The first supported source is WeChat 4.x.

AnyMessage provides a read-only stdio server for conversation listing, text history retrieval, and conversation-scoped search.
It reads local SQLCipher databases and committed WAL records without exporting a separate plaintext message archive.

This project is an early implementation.
The reader and optional key initialization have been exercised with WeChat 4.1.15 on Apple Silicon.
Other versions require validation against their local schemas.
There is no message sending, deletion, media decoding, or cloud-history synchronization.

## Install and set up

```sh
brew install knchst/tap/anymessage
anymessage setup
```

Homebrew installs Python and SQLCipher with AnyMessage.
Setup reuses existing valid keys and registers Codex by default.
It checks account selection and every supported database before saving configuration references.
When several accounts exist, setup asks you to choose one.
Restart or reload Codex's MCP connection after registration, then call `status`.

For an existing manifest or another local client:

```sh
anymessage setup --keys-file "/absolute/path/private/manifest.json" --client cursor
anymessage mcp add claude-desktop
anymessage mcp add claude-code
anymessage mcp add grok
anymessage doctor
```

The built-in client names are `codex`, `cursor`, `claude-desktop`, `claude-code`, and `grok`.
Repeat `--client` during setup to register several clients, or use `--no-register`.
Registration preserves unrelated settings, TOML comments, and JSON comments.
An existing different AnyMessage entry requires `--replace` and gets a private backup before replacement.
An identical entry is left untouched.
`--replace` also permits setup to replace saved configuration references; it never overwrites key manifests.

If no usable keys exist, read [the first-time setup guide](docs/wechat-setup.md).
Interactive setup asks for an existing manifest before offering temporary-copy capture.
Capture uses a temporary re-signed WeChat client and may require phone approval or login.
It requires native Apple Silicon macOS, Xcode command line tools with LLDB, and a normally quit original client.
Normal MCP use and `doctor` never invoke capture or change the WeChat application.

For scripts, pass `--non-interactive` and an existing `--keys-file`.
First-time capture additionally requires `--acknowledge-debug-copy`.
`doctor --json` reports readiness and prerequisites without message content.
`doctor` exits 0 when the reader is ready and 1 otherwise.
Setup and registration failures exit 2 with guidance on stderr.
Legacy `--status` prints the source status and retains its successful inspection exit code even when the source is not ready.

## Connect another MCP client

Any client that can launch a local stdio MCP server can use AnyMessage on the same Mac:

```sh
anymessage mcp config --format json
anymessage mcp config --format toml
anymessage mcp config --format json --server-only
anymessage mcp
```

Copy the generated entry into the client's configuration.
`--server-only` prints the server object for forms that provide their own wrapper.
Use `--client cursor` to include Cursor's explicit `type = stdio` equivalent in JSON.
`--command /absolute/path/bin/anymessage` selects another installation explicitly.
Registration retains the invoked executable's absolute symlink path, so a Homebrew upgrade does not pin a versioned Cellar path.
See [the JSON example](examples/mcp.json) and [the Codex example](examples/codex-mcp.toml).
No arguments also starts the stdio server for existing configurations.

For a same-Mac client that requires Streamable HTTP, start a separate server process:

```sh
anymessage mcp --transport streamable-http --port 8765
```

Connect that client to `http://127.0.0.1:8765/mcp` while the process runs.
The server binds only to `127.0.0.1`; setup does not start a background listener.
Loopback HTTP has no application authentication and is reachable by other local processes.
It is intended for clients on your Mac.

Grok Bot is separate from the local Grok CLI.
Grok Bot runs MCP tools on Grok Bot's computer, so a local command or loopback URL there cannot read this Mac's databases.
Its HTTP form needs a user-managed HTTPS proxy that authenticates requests and forwards to AnyMessage's loopback server.
AnyMessage does not create a tunnel, public listener, or authentication service.
Do not expose the unauthenticated loopback service directly to the internet.
Remote Grok Bot connectivity has not been verified.
`--client grokbot` reports this boundary instead of generating a misleading local entry.

## Install from a checkout

```sh
brew install sqlcipher
uv sync --locked
uv run anymessage setup --keys-file "/absolute/path/private/manifest.json"
```

Checkout development needs Python 3.11 or later and [uv](https://docs.astral.sh/uv/).
The repository does not assume a published PyPI package.

All four tools advertise read-only behavior.
Call `status` first, then `list_conversations` to obtain a conversation id.

| Tool | Inputs | Result |
| --- | --- | --- |
| `status` | None | Source installation, account selection, database inventory, keys and schema readiness. No message bodies. |
| `list_conversations` | `limit` 1–200, `offset` 0–100000 | Newest-first local conversations, display names, summaries, and `next_offset`. |
| `get_messages` | `conversation_id`, `limit` 1–200, optional `cursor` | Newest-first local messages across supported message shards, with `next_cursor`. |
| `search_messages` | Literal `query`, `conversation_id`, `limit` 1–200, `scan_limit` 1–10000, optional `cursor` | Decoded text matches, scanned count, coverage and continuation. |

Reuse returned cursors without editing them.
Message identities include their shard and local id, so equal local ids in different shards remain distinct.
Search is case-insensitive literal matching after decoding; SQL wildcard characters remain literal.
`truncated=true` means more records remain beyond the current result or scan limit.
`incomplete=true` also covers text decoding failures.
Unsupported non-text records are counted separately.

## Configuration

Setup stores path and account references in `~/Library/Application Support/AnyMessage/config.json`, with mode 0600.
Key bytes stay in their private manifest.
Settings resolve in this order: explicit setup options, environment variables, saved references, then defaults.
`ANYMESSAGE_CONFIG_FILE` selects another reference file; generated client entries carry that override when set.
Setup also recognizes an existing `wechat/keys.json` next to the reference file when no key path is configured.
Changing environment variables in a terminal does not change an already running MCP client.
Run setup to save references or set the client's own environment explicitly.

| Variable | Purpose |
| --- | --- |
| `ANYMESSAGE_CONFIG_FILE` | Optional path to the saved reference file. |
| `ANYMESSAGE_KEYS_FILE` | Private JSON manifest containing the selected account's per-database raw keys. |
| `ANYMESSAGE_ACCOUNT` | Opaque account id returned by `status`, required when several accounts are discovered. |
| `ANYMESSAGE_DATA_DIR` | Optional `xwechat_files` directory, account directory containing `db_storage`, or `db_storage` itself. |
| `ANYMESSAGE_SQLCIPHER` | Optional SQLCipher executable path; otherwise use `PATH` or standard Homebrew locations. |

By default, account discovery uses `~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files`.
Keys are supplied through a local file, not MCP arguments or command-line key values.
The manifest accepts this shape, with paths relative to `db_storage`:

```json
{
  "keys": {
    "session/session.db": {"enc_key": "<64 hexadecimal characters>", "salt": "<32 hexadecimal characters>"},
    "contact/contact.db": {"enc_key": "<64 hexadecimal characters>"},
    "message/message_0.db": {"enc_key": "<64 hexadecimal characters>"}
  }
}
```

Each encrypted message shard needs its matching key entry.
The optional `salt` must match that database's first 16 bytes.
An unwrapped database mapping is also accepted.
Use an owner-only directory and run `chmod 600` on the manifest.
Never commit the manifest or real database files.

## Limits

Only `message/message_N.db` message shards are supported.
Business-account shards, media retrieval, group sender-prefix normalization, rich-message XML extraction, sending, and global history indexing are not implemented.
WeChat's retention and synchronization state determine which records exist locally.
Queries spanning databases do not form one atomic snapshot.
A missing key or unreadable supported shard fails history retrieval instead of silently returning partial history.

The reader opens SQLCipher with `-readonly` and `query_only=ON`.
SQLite can still use ordinary shared-memory and read-lock coordination for an active WAL database.
The optional bootstrap's effects are separate from the read-only server and are described in its setup guide.

## Develop and verify

```sh
uv sync --locked --group dev
uv run pytest -q
python3 scripts/check_publication.py
uv build
python3 scripts/check_publication.py --archive dist/*.whl dist/*.tar.gz
```

The default suite uses only synthetic data, including CLI onboarding, configuration preservation, stdio and loopback HTTP round trips, real SQLCipher-encrypted fixtures, compressed text, multiple shards, pagination ties, literal search, committed WAL reads, rejected writes, and official MCP client round trips.
See [CONTRIBUTING.md](CONTRIBUTING.md), [the architecture](docs/architecture.md), and [the database research notes](docs/wechat-research.md).

## License

AnyMessage is [MIT licensed](LICENSE).
The optional vendored bootstrap tools retain their original MIT notice; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
WeChat and SQLCipher are installed separately and are not distributed by this repository.
