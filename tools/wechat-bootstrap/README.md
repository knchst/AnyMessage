# Optional WeChat first-key tools

These files are copied unchanged from the MIT-licensed [WeChat 4.1.15 research gist](https://gist.github.com/acmerfight/0a01249ef72970d07a2603dbb629e80f) at revision `c98dbb10cf23c30e83d5d744905101b0880887a0`.
The original copyright and full license are preserved in [LICENSE](LICENSE).

| File | SHA-256 |
| --- | --- |
| `bootstrap_keys.py` | `ce8596107df3ca3715c8b63b522bbcc84148207217fade3b642975323c65b31c` |
| `capture_keys.py` | `0b0b9f9296bf02219c07537827ed9ce97671611ffeab36572bedb267a7704d65` |
| `LICENSE` | `72380798af61d6131287b0dd3c8dc5345535df641002fece3a02d7ef109c5f8e` |

They are separate manual tools and are never imported or invoked by the MCP server.
They are included in the source distribution, not in the installed runtime wheel.
The temporary-copy procedure was exercised with WeChat 4.1.15 on Apple Silicon; other versions and platforms require separate validation.

Read [the setup guide](../../docs/wechat-setup.md) before running them.
Bootstrap requires a normally quit original client and an explicit `--acknowledge-debug-copy` argument.
It creates and re-signs a temporary application copy, launches a real client session under LLDB, and may require phone login.
The copy uses the real account's local data.
The capture helper closes its owned process when done.
The wrapper checks the original signature, disables the copy, and attempts to restore the official app.

Choose a private `--state-dir` outside the checkout.
The helper saves verified keys with owner-only permissions in that directory.
Point `ANYMESSAGE_KEYS_FILE` to its `keys.json` after acquisition and verify reader readiness.
Reuse existing valid keys for normal operation.

Do not commit or upload key files, application copies, databases, signature records, debugger captures, or real message examples.
