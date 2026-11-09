from __future__ import annotations

import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from conftest import USER, TABLE, create_database
from test_attachments import add_file, image_bytes
import base64
import hashlib
import json
from datetime import datetime


async def check_attachment_roundtrips(client, message_id):
    listing = await client.call_tool("list_attachments", {"conversation_id": USER, "message_id": message_id})
    assert not listing.isError
    attachment_id = listing.structuredContent["items"][0]["id"]
    arguments = {"conversation_id": USER, "message_id": message_id, "attachment_id": attachment_id}
    text = await client.call_tool("read_attachment", arguments)
    assert not text.isError and text.content[0].type == "text"
    assert text.content[0].text == "attachment text\nsecond line"
    binary = await client.call_tool("read_attachment", {**arguments, "as_binary": True})
    assert not binary.isError and binary.content[0].type == "resource"
    assert base64.b64decode(binary.content[0].resource.blob) == b"attachment text\nsecond line"
    listing = await client.call_tool(
        "list_attachments", {"conversation_id": USER, "message_id": "message/message_0.db:3"}
    )
    item = listing.structuredContent["items"][-1]
    image = await client.call_tool(
        "read_attachment",
        {"conversation_id": USER, "message_id": "message/message_0.db:3", "attachment_id": item["id"]},
    )
    assert not image.isError and image.content[0].type == "image"
    assert image.content[0].mimeType == "image/jpeg"
    assert base64.b64decode(image.content[0].data) == image_bytes()
    assert image.structuredContent["variant"] == "thumbnail"
    for result in (text, binary, image):
        assert json.loads(result.content[-1].text) == result.structuredContent


def prepare_attachments(source):
    _, _, message_id = add_file(source)
    _, root, _, keys = source
    digest = "a" * 32
    packed = b"\x1a\x22\x22\x20" + digest.encode()
    key = keys.get("message/message_0.db", {}).get("enc_key")
    create_database(
        root / "message/message_0.db",
        f'ALTER TABLE "{TABLE}" ADD COLUMN packed_info_data BLOB; UPDATE "{TABLE}" SET packed_info_data=X\'{packed.hex()}\' WHERE local_id=3;',
        key,
    )
    folder = (
        root.parent
        / "msg/attach"
        / hashlib.md5(USER.encode()).hexdigest()
        / datetime.fromtimestamp(101).strftime("%Y-%m")
        / "Img"
    )
    folder.mkdir(parents=True)
    (folder / (digest + "_t.dat")).write_bytes(image_bytes())
    return message_id


async def test_stdio_protocol(source):
    message_id = prepare_attachments(source)
    params = StdioServerParameters(command=sys.executable, args=["-m", "anymessage.server"], env=dict(os.environ))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            tools = await client.list_tools()
            assert {item.name for item in tools.tools} == {
                "status",
                "list_conversations",
                "get_messages",
                "search_messages",
                "list_attachments",
                "read_attachment",
            }
            assert all(
                item.annotations.readOnlyHint and item.annotations.openWorldHint is False for item in tools.tools
            )
            for name, args in [
                ("status", {}),
                ("list_conversations", {}),
                ("get_messages", {"conversation_id": USER}),
                ("search_messages", {"query": "你好", "conversation_id": USER}),
            ]:
                result = await client.call_tool(name, args)
                assert not result.isError, result.content
                assert result.structuredContent
            await check_attachment_roundtrips(client, message_id)
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


async def test_loopback_http_protocol(source):
    message_id = prepare_attachments(source)
    import asyncio
    import socket

    import httpx
    from mcp.client.streamable_http import streamable_http_client

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "anymessage.server",
        "mcp",
        "--transport",
        "streamable-http",
        "--port",
        str(port),
        env=dict(os.environ),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        async with httpx.AsyncClient() as probe:
            for _ in range(100):
                if process.returncode is not None:
                    raise AssertionError("HTTP server exited before accepting connections")
                try:
                    await probe.get(url)
                    break
                except httpx.ConnectError:
                    await asyncio.sleep(0.05)
            else:
                raise AssertionError("HTTP server did not start")
        async with streamable_http_client(url) as (read, write, _):
            async with ClientSession(read, write) as client:
                await client.initialize()
                tools = await client.list_tools()
                assert {tool.name for tool in tools.tools} == {
                    "status",
                    "list_conversations",
                    "get_messages",
                    "search_messages",
                    "list_attachments",
                    "read_attachment",
                }
                result = await client.call_tool("status", {})
                assert not result.isError
                assert result.structuredContent["sources"][0]["state"] == "ready"
                await check_attachment_roundtrips(client, message_id)
    finally:
        process.terminate()
        await asyncio.wait_for(process.wait(), timeout=10)
