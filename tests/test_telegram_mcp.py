from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import socket
import sys

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
import pytest

from anymessage.errors import SourceError
from anymessage.server import create_server
from anymessage.settings import Settings
from test_telegram_export import FIXTURE, GROUP, PERSON


def test_explicit_source_bypasses_wechat(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Telegram startup touched WeChat configuration or discovery")
    monkeypatch.setattr(Settings, "load", forbidden)
    monkeypatch.setattr("anymessage.settings.resolve_sqlcipher", forbidden)
    monkeypatch.setattr("anymessage.server.WeChat", forbidden)
    server = create_server(telegram_export=FIXTURE)
    assert server.name == "AnyMessage"
    with pytest.raises(SourceError, match="request_invalid"):
        create_server(telegram_export=FIXTURE, demo_settings=Settings(Path("unused"), None, None, None))


async def test_all_seven_input_schemas_match_default(tmp_path):
    default = create_server(demo_settings=Settings(tmp_path / "no-data", None, None, None))
    export = create_server(telegram_export=FIXTURE)
    default_tools = await default.list_tools()
    export_tools = await export.list_tools()
    assert {tool.name: tool.inputSchema for tool in default_tools} == {tool.name: tool.inputSchema for tool in export_tools}
    assert len(export_tools) == 7
    assert all(tool.annotations.readOnlyHint and tool.annotations.destructiveHint is False and tool.annotations.idempotentHint and tool.annotations.openWorldHint is False for tool in export_tools)


@asynccontextmanager
async def connect(transport, tmp_path):
    broken = tmp_path / "unrelated-config.json"
    broken.write_text("PRIVATE_INVALID_CONFIG [")
    environment = {**os.environ, "ANYMESSAGE_CONFIG_FILE": str(broken), "ANYMESSAGE_DATA_DIR": str(tmp_path / "never-discover"), "ANYMESSAGE_SQLCIPHER": str(tmp_path / "never-execute")}
    arguments = ["-m", "anymessage.server", "mcp", "--telegram-export", str(FIXTURE)]
    if transport == "stdio":
        async with stdio_client(StdioServerParameters(command=sys.executable, args=arguments, env=environment)) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                yield client
        return
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    process = await asyncio.create_subprocess_exec(sys.executable, *arguments, "--transport", "streamable-http", "--port", str(port), env=environment, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        async with httpx.AsyncClient() as probe:
            for _ in range(100):
                if process.returncode is not None:
                    raise AssertionError("Export HTTP server exited before accepting connections")
                try:
                    await probe.get(url)
                    break
                except httpx.ConnectError:
                    await asyncio.sleep(0.05)
            else:
                raise AssertionError("Export HTTP server did not start")
        async with streamable_http_client(url) as (read, write, _):
            async with ClientSession(read, write) as client:
                await client.initialize()
                yield client
    finally:
        process.terminate()
        await asyncio.wait_for(process.wait(), timeout=10)


@pytest.mark.parametrize("transport", ["stdio", "streamable-http"])
async def test_real_export_mcp_workflow(transport, tmp_path):
    async with connect(transport, tmp_path) as client:
        tools = await client.list_tools()
        assert len(tools.tools) == 7
        status = await client.call_tool("status", {})
        assert not status.isError
        assert status.structuredContent["sources"][0]["source"] == "telegram_export"
        assert status.structuredContent["sources"][0]["state"] == "ready"
        listing = await client.call_tool("list_conversations", {})
        assert {item["id"] for item in listing.structuredContent["items"]} == {GROUP, PERSON}
        search = await client.call_tool("search_messages", {"query": "proposal", "conversation_id": GROUP})
        assert not search.isError
        hit = search.structuredContent["items"][0]
        assert hit["id"] == "2" and hit["type"] == "message"
        context = await client.call_tool("get_message_context", {"conversation_id": hit["conversation_id"], "message_id": hit["id"], "before": 1, "after": 2})
        assert not context.isError
        assert [row["id"] for row in context.structuredContent["items"]] == ["1", "2", "3", "4"]
        assert context.structuredContent["items"][-1]["text"].startswith("Approved:")
        assert context.structuredContent["anchor_index"] == 1
        assert context.structuredContent["snapshot_consistency"] == "immutable_export"
        bounded = await client.call_tool("get_messages", {"conversation_id": GROUP, "start_time": 1794214920, "end_time": 1794214980})
        assert [row["id"] for row in bounded.structuredContent["items"]] == ["3", "2"]
        arguments = {"query": "proposal", "conversation_ids": [GROUP, PERSON], "scan_limit": 1}
        first = await client.call_tool("search_messages", arguments)
        assert first.structuredContent["items"] == [] and first.structuredContent["scanned"] == 1
        assert first.structuredContent["next_cursor"]
        page = first
        hits = []
        while page.structuredContent["next_cursor"]:
            page = await client.call_tool("search_messages", {**arguments, "cursor": page.structuredContent["next_cursor"]})
            assert not page.isError
            hits.extend(page.structuredContent["items"])
        assert [row["id"] for row in hits] == ["2"]
        assert not page.structuredContent["incomplete"]
        media = await client.call_tool("list_attachments", {"conversation_id": GROUP, "message_id": "5"})
        assert not media.isError and media.structuredContent["items"] == []
        assert media.structuredContent["metadata_state"] == "unsupported" and media.structuredContent["media_declared"]
        unsupported = await client.call_tool("read_attachment", {"conversation_id": GROUP, "message_id": "5", "attachment_id": "a" * 32})
        assert unsupported.isError and unsupported.structuredContent["state"] == "unsupported"
        assert unsupported.structuredContent["content_verified"] is False
        assert len(unsupported.content) == 1 and unsupported.content[0].type == "text"
        assert "attachment_id" not in unsupported.structuredContent
        for result in (status, listing, search, context, bounded, first, media, unsupported):
            assert json.loads(result.content[-1].text) == result.structuredContent
        for name, arguments in (("search_messages", {"query": "proposal"}), ("get_message_context", {"conversation_id": PERSON, "message_id": "5"}), ("get_messages", {"conversation_id": GROUP, "start_time": True}), ("get_message_context", {"conversation_id": GROUP, "message_id": "02"}), ("search_messages", {"query": "different", "conversation_ids": [GROUP, PERSON], "cursor": first.structuredContent["next_cursor"]})):
            assert (await client.call_tool(name, arguments)).isError
