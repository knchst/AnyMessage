from __future__ import annotations

import os
import shutil
import subprocess
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from anymessage.demo import CONVERSATION, DAY_END, DAY_START, SUPPLIER_CONVERSATION, demo_settings
from anymessage.cli import main
from anymessage.settings import Settings


def test_temporary_demo_ignores_saved_account(monkeypatch, sqlcipher, tmp_path):
    config = tmp_path / "config.json"
    config.write_text("invalid saved configuration")
    monkeypatch.setenv("ANYMESSAGE_CONFIG_FILE", str(config))
    monkeypatch.setenv("ANYMESSAGE_DATA_DIR", str(tmp_path / "personal-account"))
    monkeypatch.setenv("ANYMESSAGE_KEYS_FILE", str(tmp_path / "personal-keys.json"))
    monkeypatch.setenv("ANYMESSAGE_ACCOUNT", "unrelated-account")
    monkeypatch.setenv("ANYMESSAGE_SQLCIPHER", sqlcipher)

    def forbidden_load(*args, **kwargs):
        raise AssertionError("The demo must not load saved settings")

    monkeypatch.setattr(Settings, "load", forbidden_load)
    with demo_settings() as settings:
        assert settings.account is None
        assert not settings.data_dir.is_relative_to(tmp_path)
        assert settings.keys_file.stat().st_mode & 0o777 == 0o600
        for path in settings.data_dir.rglob("*.db"):
            assert path.read_bytes()[:16] != b"SQLite format 3\x00"
        root = settings.data_dir.parent
    assert not root.exists()
    assert config.read_text() == "invalid saved configuration"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["config.json"]


async def test_demo_protocol_evidence_and_missing_drawing(monkeypatch, sqlcipher, tmp_path):
    config = tmp_path / "config.json"
    config.write_text("invalid saved configuration")
    environment = {
        **os.environ,
        "ANYMESSAGE_CONFIG_FILE": str(config),
        "ANYMESSAGE_DATA_DIR": str(tmp_path / "personal-account"),
        "ANYMESSAGE_KEYS_FILE": str(tmp_path / "personal-keys.json"),
        "ANYMESSAGE_ACCOUNT": "unrelated-account",
        "ANYMESSAGE_SQLCIPHER": sqlcipher,
    }
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "anymessage.server", "demo", "--mcp"], env=environment)
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as client:
            initialized = await client.initialize()
            assert initialized.serverInfo.name == "AnyMessage Demo"
            tools = await client.list_tools()
            assert len(tools.tools) == 7
            assert all(tool.annotations.readOnlyHint for tool in tools.tools)
            status = await client.call_tool("status", {})
            assert status.structuredContent["dataset"] == "synthetic"
            assert status.structuredContent["sources"][0]["state"] == "ready"
            conversations = await client.call_tool("list_conversations", {})
            assert {item["id"] for item in conversations.structuredContent["items"]} == {CONVERSATION, SUPPLIER_CONVERSATION}
            conversation = CONVERSATION
            history = await client.call_tool("get_messages", {"conversation_id": conversation, "limit": 20})
            messages = history.structuredContent["items"]
            assert len(messages) == 8
            approval = next(message for message in messages if (message["text"] or "").startswith("Approved:"))
            assert "500 mAh" in approval["text"]
            assert approval["sender_id"] == "Maya"
            assert history.structuredContent["next_cursor"] is None
            readable, missing = {}, {}
            for message in messages:
                if not message["attachments"]:
                    continue
                listing = await client.call_tool("list_attachments", {"conversation_id": conversation, "message_id": message["id"]})
                attachment = listing.structuredContent["items"][0]
                content = await client.call_tool("read_attachment", {"conversation_id": conversation, "message_id": message["id"], "attachment_id": attachment["id"]})
                if attachment["display_name"].endswith(".txt"):
                    assert not content.isError
                    assert content.structuredContent["content_verified"] is True
                    assert "Battery: 500 mAh (approved)" in content.content[0].text
                    assert "proposed, not confirmed" in content.content[0].text
                    readable = content.structuredContent
                else:
                    assert content.isError
                    assert content.structuredContent["missing_reason"] == "cache_file_unavailable"
                    missing = content.structuredContent
            assert readable and missing
            arguments = {"conversation_id": conversation, "query": "500", "scan_limit": 2}
            first = await client.call_tool("search_messages", arguments)
            assert not first.structuredContent["items"]
            assert first.structuredContent["incomplete"] is True
            cursor = first.structuredContent["next_cursor"]
            matches = []
            while cursor:
                result = await client.call_tool("search_messages", {**arguments, "cursor": cursor})
                matches.extend(result.structuredContent["items"])
                cursor = result.structuredContent["next_cursor"]
            assert {message["sender_id"] for message in matches} == {"Maya", "Chen"}
            scoped = await client.call_tool("search_messages", {"query": "500", "conversation_ids": [CONVERSATION, SUPPLIER_CONVERSATION], "start_time": DAY_START, "end_time": DAY_END})
            assert not scoped.isError
            assert scoped.structuredContent["scanned"] == 9
            assert len(scoped.structuredContent["items"]) == 3
            assert {item["conversation_id"] for item in scoped.structuredContent["items"]} == {CONVERSATION, SUPPLIER_CONVERSATION}
            assert not scoped.structuredContent["incomplete"]
            proposal = next(item for item in scoped.structuredContent["items"] if item["conversation_id"] == CONVERSATION and (item["text"] or "").startswith("Proposal:"))
            context = await client.call_tool("get_message_context", {"conversation_id": proposal["conversation_id"], "message_id": proposal["id"], "before": 1, "after": 2})
            assert not context.isError
            neighborhood = context.structuredContent
            assert {key: value for key, value in neighborhood["items"][neighborhood["anchor_index"]].items() if key != "cursor"} == {key: value for key, value in proposal.items() if key != "cursor"}
            assert [item["id"] for item in neighborhood["items"]] == [f"message/message_0.db:{index}" for index in range(1, 5)]
            assert neighborhood["items"][2]["sender_id"] == "Maya"
            assert neighborhood["items"][2]["text"].startswith("Approved:")
            specification = neighborhood["items"][3]
            assert specification["attachments"][0]["display_name"] == "atlas-spec-v2.txt"
            content = await client.call_tool("read_attachment", {"conversation_id": specification["conversation_id"], "message_id": specification["id"], "attachment_id": specification["attachments"][0]["id"]})
            assert content.structuredContent["content_verified"]
            assert not neighborhood["has_more_before"] and neighborhood["has_more_after"]
            dated = await client.call_tool("get_messages", {"conversation_id": SUPPLIER_CONVERSATION, "start_time": DAY_START, "end_time": DAY_END})
            assert len(dated.structuredContent["items"]) == 1
            assert "Supplier proposal:" in dated.structuredContent["items"][0]["text"]
    assert config.read_text() == "invalid saved configuration"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["config.json"]


