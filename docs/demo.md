# Investigate a fictional project

Try AnyMessage without WeChat or account keys.
The demo creates a temporary fictional Atlas project, then reads it through the same MCP tools used for live conversations.
SQLCipher is required; an AI subscription is optional.

The demo commands are available in the latest source, ahead of the current Homebrew release.
Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and SQLCipher, then run:

```sh
brew install sqlcipher
uvx --from git+https://github.com/knchst/AnyMessage anymessage demo
```

From a development checkout, use `uv run anymessage demo`.
The terminal walkthrough makes real MCP calls and prints their returned messages and attachment text.
It does not call an AI model or generate a conclusion.

## Read the evidence

The Atlas pilot begins with a 400 mAh battery and a quantity of 50.
Chen proposes 500 mAh, and Maya approves that change.
A specification is cached locally, while a referenced enclosure drawing is deliberately unavailable.

The following table is a manual interpretation of the fictional data, not generated output:

| Finding | Evidence |
| --- | --- |
| The approved battery is 500 mAh. | Maya, 2026-11-09 09:03 UTC, `message/message_0.db:3`. The specification attached to message `:4` agrees. |
| The pilot quantity stays at 50. | Maya, 09:03 UTC, message `:3`; specification in message `:4`. |
| November 26 delivery is a proposal awaiting factory confirmation. | Chen, 09:07 UTC, message `:7`; specification in message `:4`. |
| Final thickness and the quote are unresolved. | Maya, 09:05 and 09:08 UTC, messages `:5` and `:8`; Chen, 09:07 UTC, message `:7`. |
| The drawing's content cannot be checked. | Attachment in message `:6` returns `attachment_missing` with `cache_file_unavailable`. This does not prove why the cache is absent. |

All message IDs above belong to conversation `atlas-demo@chatroom`.
The terminal also runs a search limited to the two newest messages.
That search has no matches for `500` and reports `incomplete=true` because older messages remain.
An empty incomplete result does not establish that the battery was never discussed.

## Connect your AI

Create a separate server entry named `anymessage-demo` in a local MCP client.
With a source installation, the server command is:

```sh
uvx --from git+https://github.com/knchst/AnyMessage anymessage demo --mcp
```

For clients that accept a JSON configuration, use:

```json
{
  "mcpServers": {
    "anymessage-demo": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/knchst/AnyMessage", "anymessage", "demo", "--mcp"]
    }
  }
}
```

For Codex, use the equivalent TOML entry:

```toml
[mcp_servers.anymessage-demo]
command = "uvx"
args = ["--from", "git+https://github.com/knchst/AnyMessage", "anymessage", "demo", "--mcp"]
```

Restart or reload the client's MCP connection, then ask:

> Use only the anymessage-demo tools. Start with status. Investigate the Atlas pilot project and read available attachments. Distinguish approved changes, proposals, and unresolved questions. For each finding, include the sender, UTC time, and message ID. List attachments you could not read. Check whether November 26 delivery was approved.

The AI client may send this fictional content to its model provider.
Different models can phrase or interpret their answers differently; compare them with the evidence above.

On 2026-11-10, the representative investigation was exercised with Codex CLI 0.166.0 and Claude Code 2.1.314 on macOS 26.6.1 using MCP SDK 1.30.0.
Both clients read the specification, cited the approved battery change, kept delivery unconfirmed, and identified the missing drawing.
This is a fixture-based compatibility check, not a claim about every client or live WeChat version.

## Data and lifecycle

Every demo server creates its own temporary encrypted databases and fictional text attachment.
Generated keys belong only to that temporary dataset.
The files are removed when the server closes normally.
The demo bypasses saved AnyMessage configuration, does not discover real accounts, and does not run setup, capture, or registration.
`ANYMESSAGE_SQLCIPHER` can select the executable; an invalid selection produces setup guidance instead of choosing another installation.
The normal source status may inspect an installed WeChat application's public version metadata, even though the messages and keys are fictional.

For your own conversations, follow [the normal setup instructions](../README.md#install-and-set-up).
The demo cannot establish compatibility with your WeChat version or availability of your real attachments.
