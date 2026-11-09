# Contributing

Start with a focused issue or pull request describing the user-visible behavior you want to change.
For a bug, include the Python, macOS, WeChat, and SQLCipher versions, the affected MCP tool, and a redacted error code.
Use synthetic data to reproduce it.
Keep actual messages, account identifiers, database files, keys, screenshots, debugger captures, and local configuration out of issues and patches.
See [SECURITY.md](SECURITY.md) for vulnerabilities.

## Set up development

Install Python 3.11 or later, [uv](https://docs.astral.sh/uv/), and SQLCipher.
On macOS:

```sh
brew install sqlcipher
uv sync --locked --group dev
uv run pytest -q
```

The default suite uses synthetic plaintext and encrypted databases only.
It does not discover or query your WeChat account.
Tests require the SQLCipher executable and shared library, and fail with setup instructions when either is missing.
When set, `ANYMESSAGE_SQLCIPHER` must point to a valid executable.
Otherwise, discovery searches `PATH`, then standard Homebrew locations.
For a nonstandard library installation, set `ANYMESSAGE_SQLCIPHER_LIBRARY` to the shared library path.

The WAL test keeps a real SQLCipher writer open while the read-only reader observes committed records.
The protocol tests launch a real stdio server through the official MCP client.
Do not replace those behavior checks with mocked responses.

## Check a release candidate

```sh
python3 scripts/check_publication.py
uv build
python3 scripts/check_publication.py --archive dist/*.whl dist/*.tar.gz
python3 scripts/check_publication.py --history
```

The publication check examines index blobs, working files, distribution members, and optionally reachable Git history for personal home paths, real account-id patterns, key manifests, private keys, and local database or capture artifacts.
It is a focused guard, not a general-purpose secret scanner.
Review the final staged diff and history as well.
`--require-noreply` can be combined with `--history` when preparing a history that must use GitHub noreply addresses; it is not required for contributors' chosen public identities.

GitHub Actions runs the synthetic suite and package checks sequentially with Python 3.11 and 3.13 on one macOS 26 runner.
It also installs the wheel into a separate environment and invokes its entry point outside the source checkout.
The workflow does not publish packages or releases.

## Change code and documentation together

Keep the MCP tool contract and the documentation consistent.
Validate external values at their owning boundary and keep the current source implementation in one module unless another source needs a shared interface.
Preserve the read-only SQLCipher flags and explicit incomplete-search coverage.
Document any supported schema change and add a synthetic behavior case that would fail without it.

Preserve the notices and pinned provenance of the optional upstream bootstrap tools.
Update their source revision, digests, and setup guide together if you intentionally replace them.
