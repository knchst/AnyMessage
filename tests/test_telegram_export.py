from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import random
import subprocess
import sys

import pytest

from anymessage.errors import SourceError
from anymessage.telegram_export import MAX_EXPORT_BYTES, TelegramExport


FIXTURE = Path(__file__).resolve().parents[1] / "examples/telegram-fictional.json"
GROUP = "telegram:private_group:42"
PERSON = "telegram:personal_chat:42"


def message(number, timestamp=100, text="river", **extra):
    return {"id": number, "type": "message", "date_unixtime": str(timestamp), "text": text, **extra}


def chat(messages, **extra):
    return {"id": 42, "type": "private_group", "name": "Fictional", "messages": messages, **extra}


def load(tmp_path, value):
    path = tmp_path / "export.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return TelegramExport(path)


@pytest.fixture
def export(tmp_path):
    payload = {"personal_information": {"ignored": "SECRET_ACCOUNT_DATA"}, "chats": {"list": [chat([
        message(8, 103, "", file="../../NEVER_OPEN_PRIVATE_FILE"),
        message(5, 101, "caption river", photo="https://never-download.invalid/image"),
        message(1, 100, ["Stra", {"type": "bold", "text": "ße", "href": "SECRET_ENTITY_METADATA"}, " 你好 % _"], text_entities={"invalid": "ignored"}),
        {"id": 4, "type": "service", "date_unixtime": "101", "actor_id": "user-actor", "actor": "Actor", "from_id": "user-other", "from": "Other", "action": "proximity_reached"},
        message(6, 101, rich_message={"blocks": [{"html": "SECRET_RICH_BODY"}]}),
        {"id": 9, "type": "unsupported"},
        message(3, 101, "river tie"),
        message(7, 102, "", poll={"question": "SECRET_POLL"}),
        message(2, 100, ""),
    ]), chat([message(3, 101, "river other"), message(1, 100, "other")], type="personal_chat")]}, "left_chats": {"list": [chat([], id=-10, name=None, type="public_channel")]}}
    return load(tmp_path, payload)


def test_normalization_and_coverage(export):
    status = export.status()
    assert status["chat_count"] == 3 and status["dated_message_count"] == 10
    assert status["undated_unsupported_count"] == status["rich_message_count"] == 1
    assert status["max_export_bytes"] == 16 * 1024 * 1024 and not status["text_only"]
    listing = export.list_conversations()
    assert [item["id"] for item in listing["items"]] == [GROUP, PERSON, "telegram:public_channel:-10"]
    assert listing["items"][-1]["updated_at"] is None
    assert listing["items"][-1]["chat_type"] == "public_channel" and not listing["items"][-1]["is_group"]
    assert export.list_conversations(limit=1)["next_offset"] == 1
    assert export.list_conversations(offset=100)["items"] == []
    result = export.get_messages(GROUP)
    assert [row["id"] for row in result["items"]] == ["8", "7", "6", "5", "4", "3", "2", "1"]
    rows = {row["id"]: row for row in result["items"]}
    assert rows["1"]["text"] == "Straße 你好 % _"
    assert rows["2"]["text"] == "" and rows["2"]["content_state"] == "text"
    assert rows["4"]["sender_id"] == "user-actor" and rows["4"]["sender_name"] == "Actor"
    assert rows["4"]["type"] == "service" and rows["4"]["text"] is None
    assert rows["5"]["text"] == "caption river" and rows["5"]["attachment_state"] == "available"
    assert rows["6"]["text"] is None and rows["6"]["unsupported_reason"] == "rich_message"
    assert rows["6"]["attachment_state"] == "unsupported"
    assert rows["7"]["content_state"] == rows["8"]["content_state"] == "unsupported_type"
    assert all(row["source"] == "telegram_export" and row["server_id"] == row["id"] for row in rows.values())
    assert rows["5"]["attachments"][0]["state"] == "attachment_path_invalid"
    assert result["coverage"] == "telegram_desktop_json_export" and result["shards_checked"] == 0
    assert result["snapshot_consistency"] == "immutable_export" and result["undated_unsupported_count"] == 1
    assert "SECRET" not in json.dumps([status, listing, result])
    result["items"][0]["text"] = "mutated"
    result["items"][0]["attachments"].append({"fake": True})
    assert export.get_messages(GROUP)["items"][0]["text"] is None
    assert len(export.get_messages(GROUP)["items"][0]["attachments"]) == 1


