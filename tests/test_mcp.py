from __future__ import annotations

import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from conftest import OTHER_USER, USER, TABLE, create_database
from test_attachments import RECOVERY_HINT, add_file, image_bytes
import base64
import hashlib
import json
from datetime import datetime


async def check_scoped_search(client, tools):
    schema = next(tool.inputSchema for tool in tools.tools if tool.name == "search_messages")
    dates = schema["properties"]["start_time"]["anyOf"]
    assert {"type": "integer", "minimum": -(2**63), "maximum": 2**63 - 1} in dates
    selection = next(value for value in schema["properties"]["conversation_ids"]["anyOf"] if value["type"] == "array")
    assert selection["minItems"] == 1 and selection["maxItems"] == 8
    assert selection["items"]["maxLength"] == 512
    assert schema["required"] == ["query"]
    arguments = {"query": "你好", "conversation_ids": [USER, OTHER_USER], "start_time": 100, "end_time": 104, "scan_limit": 1}
    first = await client.call_tool("search_messages", arguments)
    assert not first.isError
    assert first.structuredContent["scanned"] == 1 and first.structuredContent["items"] == []
    cursor = first.structuredContent["next_cursor"]
    hits = []
    while cursor:
        result = await client.call_tool("search_messages", {**arguments, "cursor": cursor})
        assert not result.isError
        hits.extend(result.structuredContent["items"])
        cursor = result.structuredContent["next_cursor"]
    assert len(hits) == 5
    assert {item["conversation_id"] for item in hits} == {USER, OTHER_USER}
    history = await client.call_tool("get_messages", {"conversation_id": USER, "start_time": 100, "end_time": 101})
    assert not history.isError and len(history.structuredContent["items"]) == 3
    for change in [{"start_time": True}, {"end_time": 104.0}, {"start_time": "100"},
                   {"start_time": 2**63}, {"end_time": 100}, {"conversation_ids": []},
                   {"conversation_ids": [USER, USER]}, {"conversation_ids": [str(i) for i in range(9)]},
                   {"conversation_id": USER}, {"conversation_ids": None},
                   {"conversation_ids": ["missing-chat", USER]}]:
        invalid = await client.call_tool("search_messages", {**arguments, **change})
        assert invalid.isError, change
    assert (await client.call_tool("search_messages", {"query": "你好", "cursor": first.structuredContent["next_cursor"]})).isError
    assert (await client.call_tool("search_messages", {**arguments, "end_time": 105, "cursor": first.structuredContent["next_cursor"]})).isError


async def check_missing_attachment(client, message_id, reason):
    listing = await client.call_tool("list_attachments", {"conversation_id": USER, "message_id": message_id})
    assert not listing.isError
    assert json.loads(listing.content[-1].text) == listing.structuredContent
    item = listing.structuredContent["items"][0]
    result = await client.call_tool(
        "read_attachment",
        {"conversation_id": USER, "message_id": message_id, "attachment_id": item["id"]},
    )
    assert result.isError
    assert len(result.content) == 1 and result.content[0].type == "text"
    assert json.loads(result.content[0].text) == result.structuredContent
    for metadata in (item, result.structuredContent):
        assert metadata["state"] == "attachment_missing"
        assert metadata["missing_reason"] == reason
        assert metadata["recovery_hint"] == RECOVERY_HINT
    return item["id"]


async def check_message_context(client, tools):
    schema = next(tool.inputSchema for tool in tools.tools if tool.name == "get_message_context")
    assert schema["required"] == ["conversation_id", "message_id"]
    for name in ("before", "after"):
        assert schema["properties"][name] == {"default": 3, "maximum": 50, "minimum": 0, "title": name.title(), "type": "integer"}
    search = await client.call_tool("search_messages", {"query": "你好", "conversation_id": USER})
    hit = search.structuredContent["items"][0]
    arguments = {"conversation_id": hit["conversation_id"], "message_id": hit["id"], "before": 1, "after": 1}
    context = await client.call_tool("get_message_context", arguments)
    assert not context.isError
    result = context.structuredContent
    assert [item["id"] for item in result["items"]] == ["message/message_1.db:1", "message/message_0.db:2", "message/message_0.db:3"]
    assert result["anchor_index"] == 1 and result["has_more_before"] and result["has_more_after"]
    assert result["items"][1] == hit
    assert json.loads(context.content[0].text) == result
    for change in ({"before": True}, {"after": 1.0}, {"before": "1"}, {"after": -1},
                   {"before": 51}, {"message_id": 1}, {"conversation_id": 1},
                   {"message_id": "message/message_0.db:01"}, {"message_id": "message/message_0.db:999"}):
        assert (await client.call_tool("get_message_context", {**arguments, **change})).isError


