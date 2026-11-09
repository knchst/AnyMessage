# Set up WeChat access

Use existing verified keys when available.
The MCP server never launches a debugger or changes WeChat's signature or login state.
First-time key acquisition is a separate manual procedure.

## Use an existing manifest

Place the key manifest outside the checkout in an owner-only directory.
Set `ANYMESSAGE_KEYS_FILE` to its absolute path, then restrict its permissions:

```sh
chmod 600 "$ANYMESSAGE_KEYS_FILE"
uv run anymessage --status
```

`ready` and `schema_verified=true` mean the required schemas were read with compatible columns.
They do not prove that all history is synchronized locally or that every message type can be decoded.
The reader requires keys for the contact database, session database, and every discovered `message_N.db` shard.
Unused databases may remain outside its supported coverage.

| State | Next step |
| --- | --- |
| `data_missing` | Log into the original WeChat client and verify that local history exists, or check `ANYMESSAGE_DATA_DIR`. |
| `database_missing` | Check that the selected local data includes the required contact, session and message database files. |
| `account_required` | Set `ANYMESSAGE_ACCOUNT` to an account id from `status`. |
| `sqlcipher_missing` | Install SQLCipher or set `ANYMESSAGE_SQLCIPHER`. |
| `key_missing` | Supply a private manifest with the required per-database entries. |
| `key_file_permissions` | Set the manifest's permissions to 0600. |
| `key_manifest_invalid` | Correct the manifest's JSON shape, relative paths, keys or salts. |
| `key_invalid` | Verify the selected account, database salt and key before acquiring new keys. |
| `schema_unsupported` | Report the version and missing table or column using synthetic or redacted information. |
| `database_read_failed` | Check the database path, key, SQLCipher installation and WAL availability. |

When several accounts exist, select one explicitly and keep its keys separate.
Do not combine a key manifest from one account with another account's databases.

## First-time initialization on Apple Silicon

The optional [upstream tools](../tools/wechat-bootstrap/README.md) target native Apple Silicon macOS and were exercised with WeChat 4.1.15.
They require Xcode's LLDB Python module, the Apple system Python, `codesign`, and SQLCipher for subsequent reader verification.
Check that `xcrun lldb -P` succeeds before proceeding.
Intel Macs, Rosetta, other client versions, and future login behavior are not validated by that result.

The temporary client uses the real account's data.
It can require phone approval or re-login and may affect ordinary client session state.
The helper closes the temporary process after capture.
Review the scripts and preserve drafts before starting; do not run this procedure while another tool or person is using WeChat.

1. Save drafts and quit the original WeChat normally.
2. Choose a new private state directory outside the checkout.
3. Run the manual bootstrap from the checkout, replacing the state directory with your actual path:

```sh
/usr/bin/python3 tools/wechat-bootstrap/bootstrap_keys.py \
  --acknowledge-debug-copy \
  --state-dir "/absolute/path/private/anymessage-bootstrap"
```

For multiple accounts, add `--db-root "/absolute/path/account/db_storage"`.
The tool refuses an existing state directory containing a previous copy or key file.
It does not use `sudo`, change SIP, or re-sign the original application.

The wrapper records and verifies the original signature, copies WeChat, re-signs only that copy, and launches the copy under LLDB.
The capture core observes PBKDF arguments only when their salts match the selected databases and saves candidates only after first-page HMAC verification.
Complete login in the temporary window if requested.

After capture, the wrapper checks the original signature, disables the temporary copy, and attempts to launch the original executable.
Confirm that the original client is running before using WeChat normally.
If it reports `manual_restore_required`, quit the exact temporary copy normally and open `/Applications/WeChat.app` yourself.
Do not overwrite existing keys or repeatedly retry login and capture automatically.
An interrupted directory read can stop setup; resolve the filesystem issue before retrying.

Point `ANYMESSAGE_KEYS_FILE` to the generated private `keys.json` and run `uv run anymessage --status`.
Capture may leave some unused databases uncovered; reader readiness is the check for its supported databases.
If a required shard remains locked, setup is incomplete even if other keys were saved.
Reuse valid keys for ordinary reads instead of rerunning bootstrap after every restart.

## Protect local artifacts

Keep keys, application copies, signature records, databases, and debugger captures outside the repository.
Do not upload them to issues or include them in a release archive.
After confirming the original application is restored and the manifest is safely stored, remove temporary application copies you no longer need.
