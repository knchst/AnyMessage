from __future__ import annotations

import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from conftest import USER


async def test_stdio_protocol(source):
    params = StdioServerParameters(command=sys.executable, args=["-m", "anymessage.server"], env=dict(os.environ))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            tools = await client.list_tools()
            assert {item.name for item in tools.tools} == {"status", "list_conversations", "get_messages", "search_messages"}
            assert all(item.annotations.readOnlyHint and item.annotations.openWorldHint is False for item in tools.tools)
            for name, args in [("status", {}), ("list_conversations", {}), ("get_messages", {"conversation_id": USER}), ("search_messages", {"query": "你好", "conversation_id": USER})]:
                result = await client.call_tool(name, args)
                assert not result.isError, result.content
                assert result.structuredContent
            invalid = await client.call_tool("get_messages", {"conversation_id": USER, "limit": -1})
            assert invalid.isError


async def test_stdio_missing_source(monkeypatch, tmp_path):
    monkeypatch.setenv("ANYMESSAGE_DATA_DIR", str(tmp_path / "missing-source"))
    monkeypatch.delenv("ANYMESSAGE_KEYS_FILE", raising=False)
    monkeypatch.delenv("ANYMESSAGE_ACCOUNT", raising=False)
    params = StdioServerParameters(command=sys.executable, args=["-m", "anymessage.server"], env=dict(os.environ))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            result = await client.call_tool("status", {})
            assert not result.isError
            source = result.structuredContent["sources"][0]
            assert source["state"] == "data_missing"
            assert source["schema_verified"] is False