async def check_attachment_roundtrips(client, message_id, source):
    listing = await client.call_tool("list_attachments", {"conversation_id": USER, "message_id": message_id})
    assert not listing.isError
    attachment_id = listing.structuredContent["items"][0]["id"]
    arguments = {"conversation_id": USER, "message_id": message_id, "attachment_id": attachment_id}
    text = await client.call_tool("read_attachment", arguments)
    assert not text.isError and text.content[0].type == "text"
    assert text.content[0].text == "attachment text\nsecond line"
    assert text.structuredContent.get("text") == "attachment text\nsecond line"
    page = await client.call_tool("read_attachment", {**arguments, "offset": 11, "limit": 4})
    assert page.structuredContent.get("text") == "text"
    assert page.structuredContent["next_offset"] == 15
    assert page.content[0].text == page.structuredContent["text"]
    binary = await client.call_tool("read_attachment", {**arguments, "as_binary": True})
    assert not binary.isError and binary.content[0].type == "resource"
    assert base64.b64decode(binary.content[0].resource.blob) == b"attachment text\nsecond line"
    assert "text" not in binary.structuredContent
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
    assert "text" not in image.structuredContent
    for result in (text, binary, image):
        assert json.loads(result.content[-1].text) == result.structuredContent
        assert "missing_reason" not in result.structuredContent
        assert "recovery_hint" not in result.structuredContent
    image_message_id = "message/message_0.db:3"
    assert (
        await check_missing_attachment(client, image_message_id, "cache_file_unavailable")
        == listing.structuredContent["items"][0]["id"]
    )
    _, root, _, keys = source
    create_database(root / "hardlink/hardlink.db", "DELETE FROM file_hardlink_info_v4;", None)
    assert await check_missing_attachment(client, message_id, "file_index_no_match") == attachment_id
    key = keys.get("message/message_0.db", {}).get("enc_key")
    create_database(
        root / "message/message_0.db",
        f'UPDATE "{TABLE}" SET packed_info_data=NULL WHERE local_id=3;',
        key,
    )
    create_database(
        root / "message/message_resource.db",
        "CREATE TABLE ChatName2Id(user_name TEXT); CREATE TABLE MessageResourceInfo(chat_id INTEGER,message_local_id INTEGER,message_svr_id INTEGER,packed_info BLOB);",
        None,
    )
    await check_missing_attachment(client, image_message_id, "image_resource_index_no_match")


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
                "get_message_context",
                "search_messages",
                "list_attachments",
                "read_attachment",
            }
            assert all(
                item.annotations.readOnlyHint and item.annotations.openWorldHint is False for item in tools.tools
            )
            await check_scoped_search(client, tools)
            await check_message_context(client, tools)
            for name, args in [
                ("status", {}),
                ("list_conversations", {}),
                ("get_messages", {"conversation_id": USER}),
                ("search_messages", {"query": "你好", "conversation_id": USER}),
            ]:
                result = await client.call_tool(name, args)
                assert not result.isError, result.content
                assert result.structuredContent
            await check_attachment_roundtrips(client, message_id, source)
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
                    "get_message_context",
                    "search_messages",
                    "list_attachments",
                    "read_attachment",
                }
                await check_scoped_search(client, tools)
                await check_message_context(client, tools)
                result = await client.call_tool("status", {})
                assert not result.isError
                assert result.structuredContent["sources"][0]["state"] == "ready"
                await check_attachment_roundtrips(client, message_id, source)
    finally:
        process.terminate()
        await asyncio.wait_for(process.wait(), timeout=10)