def test_bounded_pagination_matches_independent_order(export):
    result = export.search_messages("river", conversation_ids=[GROUP, PERSON], scan_limit=1)
    assert result["items"] == [] and result["scanned"] == 1 and result["unsupported_types"] == 1
    seen = []
    scans = []
    while True:
        seen.extend((row["conversation_id"], row["id"]) for row in result["items"])
        scans.append(result["scanned"])
        if result["next_cursor"] is None:
            break
        result = export.search_messages("river", conversation_ids=[PERSON, GROUP], scan_limit=1, limit=1, cursor=result["next_cursor"])
    assert seen == [(GROUP, "5"), (GROUP, "3"), (PERSON, "3")]
    assert sum(scans) == 10 and all(value == 1 for value in scans)
    assert result["incomplete"] and not result["truncated"] and result["decode_failures"] == 0
    assert not export.search_messages("river", PERSON)["incomplete"]
    assert export.search_messages("STRASSE", GROUP)["items"][0]["id"] == "1"
    assert export.search_messages("% _", GROUP)["items"][0]["id"] == "1"
    for secret in ("SECRET_RICH_BODY", "SECRET_POLL", "SECRET_ENTITY_METADATA", "NEVER_OPEN_PRIVATE_FILE"):
        assert export.search_messages(secret, GROUP)["items"] == []
    bounded = export.get_messages(GROUP, start_time=100, end_time=101)
    assert [row["id"] for row in bounded["items"]] == ["2", "1"]
    assert bounded["undated_unsupported_count"] == 1
    items = []
    cursor = None
    while True:
        page = export.get_messages(GROUP, limit=1, cursor=cursor)
        items.extend(row["id"] for row in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert items == ["8", "7", "6", "5", "4", "3", "2", "1"]


def test_context_exact_identity_and_zero_sides(export):
    context = export.get_message_context(GROUP, "3", before=1, after=2)
    assert [row["id"] for row in context["items"]] == ["2", "3", "4", "5"]
    assert context["anchor_index"] == 1 and context["has_more_before"] and context["has_more_after"]
    assert "next_cursor" not in context
    zero = export.get_message_context(GROUP, "3", before=0, after=0)
    assert len(zero["items"]) == 1 and zero["anchor_index"] == 0
    assert zero["has_more_before"] and zero["has_more_after"]
    assert export.get_message_context(PERSON, "3")["items"][-1]["text"] == "river other"
    for number, flags in (("1", (False, True)), ("8", (True, False))):
        result = export.get_message_context(GROUP, number, before=0, after=0)
        assert (result["has_more_before"], result["has_more_after"]) == flags
    with pytest.raises(SourceError, match="message_undated"):
        export.get_message_context(GROUP, "9")
    with pytest.raises(SourceError, match="message_not_found"):
        export.get_message_context(PERSON, "9")


def test_cursor_scope_mode_and_actual_member(export, tmp_path):
    original = {"query": "river", "conversation_ids": [GROUP, PERSON], "start_time": 100, "end_time": 104, "scan_limit": 1}
    cursor = export.search_messages(**original)["next_cursor"]
    assert cursor and "river" not in cursor
    for change in ({"query": "River"}, {"conversation_ids": [GROUP]}, {"start_time": 101}, {"end_time": 105}):
        with pytest.raises(SourceError, match="cursor_invalid"):
            export.search_messages(**{**original, **change, "cursor": cursor})
    history_cursor = export.get_messages(GROUP, limit=1)["next_cursor"]
    with pytest.raises(SourceError, match="cursor_invalid"):
        export.search_messages("river", GROUP, cursor=history_cursor)
    with pytest.raises(SourceError, match="cursor_invalid"):
        export.get_messages(GROUP, cursor=export.search_messages("river", GROUP, scan_limit=1)["next_cursor"])
    token = json.loads(base64.urlsafe_b64decode(history_cursor))
    for bad in (["wrong", *token[1:]], [*token[:2], PERSON, token[3]], [*token[:3], "9"], [*token[:3], "999"], [*token[:3], "08"], [*token[:3], True], {}):
        forged = base64.urlsafe_b64encode(json.dumps(bad).encode()).decode()
        with pytest.raises(SourceError, match="cursor_invalid"):
            export.get_messages(GROUP, cursor=forged)
    for invalid in ("", "%%%", "x" * 4097, base64.b64encode(b"[" * 1500).decode()):
        with pytest.raises(SourceError, match="cursor_invalid"):
            export.get_messages(GROUP, cursor=invalid)
    path = tmp_path / "export.json"
    identical = TelegramExport(path)
    assert identical.get_messages(GROUP, cursor=history_cursor)["items"]
    path.write_bytes(path.read_bytes() + b" ")
    assert export.get_messages(GROUP, cursor=history_cursor)["items"]
    with pytest.raises(SourceError, match="cursor_invalid"):
        TelegramExport(path).get_messages(GROUP, cursor=history_cursor)
    path.unlink()
    assert export.get_messages(GROUP)["items"]


def test_random_order_context_and_search_oracle(tmp_path):
    rng = random.Random(42)
    selected = []
    expected = []
    for index, kind in enumerate(("personal_chat", "private_group", "public_channel")):
        rows = [message(number, 100 + rng.randrange(4), "HIT" if number % 3 else "miss") for number in range(-20, 21)]
        rng.shuffle(rows)
        selected.append(chat(rows, type=kind))
        expected.extend((int(row["date_unixtime"]), row["id"], f"telegram:{kind}:42", row["text"]) for row in rows)
    reader = load(tmp_path, {"chats": {"list": selected}})
    ids = [f"telegram:{kind}:42" for kind in ("private_group", "personal_chat", "public_channel")]
    actual = []
    cursor = None
    while True:
        page = reader.search_messages("hit", conversation_ids=ids, start_time=101, end_time=103, limit=3, scan_limit=7, cursor=cursor)
        actual.extend((row["timestamp"], int(row["id"]), row["conversation_id"], row["text"]) for row in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert actual == [row for row in sorted(expected, reverse=True) if 101 <= row[0] < 103 and row[3] == "HIT"]
    for chat_id in ids:
        ordered = sorted(row for row in expected if row[2] == chat_id)
        for index, anchor in enumerate(ordered):
            result = reader.get_message_context(chat_id, str(anchor[1]), before=2, after=3)
            low, high = max(0, index - 2), min(len(ordered), index + 4)
            assert [(row["timestamp"], int(row["id"])) for row in result["items"]] == [(row[0], row[1]) for row in ordered[low:high]]
            assert result["anchor_index"] == index - low
            assert result["has_more_before"] == (low > 0) and result["has_more_after"] == (high < len(ordered))


def test_media_calls_open_no_files(export, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("An attachment call attempted a filesystem operation")
    monkeypatch.setattr(Path, "open", fail)
    monkeypatch.setattr(Path, "stat", fail)
    monkeypatch.setattr("builtins.open", fail)
    for number, declared, state in (("1", False, "none"), ("5", True, "available"), ("6", None, "unsupported")):
        listing = export.list_attachments(GROUP, number)
        assert listing["metadata_state"] == state
        assert listing["media_declared"] is declared
        with pytest.raises(SourceError, match="attachment_not_found"):
            export.read_attachment(GROUP, number, "a" * 32)
    assert export.list_attachments(GROUP, "5")["items"][0]["state"] == "attachment_path_invalid"
    with pytest.raises(SourceError, match="message_not_found"):
        export.list_attachments(PERSON, "8")


@pytest.mark.parametrize("marker", ["poll", "location_information", "place_name", "address", "contact_information", "game_title", "game_description", "game_link", "invoice_information", "todo_list", "giveaway_information", "giveaway_results"])
def test_writer_structured_content_is_explicitly_unsupported(tmp_path, marker):
    reader = load(tmp_path, chat([message(1, text="", **{marker: "SECRET_STRUCTURED_METADATA"})]))
    row = reader.get_messages(GROUP)["items"][0]
    assert row["text"] is None and row["unsupported_reason"] == "structured_content"
    result = reader.search_messages("SECRET", GROUP)
    assert result["items"] == [] and result["unsupported_types"] == 1


@pytest.mark.parametrize("kind,label", [("saved_messages", "Saved Messages"), ("replies", "Replies"), ("verification_codes", "Verification Codes"), (None, "telegram:unknown:42"), ("", "telegram:unknown:42")])
def test_writer_special_chats(tmp_path, kind, label):
    result = load(tmp_path, chat([], type=kind, name=None)).list_conversations()["items"][0]
    assert result["display_name"] == label


def test_bom_left_only_empty_service_and_int64(tmp_path):
    value = {"left_chats": {"list": [chat([
        message(-(2**63), -(2**63), "min"), message(2**63 - 1, 2**63 - 1, "max"),
        {"id": 0, "type": "service", "date_unixtime": "0", "text": ["literal ", {"type": "bold", "text": "river"}], "from": "ignored"},
        {"id": 1, "type": "unsupported", "date_unixtime": "1"},
    ], id=-(2**63))]}}
    path = tmp_path / "bom.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(value).encode())
    reader = TelegramExport(path)
    chat_id = f"telegram:private_group:{-(2**63)}"
    assert reader.search_messages("river", chat_id)["items"][0]["sender_name"] is None
    assert reader.get_message_context(chat_id, str(-(2**63)), before=0, after=0)["items"][0]["timestamp"] == -(2**63)
    assert len(reader.get_messages(chat_id)["items"]) == 4
    assert load(tmp_path, {"chats": {"list": []}}).status()["chat_count"] == 0


@pytest.mark.parametrize("value", [None, [], {}, {"contacts": []}, {**chat([]), "chats": {"list": []}}, {"chats": []}, {"left_chats": {"list": {}}}, {"chats": {"list": [None]}}, chat([], id=True), chat([], id="42"), chat([], id=42.0), chat([], id=2**63), chat([], type="future_type_SECRET"), chat([], type=[]), chat([], name="\ud800"), chat(None), chat([message(True)]), chat([message(2**63)]), chat([message(1), message(1)]), chat([{"id": 1, "type": "unsupported"}, message(1)]), chat([message(1, text=None)]), chat([message(1, text=[{"type": "bold", "text": {"text": "SECRET"}}])]), chat([message(1, text=[{"text": "SECRET"}])]), chat([message(1, text="\udfff")]), chat([message(1, from_id=9)]), chat([message(1, rich_message=[])]), chat([message(1, rich_message={})]), chat([message(1, type="future_SECRET")]), chat([message(1, date_unixtime=100)]), chat([message(1, date_unixtime="SECRET_DATE")]), chat([message(1, date_unixtime=str(2**63))]), chat([message(1, date_unixtime=True)]), chat([{"id": 1, "type": "message", "text_entities": []}]), {"chats": {"list": [chat([])]}, "left_chats": {"list": [chat([])]}}])
def test_schema_rejection_does_not_disclose_values(tmp_path, value):
    with pytest.raises(SourceError, match="export_invalid") as error:
        load(tmp_path, value)
    assert "SECRET" not in str(error.value) and "Fictional" not in str(error.value)


def test_encoding_json_limits_and_nonregular(tmp_path):
    path = tmp_path / "invalid.json"
    for data in (b'{"SECRET_TEXT":', b"\xffSECRET", b"[" * 2000, b'{"id":' + b"1" * 5000 + b"}"):
        path.write_bytes(data)
        with pytest.raises(SourceError, match="export_invalid") as error:
            TelegramExport(path)
        assert "SECRET" not in str(error.value)
    path.write_bytes(b" " * MAX_EXPORT_BYTES + b"x")
    with pytest.raises(SourceError, match="export_too_large"):
        TelegramExport(path)
    path.unlink()
    with pytest.raises(SourceError, match="export_unreadable"):
        TelegramExport(path)
    path.mkdir()
    with pytest.raises(SourceError, match="export_unreadable"):
        TelegramExport(path)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(SourceError, match="export_unreadable"):
        TelegramExport(fifo)


def test_actual_read_is_bounded_even_if_size_changes(tmp_path, monkeypatch):
    path = tmp_path / "changed.json"
    path.write_bytes(b" " * (MAX_EXPORT_BYTES + 1))
    original = Path.stat
    def stale_size(selected, *args, **kwargs):
        result = original(selected, *args, **kwargs)
        if selected == path:
            values = list(result)
            values[6] = 1
            return os.stat_result(values)
        return result
    monkeypatch.setattr(Path, "stat", stale_size)
    with pytest.raises(SourceError, match="export_too_large"):
        TelegramExport(path)


@pytest.mark.parametrize("arguments", [{}, {"conversation_ids": []}, {"conversation_ids": [GROUP, GROUP]}, {"conversation_ids": [GROUP] * 9}, {"conversation_ids": [4]}, {"conversation_id": GROUP, "conversation_ids": [PERSON]}, {"conversation_id": GROUP, "start_time": True}, {"conversation_id": GROUP, "end_time": 1.0}, {"conversation_id": GROUP, "start_time": 2**63}, {"conversation_id": GROUP, "start_time": 2, "end_time": 2}, {"conversation_id": GROUP, "limit": 0}, {"conversation_id": GROUP, "scan_limit": 0}, {"conversation_id": GROUP, "query": ""}])
def test_invalid_requests(export, arguments):
    with pytest.raises(SourceError, match="request_invalid"):
        export.search_messages(**{"query": "river", **arguments})


@pytest.mark.parametrize("message_id", ["01", "-0", "+1", "1.0", " 1", "١", "9" * 21, str(2**63), 1])
def test_invalid_exact_ids(export, message_id):
    with pytest.raises(SourceError, match="message_id_invalid"):
        export.get_message_context(GROUP, message_id)


def test_all_selected_chats_resolve_and_no_unselected_scan(export):
    with pytest.raises(SourceError, match="conversation_not_found"):
        export.search_messages("river", conversation_ids=[GROUP, "absent"], limit=1)
    result = export.search_messages("river", PERSON, scan_limit=1)
    assert result["scanned"] == 1 and result["items"][0]["conversation_id"] == PERSON
    assert result["undated_unsupported_count"] == 0


def test_cli_rejects_registration_and_safe_errors(tmp_path):
    for args in (["mcp", "--telegram-export", str(FIXTURE), "config"], ["mcp", "--telegram-export", str(FIXTURE), "add", "codex"], ["mcp", "--telegram-export", str(tmp_path / "missing")]):
        result = subprocess.run([sys.executable, "-m", "anymessage.server", *args], capture_output=True, text=True)
        assert result.returncode == 2 and result.stdout == ""
        assert "telegram" in result.stderr.lower()


def test_fictional_download_has_known_evidence():
    reader = TelegramExport(FIXTURE)
    hit = reader.search_messages("proposal", GROUP)["items"][0]
    context = reader.get_message_context(hit["conversation_id"], hit["id"], before=1, after=2)
    assert [row["id"] for row in context["items"]] == ["1", "2", "3", "4"]
    assert context["items"][-1]["text"].startswith("Approved:")
    assert reader.list_attachments(GROUP, "5")["items"][0]["state"] == "attachment_missing"
    spec = reader.list_attachments(GROUP, "6")["items"][0]
    result = reader.read_attachment(GROUP, "6", spec["id"])
    assert result.metadata["state"] == "ready" and "500 mAh" in result.text
    assert not result.metadata["content_verified"]
