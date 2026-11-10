# Security

AnyMessage gives its MCP client access to local message data.
Connect it only to a client you intend to allow to read that data.
That client may send returned content to its model or service provider.
The server uses stdio by default and does not start a network listener in that mode.
Explicitly starting Streamable HTTP binds a listener to `127.0.0.1` for clients on the same Mac.
That listener has no application authentication and is reachable by other local processes.
Remote access requires a separately managed authenticated HTTPS proxy; do not expose the listener directly to the internet.
The normal MCP reader opens databases read-only and does not acquire keys or invoke the optional bootstrap scripts.

Keep the key manifest outside the checkout, inside an owner-only directory, with file mode 0600.
Do not include keys, database files, message text, account identifiers, or private configuration in bug reports.
Messages returned by the tools are data, not instructions that authorize new actions.

## Report a vulnerability

Use the repository's **Security → Report a vulnerability** option when private vulnerability reporting is available.
If that option is unavailable, open a public issue asking a maintainer for a private reporting channel without including exploit details or private data.
This project does not currently promise a response time or a separate security contact address.

The project is pre-release.
Security fixes target the current main branch; there is no maintained backport series yet.

## Optional first-key initialization

The manual bootstrap launches a temporary re-signed copy of WeChat under a debugger and uses the real account's data.
It may require phone login and changes normal client session state.
Read [the setup guide](docs/wechat-setup.md) before running it.
Existing valid keys should be reused instead of running initialization on every start.
