"""Temporary fictional data read through the production MCP tools."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult

from anymessage.settings import Settings, resolve_sqlcipher
from anymessage.wechat import sql_text


CONVERSATION = "atlas-demo@chatroom"
SUPPLIER_CONVERSATION = "atlas-supplier-demo@chatroom"
DAY_START = int(datetime(2026, 11, 9, tzinfo=timezone.utc).timestamp())
DAY_END = DAY_START + 86400
SPECIFICATION = b"Atlas pilot specification v2\nBattery: 500 mAh (approved)\nQuantity: 50 units\nDelivery: November 26 (proposed, not confirmed)\nEnclosure thickness: pending updated drawing\n"
MESSAGES = (
    (1, "Maya", "Baseline approved: 400 mAh battery, pilot batch of 50."),
    (2, "Chen", "Proposal: move to 500 mAh. I will check the quote and thickness."),
    (3, "Maya", "Approved: use 500 mAh for the pilot. Keep quantity at 50."),
    (4, "Chen", "atlas-spec-v2.txt"),
    (5, "Maya", "Please confirm final thickness against the drawing before we release the order."),
    (6, "Chen", "atlas-enclosure.pdf"),
    (7, "Chen", "I proposed November 26 delivery, but the factory has not confirmed. Quote still pending."),
    (8, "Maya", "Next review: approve final thickness only after the drawing is available."),
)
SUPPLIER_MESSAGES = (
    (1, DAY_START + 9 * 3600 + 90, "Supplier proposal: 500 mAh needs a revised quote; delivery remains unconfirmed."),
    (2, DAY_END + 9 * 3600, "Supplier follow-up: enclosure thickness still awaits the drawing."),
)


def demo_sqlcipher() -> str:
    executable = resolve_sqlcipher(os.environ.get("ANYMESSAGE_SQLCIPHER"))
    if executable is None:
        raise ValueError("The demo needs SQLCipher, but no WeChat installation or account keys. Install it with brew install sqlcipher, or correct ANYMESSAGE_SQLCIPHER.")
    version = subprocess.run([executable, ":memory:"], input=".bail on\nPRAGMA cipher_version;\n", capture_output=True, text=True, timeout=20)
    if version.returncode or not version.stdout.strip():
        raise ValueError("The selected executable does not provide SQLCipher encryption. Install SQLCipher with brew install sqlcipher, or correct ANYMESSAGE_SQLCIPHER.")
    return executable


@contextmanager
def demo_settings() -> Iterator[Settings]:
    executable = demo_sqlcipher()
    with tempfile.TemporaryDirectory(prefix="anymessage-demo-") as directory:
        base = Path(directory)
        root = base / "db_storage"
        table = "Msg_" + hashlib.md5(CONVERSATION.encode()).hexdigest()
        supplier_table = "Msg_" + hashlib.md5(SUPPLIER_CONVERSATION.encode()).hexdigest()
        started_at = int(datetime(2026, 11, 9, 9, tzinfo=timezone.utc).timestamp())
        attachments = {4: SPECIFICATION, 6: b"Fictional drawing intentionally absent from the demo cache."}
        sqls = {
            "session/session.db": f"CREATE TABLE SessionTable(username TEXT,sort_timestamp INTEGER,summary TEXT); INSERT INTO SessionTable VALUES({sql_text(CONVERSATION)},{started_at + len(MESSAGES) * 60},'Atlas pilot specification review');",
            "contact/contact.db": f"CREATE TABLE contact(username TEXT,remark TEXT,nick_name TEXT,alias TEXT); INSERT INTO contact VALUES({sql_text(CONVERSATION)},'Atlas pilot project (fictional)','','');",
            "message/message_0.db": f'CREATE TABLE "{table}"(local_id INTEGER PRIMARY KEY,server_id INTEGER,local_type INTEGER,sort_seq INTEGER,real_sender_id INTEGER,create_time INTEGER,message_content BLOB,WCDB_CT_message_content INTEGER,compress_content BLOB); CREATE TABLE Name2Id(user_name TEXT); INSERT INTO Name2Id VALUES(\'Maya\'),(\'Chen\');',
            "hardlink/hardlink.db": "CREATE TABLE dir2id(username TEXT); INSERT INTO dir2id VALUES('2026-11'); CREATE TABLE file_hardlink_info_v4(md5 TEXT,file_name TEXT,file_size INTEGER,dir1 INTEGER,dir2 INTEGER);",
        }
        for identity, sender, text in MESSAGES:
            kind = 1
            if identity in attachments:
                data = attachments[identity]
                digest = hashlib.md5(data).hexdigest()
                sqls["hardlink/hardlink.db"] += f"INSERT INTO file_hardlink_info_v4 VALUES('{digest}',{sql_text(text)},{len(data)},1,0);"
                text = f"<msg><appmsg><title>{text}</title><type>6</type><md5>{digest}</md5><appattach><totallen>{len(data)}</totallen></appattach></appmsg></msg>"
                kind = 25769803825
            timestamp = started_at + identity * 60
            sqls["message/message_0.db"] += f'INSERT INTO "{table}" VALUES({identity},{1000 + identity},{kind},{identity},{1 if sender == "Maya" else 2},{timestamp},X\'{text.encode().hex()}\',0,NULL);'
        sqls["session/session.db"] += f"INSERT INTO SessionTable VALUES({sql_text(SUPPLIER_CONVERSATION)},{SUPPLIER_MESSAGES[-1][1]},'Atlas supplier follow-up');"
        sqls["contact/contact.db"] += f"INSERT INTO contact VALUES({sql_text(SUPPLIER_CONVERSATION)},'Atlas supplier (fictional)','','');"
        sqls["message/message_0.db"] += f'CREATE TABLE "{supplier_table}" AS SELECT * FROM "{table}" WHERE 0;'
        for identity, timestamp, text in SUPPLIER_MESSAGES:
            sqls["message/message_0.db"] += f'INSERT INTO "{supplier_table}" VALUES({identity},{2000 + identity},1,{identity},2,{timestamp},X\'{text.encode().hex()}\',0,NULL);'
        cache = base / "msg/file/2026-11/atlas-spec-v2.txt"
        cache.parent.mkdir(parents=True)
        cache.write_bytes(SPECIFICATION)
        keys = {}
        for relative, sql in sqls.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            key = secrets.token_hex(32)
            preamble = f'.bail on\nPRAGMA key="x\'{key}\'";\nPRAGMA cipher_compatibility=4;\n'
            result = subprocess.run([executable, str(path)], input=preamble + sql, capture_output=True, text=True, timeout=20)
            if result.returncode:
                raise ValueError("SQLCipher could not create the temporary fictional dataset.")
            header = path.read_bytes()[:16]
            if header == b"SQLite format 3\x00":
                raise ValueError("SQLCipher did not encrypt the temporary demo database.")
            keys[relative] = {"enc_key": key, "salt": header.hex()}
        manifest = base / "manifest.json"
        manifest.write_text(json.dumps(keys))
        manifest.chmod(0o600)
        yield Settings(root, None, manifest, executable)


async def print_demo_evidence() -> None:
    executable = demo_sqlcipher()
    print("AnyMessage demo. Fictional project data, real MCP tool calls. No AI model is called.", flush=True)
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "anymessage.server", "demo", "--mcp"], env={**os.environ, "ANYMESSAGE_SQLCIPHER": executable})
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()

            async def call(name: str, arguments: dict[str, Any]) -> CallToolResult:
                result = await client.call_tool(name, arguments)
                if result.isError:
                    raise ValueError(f"The demo could not complete {name}.")
                return result

            ready = await call("status", {})
            print(f"Dataset: {ready.structuredContent['dataset']}. Reader: {ready.structuredContent['sources'][0]['state']}.")
            conversations = await call("list_conversations", {})
            print("Conversations: " + ", ".join(item["display_name"] for item in conversations.structuredContent["items"]))
            print("\nQuestion to investigate: What changed in Atlas, and what still needs confirmation?\n")
            print("History range: 2026-11-09 UTC, start inclusive and next midnight exclusive.")
            history = await call("get_messages", {"conversation_id": CONVERSATION, "start_time": DAY_START, "end_time": DAY_END})
            for message in reversed(history.structuredContent["items"]):
                timestamp = datetime.fromtimestamp(message["timestamp"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                body = message["text"] or "Attachment: " + ", ".join(item["display_name"] for item in message["attachments"])
                print(f"{message['id']} | {message['sender_id']} | {timestamp}\n  {body}")
            print("\nVerified local attachment from message/message_0.db:4:")
            listing = await call("list_attachments", {"conversation_id": CONVERSATION, "message_id": "message/message_0.db:4"})
            attachment = listing.structuredContent["items"][0]
            content = await call("read_attachment", {"conversation_id": CONVERSATION, "message_id": "message/message_0.db:4", "attachment_id": attachment["id"]})
            print(content.content[0].text)
            print(f"Read state: {content.structuredContent['state']}. Content verified: {content.structuredContent['content_verified']}.")
            missing = await call("list_attachments", {"conversation_id": CONVERSATION, "message_id": "message/message_0.db:6"})
            drawing = missing.structuredContent["items"][0]
            print(f"\nUnavailable drawing: {drawing['display_name']} | {drawing['state']} | {drawing.get('missing_reason')}")
            search = await call("search_messages", {"conversation_id": CONVERSATION, "query": "500", "scan_limit": 2})
            evidence = search.structuredContent
            print(f"\nBounded search for '500': {len(evidence['items'])} matches in {evidence['scanned']} scanned messages. Incomplete: {evidence['incomplete']}.")
            print("An incomplete search does not establish that older matching messages are absent.")
            selected = await call("search_messages", {"conversation_ids": [CONVERSATION, SUPPLIER_CONVERSATION], "query": "500", "start_time": DAY_START, "end_time": DAY_END})
            evidence = selected.structuredContent
            if len(history.structuredContent["items"]) != 8 or len(evidence["items"]) != 3 or {item["conversation_id"] for item in evidence["items"]} != {CONVERSATION, SUPPLIER_CONVERSATION}:
                raise ValueError("The scoped demo did not return the seeded evidence.")
            print(f"\nSelected-chat search for '500' on 2026-11-09 UTC: {len(evidence['items'])} matches in {evidence['scanned']} scanned messages.")
            for message in evidence["items"]:
                print(f"{message['conversation_id']} | {message['id']} | {message['text']}")
            print("The supplier's November 10 follow-up is outside this search range.")
    print("\nConnect your AI to the same fictional dataset with:")
    print("uvx --from git+https://github.com/knchst/AnyMessage anymessage demo --mcp")
    print("Ask it to distinguish approved changes, proposals, and missing evidence, with message IDs.")
