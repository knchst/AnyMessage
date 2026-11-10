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

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


EXPECTED = {
    "bootstrap_keys.py": "ce8596107df3ca3715c8b63b522bbcc84148207217fade3b642975323c65b31c",
    "capture_keys.py": "0b0b9f9296bf02219c07537827ed9ce97671611ffeab36572bedb267a7704d65",
    "LICENSE": "72380798af61d6131287b0dd3c8dc5345535df641002fece3a02d7ef109c5f8e",
}


async def check_export(executable: str, export: Path, environment: dict[str, str]) -> None:
    parameters = StdioServerParameters(command=executable, args=["mcp", "--telegram-export", str(export)], env=environment)
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            assert len((await client.list_tools()).tools) == 7
            status = await client.call_tool("status", {})
            assert status.structuredContent["sources"][0]["source"] == "telegram_export"
            assert status.structuredContent["sources"][0]["state"] == "ready"
            search = await client.call_tool("search_messages", {"query": "proposal", "conversation_id": "telegram:private_group:42"})
            hit = search.structuredContent["items"][0]
            context = await client.call_tool("get_message_context", {"conversation_id": hit["conversation_id"], "message_id": hit["id"], "before": 0, "after": 1})
            assert not context.isError and context.structuredContent["items"][-1]["text"] == "Approved."
            assert context.structuredContent["snapshot_consistency"] == "immutable_export"
            media = await client.call_tool("read_attachment", {"conversation_id": hit["conversation_id"], "message_id": hit["id"], "attachment_id": "a" * 32})
            assert media.isError and media.structuredContent["state"] == "unsupported" and not media.structuredContent["content_verified"]


def main() -> None:
    resources = files("anymessage").joinpath("vendor/wechat-bootstrap")
    for name, digest in EXPECTED.items():
        assert hashlib.sha256(resources.joinpath(name).read_bytes()).hexdigest() == digest, name
    with tempfile.TemporaryDirectory() as directory:
        environment = {key: value for key, value in os.environ.items() if not key.startswith("ANYMESSAGE_")}
        environment.update(ANYMESSAGE_CONFIG_FILE=str(Path(directory) / "config.json"), ANYMESSAGE_DATA_DIR=str(Path(directory) / "no-account"))
        executable = str(Path(sys.executable).parent / "anymessage")
        for arguments in (["--version"], ["setup", "--help"], ["mcp", "config", "--format", "toml"]):
            subprocess.run([executable, *arguments], check=True, capture_output=True, text=True, env=environment)
        diagnostic = subprocess.run([executable, "doctor", "--json"], capture_output=True, text=True, env=environment)
        assert diagnostic.returncode == 1
        assert json.loads(diagnostic.stdout)["source"]["state"] == "data_missing"
        assert not (Path(directory) / "config.json").exists()
        (Path(directory) / "config.json").write_text("unrelated broken WeChat configuration")
        export = Path(directory) / "fictional.json"
        export.write_text(json.dumps({"id": 42, "type": "private_group", "messages": [
            {"id": 1, "type": "message", "date_unixtime": "100", "text": "Proposal: new battery."},
            {"id": 2, "type": "message", "date_unixtime": "100", "text": "Approved."},
        ]}))
        asyncio.run(check_export(executable, export, environment))
    print("Installed CLI, unchanged bootstrap resources, and key-free export MCP context passed.")


if __name__ == "__main__":
    main()
