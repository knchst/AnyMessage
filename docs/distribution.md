# MCPB distribution and Registry publication

The Homebrew installation remains the tested user setup path.
An MCPB package provides the same released source for hosts supporting MCPB manifest 0.4 and its UV runtime.
It requires Apple Silicon macOS 14 or later.
Reading personal history through the default WeChat entry requires SQLCipher, account keys, and explicit account setup.
An explicitly selected local Telegram Desktop JSON export needs no WeChat installation, settings, keys, or SQLCipher.
The host must provide a working UV launcher and Python 3.11 or later, or manage those runtimes itself.
The first launch can download Python dependencies and build the local project.
It does not fetch the unrelated `anymessage` package from PyPI, capture keys, or register an account.

The package's default installed entry launches normal WeChat `mcp` mode using saved configuration references.
Telegram export mode requires a separate [client entry](telegram-export.md#connect-your-ai) with `--telegram-export` and an explicit JSON path.
From 0.9.0 onward, print that entry with `anymessage mcp config --telegram-export result.json --format json` or `--format toml`, then merge it into the existing configuration.
Generation does not validate the export or write client settings. The launched server validates the selected file.
Neither bundle installation nor `mcp add` selects an export; registration with the flag is rejected.
The export workflow is verified through the MCP SDK on macOS. Export configuration in a desktop host has not been tested.
Use the [fictional demo](demo.md) separately before connecting an account.
Manifest validation and an MCP SDK check establish artifact behavior; they do not establish installation support in a particular desktop host.
Keep each host's actual installation results separate from these checks.

## Use the published bundle

Download [AnyMessage 0.9.0 MCPB](https://github.com/knchst/AnyMessage/releases/download/v0.9.0/anymessage-0.9.0.mcpb) from the [release page](https://github.com/knchst/AnyMessage/releases/tag/v0.9.0).
The [historical 0.8.0 bundle](https://github.com/knchst/AnyMessage/releases/tag/v0.8.0) retains its manual Telegram entries and original metadata. Configuration generation and qualified source requirements apply to 0.9.0 and later.
If you already configured AnyMessage, the bundle uses those saved account references.
For a new account setup, read the [first-time setup guide](wechat-setup.md), then run:

```sh
brew install knchst/tap/anymessage
anymessage setup --no-register
anymessage doctor
```

`--no-register` saves ready account references without adding another client entry.
First-time key capture still requires the setup flow's explicit consent.
Import the downloaded bundle using your host's MCPB 0.4 UV installation procedure, then check `status`.
After updating a bundle, restart or reconnect its MCP server before checking `status` again.

## Build the release pair

The commands below are a reproducible 0.4.0 example with its immutable source hash.
For another release, use that version's reviewed archive, expected hash, tag, and a new output directory.
Use Python 3.11 or later and the already published source archive.
Obtain its expected SHA-256 from the reviewed release record.
Use a new output directory; the builder refuses an existing directory.

```sh
python3 scripts/build_mcpb.py downloads/anymessage-0.4.0.tar.gz \
  --sha256 721fe672a8d1dd912faae5c61b615a000fdb35bf56d8483f96efc2a4b4d75c46 \
  --out dist/registry-0.4.0
```

The builder derives the version from the verified archive and retains its project, local lock, bootstrap resources, licenses, documentation, and linked assets.
Archives before 0.9.0 retain the original WeChat metadata; 0.9.0 and later describe both sources and qualify the default WeChat requirements.
It emits `anymessage-0.4.0.mcpb` and `server.json` referring to that artifact's SHA-256 and exact GitHub Release URL.
It copies no working checkout or virtual environment.
If any command fails, do not upload its output.

Check the actual emitted files with the official MCPB CLI and publisher, then run the isolated MCP check:

```sh
unzip -p dist/registry-0.4.0/anymessage-0.4.0.mcpb manifest.json > dist/registry-0.4.0/manifest.json
npx --yes @anthropic-ai/mcpb@2.1.2 validate dist/registry-0.4.0/manifest.json
mcp-publisher validate dist/registry-0.4.0/server.json
uv run --frozen python scripts/check_mcpb.py dist/registry-0.4.0/server.json --tag v0.4.0 --runtime
```

Use a verified official `mcp-publisher` release.
The runtime check needs SQLCipher and a working `uv` command; `--uv /absolute/path/to/uv` selects another launcher.
It extracts into a temporary path containing spaces, sets explicit nonexistent account references, checks the version's read-only tool inventory and unconfigured status, and reads eight synthetic demo messages.
The verified numeric release version selects the original six tools for releases before 0.6.0 and seven tools including `get_message_context` from 0.6.0 onward.
For those newer releases, it also follows an actual search hit to the adjacent approval and verified specification attachment.
From 0.7.0 onward, it separately launches the packaged fictional Telegram export with broken unrelated WeChat configuration and follows its proposal to the approval.
For 0.7 releases it checks unsupported media. From 0.8.0 onward it requires the packaged specification TXT and reads its actual text through context, attachment listing, and the returned ID, while keeping Telegram verification false.
From 0.9.0 onward, the extracted bundle's own environment generates a TOML entry for a relative export path containing spaces and non-ASCII characters.
The check parses that entry and starts its actual command, arguments, and environment from a different directory, then follows the same proposal, approval, and specification chain.
This explicit SDK launch does not change the bundle's default installed WeChat entry.
The check prints the observed tool count; the immutable 0.4.0 example above still expects six.
It does not read an actual account.
Review the archive inventory and `server.json` before uploading.

## Publish the verified pair

The commands below continue the reproducible 0.4.0 example.
Upload exactly the reviewed pair to its existing release without overwriting assets:

```sh
gh release upload v0.4.0 dist/registry-0.4.0/anymessage-0.4.0.mcpb dist/registry-0.4.0/server.json --repo knchst/AnyMessage
gh workflow run publish-registry.yml --repo knchst/AnyMessage --ref main -f tag=v0.4.0
```

The manual workflow downloads the existing pair, checks its identity, version, launch contract and actual hash, and validates it with the pinned publisher before authentication.
It then publishes only Registry metadata under `io.github.knchst/anymessage` using GitHub OIDC.
Its permissions are `contents: read` and `id-token: write`; it has no dedicated PAT, PyPI credential, release-write permission, or pull-request trigger.
The publisher is pinned and its Sigstore bundle is verified against the upstream release workflow.

Read the public Registry record after publication and compare its version, package URL and hash with the reviewed pair.
Until that readback succeeds, report publication as unverified.
For a partial upload, compare any existing asset with the reviewed local bytes and upload only the missing file.
Never overwrite a published artifact.
If publication returns an ambiguous error or a duplicate version, read the exact Registry version first; matching metadata proves the earlier publication succeeded.
Otherwise stop and investigate before publishing a new version.
Registry versions and metadata are immutable after publication.

PyPI publication remains separate work in [Issue #15](https://github.com/knchst/AnyMessage/issues/15).
The PyPI name `anymessage` belongs to an unrelated email SDK.
See the official [MCPB specification](https://github.com/modelcontextprotocol/mcpb/blob/main/MANIFEST.md), [Registry package types](https://modelcontextprotocol.io/registry/package-types), and [OIDC publication guide](https://github.com/modelcontextprotocol/registry/blob/main/docs/modelcontextprotocol-io/github-actions.mdx).
