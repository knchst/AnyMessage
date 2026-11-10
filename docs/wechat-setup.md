# Set up WeChat access

Use existing verified keys when available.
The MCP server never launches a debugger or changes WeChat's signature or login state.
First-time key acquisition runs only through explicit setup consent or the separate manual tools.

## Use an existing manifest

Place the key manifest outside the checkout in an owner-only directory.
Restrict its permissions, then save its reference with setup:

```sh
chmod 600 "/absolute/path/private/manifest.json"
anymessage setup --keys-file "/absolute/path/private/manifest.json"
anymessage doctor
```

`ready` and `schema_verified=true` mean the required schemas were read with compatible columns.
They do not prove that all history is synchronized locally or that every message type can be decoded.
The reader requires keys for the contact database, session database, and every discovered `message_N.db` shard.
Unused databases may remain outside its supported coverage.

| State | Next step |
| --- | --- |
| `data_missing` | Log into the original WeChat client and verify that local history exists, or check `ANYMESSAGE_DATA_DIR`. |
| `database_missing` | Check that the selected local data includes the required contact, session and message database files. |
| `account_required` | Pass `setup --account ID` with an account id from `doctor --json`, or set `ANYMESSAGE_ACCOUNT`. |
| `sqlcipher_missing` | Install SQLCipher, or correct `ANYMESSAGE_SQLCIPHER` or the saved `sqlcipher` reference to an executable command or path. An invalid selection does not fall back to another installation. |
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
2. Run `anymessage doctor` and resolve missing capture prerequisites.
3. Run `anymessage setup` and acknowledge the temporary-copy procedure when prompted.
4. Complete phone approval or login in the temporary WeChat window if requested.
5. Confirm that the original WeChat client is running after capture.

For explicit setup without terminal prompts:

```sh
anymessage setup --non-interactive --acknowledge-debug-copy \
  --state-dir "/absolute/path/private/new-capture-directory"
```

The state directory must not exist.
If omitted, setup creates a new mode-0700 directory beneath `~/Library/Application Support/AnyMessage/wechat`.
The selected account's `db_storage` path is passed explicitly to capture.
For multiple accounts, pass `--account ID`; `--data-dir` can also select an account directory or its `db_storage` directly.
Setup never overwrites an existing capture directory or key manifest.
It saves references and registers clients only after the reader reports `ready`.
A zero capture exit code alone does not complete setup.

To invoke the unchanged upstream wrapper manually from a checkout:

```sh
/usr/bin/python3 tools/wechat-bootstrap/bootstrap_keys.py \
  --acknowledge-debug-copy \
  --state-dir "/absolute/path/private/new-manual-capture-directory" \
  --db-root "/absolute/path/account/db_storage"
```

The manual wrapper refuses a state directory containing a previous copy or key file.
Neither entry point uses `sudo`, changes SIP, or re-signs the original application.

The wrapper records and verifies the original signature, copies WeChat, re-signs only that copy, and launches the copy under LLDB.
The capture core observes PBKDF arguments only when their salts match the selected databases and saves candidates only after first-page HMAC verification.
Complete login in the temporary window if requested.

After capture, the wrapper checks the original signature, disables the temporary copy, and attempts to launch the original executable.
Confirm that the original client is running before using WeChat normally.
If it reports `manual_restore_required`, CLI setup fails even if capture returned zero.
Before trying setup again, quit the exact temporary copy normally and open `/Applications/WeChat.app` yourself.
Do not overwrite existing keys or repeatedly retry login and capture automatically.
An interrupted directory read can stop setup; resolve the filesystem issue before retrying.

For a manual capture, run `anymessage setup --keys-file "/absolute/path/private/capture/keys.json"` afterward.
CLI setup does this validation before saving the new reference.
Capture may leave some unused databases uncovered; reader readiness is the check for its supported databases.
If a required shard remains locked, setup is incomplete even if other keys were saved.
Reuse valid keys for ordinary reads instead of rerunning bootstrap after every restart.

## Protect local artifacts

Setup prints only fixed, allowlisted progress messages from the helper and never prints captured keys or arbitrary helper output.
A client registration conflict can occur after a ready configuration has been saved.
Inspect the existing client entry, then rerun `anymessage mcp add NAME --replace` if you want to replace it.

Keep keys, application copies, signature records, databases, and debugger captures outside the repository.
Do not upload them to issues or include them in a release archive.
After confirming the original application is restored and the manifest is safely stored, remove temporary application copies you no longer need.

## Read local attachments

After reader setup, reload the client's MCP connection to expose all seven tools in AnyMessage 0.6.0 or later.
Call `get_messages`, then `list_attachments` and `read_attachment` with the returned message and attachment ids.
For a search hit, pass its conversation/message pair to `get_message_context` to read nearby records before selecting an attachment.
Attachment reads reuse local files and keys.
They do not start capture, restart WeChat, or download a missing cache variant.

An encrypted `hardlink/hardlink.db` needs its own existing manifest entry for file lookup.
An image without packed row metadata needs the key for `message/message_resource.db`.
A ready `status` checks core conversation and message databases; attachment index and image-key failures are reported by the attachment tools.
Supported V2 images also require one existing configuration that matches the selected account suffix.
An absent or ambiguous match reports `image_key_required` without trying another account.
See [attachment states and limits](attachments.md).
