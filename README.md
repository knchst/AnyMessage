# AnyMessage

Read local macOS messages from an AI agent through MCP.
The first supported source is WeChat 4.x.

AnyMessage provides a read-only stdio server for conversation listing, text history retrieval, and conversation-scoped search.
It reads local SQLCipher databases and committed WAL records without exporting a separate plaintext message archive.

This project is an early implementation.
The reader and optional key initialization have been exercised with WeChat 4.1.15 on Apple Silicon.
Other versions require validation against their local schemas.
There is no message sending, deletion, media decoding, or cloud-history synchronization.

## Requirements

- macOS with WeChat installed and an account that has local message history.
- Python 3.11 or later and [uv](https://docs.astral.sh/uv/).
- SQLCipher 4.x.
- Verified per-database raw SQLCipher keys, kept in a private local manifest.

The normal reader does not acquire keys.
If you already have valid keys, use them directly.
For first-time Apple Silicon setup, read [the optional initialization guide](docs/wechat-setup.md) before running the separate manual tools.
That procedure uses a temporary re-signed client and may require phone login.

## Install from a checkout

From the repository directory:

```sh
brew install sqlcipher
uv sync --locked
export ANYMESSAGE_KEYS_FILE="$HOME/Library/Application Support/AnyMessage/wechat/keys.json"
uv run anymessage --status
```

Set `ANYMESSAGE_KEYS_FILE` to your existing manifest's actual path.
`ready` means the required database schemas could be read with the supplied keys.
`key_missing` means setup is incomplete; it does not mean there is no local history.
The [setup guide](docs/wechat-setup.md) explains other readiness states.

Start MCP over stdin and stdout:

```sh
uv run anymessage
```

The server does not listen on a network port or register itself in a client automatically.
After installation, `.venv/bin/anymessage` is the executable to use in an MCP client configuration.
This repository does not assume a published PyPI package.

## Connect an MCP client

Use [examples/mcp.json](examples/mcp.json) for clients with an `mcpServers` object, or [examples/codex-mcp.toml](examples/codex-mcp.toml) for Codex.
Replace each `/absolute/path/...` value with an actual absolute path before saving the entry in your client.
The command should point to this checkout's `.venv/bin/anymessage`; the key-file value should point outside the checkout.
Do not rely on shell expansion of `~` or `$HOME` in the configuration's command or environment strings.
Restart or reload the client's MCP connection after changing its configuration.

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

| Variable | Purpose |
| --- | --- |
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

The default suite uses only synthetic data, including real SQLCipher-encrypted fixtures, compressed text, multiple shards, pagination ties, literal search, committed WAL reads, rejected writes, and official MCP client round trips.
See [CONTRIBUTING.md](CONTRIBUTING.md), [the architecture](docs/architecture.md), and [the database research notes](docs/wechat-research.md).

## License

AnyMessage is [MIT licensed](LICENSE).
The optional vendored bootstrap tools retain their original MIT notice; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
WeChat and SQLCipher are installed separately and are not distributed by this repository.
