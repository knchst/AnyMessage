# Third-party notices

AnyMessage is licensed under the [MIT License](LICENSE).
Its source distribution and runtime wheel include two optional upstream tools with their own preserved notice.

## WeChat first-key tools

`tools/wechat-bootstrap/bootstrap_keys.py` and `tools/wechat-bootstrap/capture_keys.py` are copied unchanged from the [WeChat research gist by acmerfight](https://gist.github.com/acmerfight/0a01249ef72970d07a2603dbb629e80f), revision `c98dbb10cf23c30e83d5d744905101b0880887a0`.
Their MIT copyright notice is `Copyright (c) 2026 Contributors`.
The full original license is preserved in [tools/wechat-bootstrap/LICENSE](tools/wechat-bootstrap/LICENSE).
The file digests are listed in [the tools README](tools/wechat-bootstrap/README.md).

These scripts are subprocess resources, not imports or MCP tools.
The wheel installs byte-identical copies and the license under `anymessage/vendor/wechat-bootstrap`.
Only explicit setup consent or manual invocation runs them.

## Runtime dependencies and external tools

The Python package depends on the official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk), [python-zstandard](https://github.com/indygreg/python-zstandard), and [TOMLKit](https://github.com/python-poetry/tomlkit).
Dependency versions and distribution hashes are recorded in `uv.lock`; each dependency retains its own license.
SQLCipher is installed separately and retains [its own license](https://github.com/sqlcipher/sqlcipher/blob/master/LICENSE.md).
WeChat is a separate proprietary application and is not distributed by this project.

[REA](https://github.com/morluto/rea) was used for application research.
[GreenBubbles](https://github.com/bojieli/greenbubbles) provided public database-format references.
Neither is a runtime dependency, and their application code is not bundled here.