def test_demo_walkthrough_and_missing_sqlcipher(monkeypatch, sqlcipher, tmp_path):
    monkeypatch.setenv("ANYMESSAGE_SQLCIPHER", sqlcipher)
    monkeypatch.setenv("ANYMESSAGE_CONFIG_FILE", str(tmp_path / "bad-config.json"))
    (tmp_path / "bad-config.json").write_text("invalid JSON")
    result = subprocess.run([sys.executable, "-m", "anymessage.server", "demo"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "No AI model is called" in result.stdout
    assert "Reader: ready" in result.stdout
    assert "Battery: 500 mAh (approved)" in result.stdout
    assert "cache_file_unavailable" in result.stdout
    assert "Incomplete: True" in result.stdout
    assert "3 matches in 9 scanned messages" in result.stdout
    assert SUPPLIER_CONVERSATION in result.stdout
    assert "November 10 follow-up is outside" in result.stdout
    assert "Context for search hit atlas-demo@chatroom | message/message_0.db:2" in result.stdout
    assert "Neighbor | message/message_0.db:3 | Maya | Approved:" in result.stdout
    assert "Neighbor | message/message_0.db:4 | Chen | Attachment: atlas-spec-v2.txt" in result.stdout
    assert "Context attachment verified: True" in result.stdout
    assert "Anchor index: 1. More before: False. More after: True" in result.stdout
    assert "Snapshot consistency: per_query" in result.stdout
    monkeypatch.setenv("ANYMESSAGE_SQLCIPHER", str(tmp_path / "not-installed"))
    failed = subprocess.run([sys.executable, "-m", "anymessage.server", "demo"], capture_output=True, text=True, timeout=10)
    assert failed.returncode == 2
    assert "brew install sqlcipher" in failed.stderr
    assert "Traceback" not in failed.stderr


def test_demo_rejects_plain_sqlite(monkeypatch, capsys):
    executable = shutil.which("sqlite3")
    assert executable is not None, "macOS SQLite is required for this compatibility check"
    monkeypatch.setenv("ANYMESSAGE_SQLCIPHER", executable)
    assert main(["demo"]) == 2
    assert "SQLCipher" in capsys.readouterr().err
