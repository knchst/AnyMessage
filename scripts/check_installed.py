#!/usr/bin/env python3
"""Smoke-test installed CLI resources without accessing a real messaging account."""

from __future__ import annotations

import asyncio
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from anymessage.clients import CLIENTS
from anymessage.server import create_server
from anymessage.settings import Settings


EXPECTED = {
    "bootstrap_keys.py": "ce8596107df3ca3715c8b63b522bbcc84148207217fade3b642975323c65b31c",
    "capture_keys.py": "0b0b9f9296bf02219c07537827ed9ce97671611ffeab36572bedb267a7704d65",
    "LICENSE": "72380798af61d6131287b0dd3c8dc5345535df641002fece3a02d7ef109c5f8e",
}


async def check_export(entry: dict, environment: dict[str, str], cwd: Path) -> None:
    default = create_server(demo_settings=Settings(cwd / "no-account", None, None, None))
    expected_schemas = {tool.name: tool.inputSchema for tool in await default.list_tools()}
    parameters = StdioServerParameters(command=entry["command"], args=entry["args"], env={**environment, **entry.get("env", {})}, cwd=str(cwd))
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            tools = (await client.list_tools()).tools
            assert len(tools) == 7 and {tool.name: tool.inputSchema for tool in tools} == expected_schemas
            assert all(tool.annotations.readOnlyHint and tool.annotations.destructiveHint is False and tool.annotations.idempotentHint and tool.annotations.openWorldHint is False for tool in tools)
            status = await client.call_tool("status", {})
            assert len(status.structuredContent["sources"]) == 1
            assert status.structuredContent["sources"][0]["source"] == "telegram_export"
            assert status.structuredContent["sources"][0]["state"] == "ready"
            search = await client.call_tool("search_messages", {"query": "proposal", "conversation_id": "telegram:private_group:42"})
            hit = search.structuredContent["items"][0]
            context = await client.call_tool("get_message_context", {"conversation_id": hit["conversation_id"], "message_id": hit["id"], "before": 0, "after": 2})
            assert not context.isError and context.structuredContent["items"][1]["text"] == "Approved."
            assert context.structuredContent["anchor_index"] == 0 and context.structuredContent["items"][0] == hit
            assert context.structuredContent["snapshot_consistency"] == "immutable_export"
            specification = context.structuredContent["items"][-1]
            listing = await client.call_tool("list_attachments", {"conversation_id": specification["conversation_id"], "message_id": specification["id"]})
            assert listing.structuredContent["items"][0]["state"] == "available"
            attachment = await client.call_tool("read_attachment", {"conversation_id": specification["conversation_id"], "message_id": specification["id"], "attachment_id": listing.structuredContent["items"][0]["id"]})
            assert not attachment.isError and attachment.content[0].type == "text"
            assert attachment.content[0].text == "Battery: 500 mAh. Thickness: awaiting confirmation."
            assert not attachment.structuredContent["content_verified"]
            assert attachment.structuredContent["local_sha256"] == hashlib.sha256(attachment.content[0].text.encode()).hexdigest()
            assert attachment.structuredContent["media_consistency"] == "current_local_files"



def main() -> None:
    resources = files("anymessage").joinpath("vendor/wechat-bootstrap")
    for name, digest in EXPECTED.items():
        assert hashlib.sha256(resources.joinpath(name).read_bytes()).hexdigest() == digest, name
    with tempfile.TemporaryDirectory() as directory:
        environment = {key: value for key, value in os.environ.items() if not key.startswith("ANYMESSAGE_")}
        root = Path(directory).resolve()
        environment.update(ANYMESSAGE_CONFIG_FILE=str(root / "config.json"), ANYMESSAGE_DATA_DIR=str(root / "no-account"), ANYMESSAGE_KEYS_FILE=str(root / "no-keys"), ANYMESSAGE_SQLCIPHER=str(root / "no-sqlcipher"))
        executable = str(Path(sys.executable).parent / "anymessage")
        for arguments in (["--version"], ["setup", "--help"], ["mcp", "config", "--format", "toml"]):
            subprocess.run([executable, *arguments], check=True, capture_output=True, text=True, env=environment)
        diagnostic = subprocess.run([executable, "doctor", "--json"], capture_output=True, text=True, env=environment)
        assert diagnostic.returncode == 1
        assert json.loads(diagnostic.stdout)["source"]["state"] == "data_missing"
        assert not (Path(directory) / "config.json").exists()
        (Path(directory) / "config.json").write_text("unrelated broken WeChat configuration")
        selected = root / "東京 export with spaces"
        selected.mkdir()
        export = selected / "fictional.json"
        (selected / "spec.txt").write_text("Battery: 500 mAh. Thickness: awaiting confirmation.")
        export.write_text(json.dumps({"id": 42, "type": "private_group", "messages": [
            {"id": 1, "type": "message", "date_unixtime": "100", "text": "Proposal: new battery."},
            {"id": 2, "type": "message", "date_unixtime": "100", "text": "Approved."},
            {"id": 3, "type": "message", "date_unixtime": "101", "text": "Specification attached.", "file": "spec.txt"},
        ]}))
        launch_cwd = root / "separate launch directory"
        launch_cwd.mkdir()
        before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
        for client in (None, *CLIENTS):
            for format in ("json", "toml"):
                for server_only in (False, True):
                    arguments = ["mcp", "config", "--telegram-export", str(export.relative_to(root)), "--format", format]
                    if client:
                        arguments += ["--client", client]
                    if server_only:
                        arguments.append("--server-only")
                    generated = subprocess.run([executable, *arguments], check=True, capture_output=True, text=True, env=environment, cwd=root)
                    assert not generated.stderr
                    document = json.loads(generated.stdout) if format == "json" else tomllib.loads(generated.stdout)
                    entry = document if server_only else document["mcpServers" if format == "json" else "mcp_servers"]["telegram-export"]
                    assert entry == {**({"type": "stdio"} if client == "cursor" else {}), "command": executable, "args": ["mcp", "--telegram-export", str(export)]}
                    if client == "cursor" and format == "json" and not server_only:
                        asyncio.run(check_export(entry, environment, launch_cwd))
        assert {path: path.read_bytes() for path in root.rglob("*") if path.is_file()} == before
        for name in ("missing.json", "invalid.json", "symlink.json"):
            candidate = selected / name
            if name == "invalid.json":
                candidate.write_text("not JSON")
            elif name == "symlink.json":
                candidate.symlink_to(export)
            generated = subprocess.run([executable, "mcp", "config", "--telegram-export", str(candidate.relative_to(root))], check=True, capture_output=True, text=True, env=environment, cwd=root)
            entry = json.loads(generated.stdout)["mcpServers"]["telegram-export"]
            assert entry["args"][-1] == str(candidate) and "env" not in entry
            rejected = subprocess.run([entry["command"], *entry["args"]], input="", capture_output=True, text=True, env={**environment, **entry.get("env", {})}, cwd=launch_cwd, timeout=30)
            assert rejected.returncode == 2 and not rejected.stdout and "telegram" in rejected.stderr.lower()
            assert "configuration file contains invalid JSON" not in rejected.stderr
        assert (root / "config.json").read_text() == "unrelated broken WeChat configuration"
    print("Installed CLI resources, all generated client shapes, unchanged seven schemas, and parsed JSON launch with actual Telegram approval and specification text passed. Invalid exports failed without WeChat fallback.")


if __name__ == "__main__":
    main()
