"""Preserve client configuration while registering one local MCP server."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any, Literal

import tomlkit

from anymessage.settings import private_write


@dataclass(frozen=True)
class ClientSpec:
    name: str
    relative_path: str
    format: Literal["json", "toml"]
    section: str

    def path(self) -> Path:
        if self.name == "codex":
            return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser() / "config.toml"
        return Path.home() / self.relative_path


CLIENTS = {
    client.name: client for client in (
        ClientSpec("codex", ".codex/config.toml", "toml", "mcp_servers"),
        ClientSpec("cursor", ".cursor/mcp.json", "json", "mcpServers"),
        ClientSpec("claude-desktop", "Library/Application Support/Claude/claude_desktop_config.json", "json", "mcpServers"),
        ClientSpec("claude-code", ".claude.json", "json", "mcpServers"),
        ClientSpec("grok", ".grok/config.toml", "toml", "mcp_servers"),
    )
}

GROKBOT_GUIDANCE = (
    "Grok Bot runs MCP tools on Grok Bot's computer, not this Mac. A local command or "
    "127.0.0.1 URL cannot reach this Mac's databases there. For its HTTP form, supply a "
    "user-managed HTTPS proxy that authenticates requests and forwards to AnyMessage's "
    "loopback HTTP server. Remote Grok Bot connectivity has not been verified. "
    "Grok CLI is a separate local client; use 'anymessage mcp add grok' for that client."
)


@dataclass(frozen=True)
class RegistrationResult:
    client: str
    status: Literal["added", "replaced", "unchanged"]
    path: Path
    backup: Path | None = None


TOKEN = re.compile(r'\s+|//[^\n]*|/\*[\s\S]*?\*/|"(?:[^"\\]|\\.)*"|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null|[{}\[\],:]')


def json_document(text: str) -> tuple[dict[str, Any], list[re.Match[str]]]:
    tokens = []
    offset = 0
    for token in TOKEN.finditer(text):
        if token.start() != offset:
            raise ValueError("Client configuration must be JSON with optional comments and trailing commas.")
        offset = token.end()
        if not token[0].isspace() and not token[0].startswith(("//", "/*")):
            tokens.append(token)
    if offset != len(text):
        raise ValueError("Client configuration contains unsupported JSON syntax.")
    cleaned = " ".join(token[0] for index, token in enumerate(tokens) if not (token[0] == "," and index + 1 < len(tokens) and tokens[index + 1][0] in ("}", "]")))
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValueError("Duplicate JSON object keys cannot be edited safely.")
            result[name] = value
        return result

    document = json.loads(cleaned, object_pairs_hook=unique_object)
    if not isinstance(document, dict):
        raise ValueError("Client configuration must be an object.")
    return document, tokens


def json_members(tokens: list[re.Match[str]], start: int) -> tuple[dict[str, tuple[int, int]], int]:
    members = {}
    index = start + 1
    while tokens[index][0] != "}":
        name = json.loads(tokens[index][0])
        if name in members:
            raise ValueError("Duplicate JSON object keys cannot be edited safely.")
        index += 2
        value_start = index
        if tokens[index][0] in ("{", "["):
            depth = 1
            index += 1
            while depth:
                depth += int(tokens[index][0] in ("{", "[")) - int(tokens[index][0] in ("}", "]"))
                index += 1
        else:
            index += 1
        members[name] = (value_start, index)
        if tokens[index][0] == ",":
            index += 1
    return members, index


def edit_json(text: str, section: str, name: str, entry: dict[str, Any]) -> str:
    document, tokens = json_document(text)
    members, close = json_members(tokens, 0)
    if section not in document:
        insertion = json.dumps(section) + ": " + json.dumps({name: entry}, indent=2)
    else:
        start, _ = members[section]
        members, close = json_members(tokens, start)
        if name in members:
            start, end = members[name]
            return text[:tokens[start].start()] + json.dumps(entry, indent=2) + text[tokens[end - 1].end():]
        insertion = json.dumps(name) + ": " + json.dumps(entry, indent=2)
    comma = "," if members and tokens[close - 1][0] != "," else ""
    position = tokens[close].start()
    return text[:position] + comma + "\n" + insertion + "\n" + text[position:]


def server_entry(client: str | None = None, command: str | None = None) -> dict[str, Any]:
    executable = command
    if executable is None and Path(sys.argv[0]).name == "anymessage":
        executable = sys.argv[0]
    executable = executable or shutil.which("anymessage")
    if not executable:
        raise ValueError("AnyMessage executable was not found on PATH. Pass --command with its absolute path.")
    executable = str(Path(executable).expanduser().absolute())
    entry: dict[str, Any] = {**({"type": "stdio"} if client == "cursor" else {}), "command": executable, "args": ["mcp"]}
    if os.environ.get("ANYMESSAGE_CONFIG_FILE"):
        entry["env"] = {"ANYMESSAGE_CONFIG_FILE": str(Path(os.environ["ANYMESSAGE_CONFIG_FILE"]).expanduser().absolute())}
    return entry


def configuration(format: str, *, client: str | None = None, command: str | None = None, server_only: bool = False) -> str:
    if client == "grokbot":
        raise ValueError(GROKBOT_GUIDANCE)
    entry = server_entry(client, command)
    if format == "json":
        return json.dumps(entry if server_only else {"mcpServers": {"AnyMessage": entry}}, indent=2) + "\n"
    return tomlkit.dumps(entry if server_only else {"mcp_servers": {"AnyMessage": entry}})


def register(client: str, *, replace: bool = False, command: str | None = None) -> RegistrationResult:
    spec = CLIENTS[client]
    path = spec.path()
    if path.is_symlink():
        raise ValueError(f"Refusing to replace symlink configuration {path}. Use anymessage mcp config to generate an entry, then edit its managed target manually.")
    original = path.read_text() if path.exists() else ("{}\n" if spec.format == "json" else "")
    parsed = tomlkit.parse(original) if spec.format == "toml" else json_document(original)[0]
    servers = parsed.get(spec.section, {})
    if not isinstance(servers, dict):
        raise ValueError(f"{spec.section} must be an object/table. Configuration was not changed.")
    matches = [name for name in servers if name.casefold() == "anymessage"]
    if len(matches) > 1:
        raise ValueError("Several AnyMessage entries exist. Resolve them manually before registration.")
    name = matches[0] if matches else "AnyMessage"
    present = name in servers
    entry = server_entry(client, command)
    existing = servers.get(name)
    if existing == entry:
        return RegistrationResult(client, "unchanged", path)
    if present and not replace:
        raise ValueError(f"{client} already has an AnyMessage entry. Inspect {path}, then use --replace to replace it.")
    if spec.format == "toml":
        if spec.section not in parsed:
            parsed[spec.section] = tomlkit.table()
        parsed[spec.section][name] = entry
        updated = tomlkit.dumps(parsed)
        readback = tomlkit.parse(updated)
    else:
        updated = edit_json(original, spec.section, name, entry)
        readback = json_document(updated)[0]
    expected = {key: value for key, value in servers.items() if key != name}
    actual = {key: value for key, value in readback[spec.section].items() if key != name}
    if readback[spec.section][name] != entry or actual != expected:
        raise ValueError("Client configuration readback failed. Configuration was not changed.")
    backup = None
    if path.exists():
        descriptor, filename = tempfile.mkstemp(prefix=path.name + ".backup-", dir=path.parent)
        backup = Path(filename)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(original)
    private_write(path, updated)
    return RegistrationResult(client, "replaced" if present else "added", path, backup)
