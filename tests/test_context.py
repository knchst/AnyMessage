from __future__ import annotations

import json

import pytest

from anymessage.settings import Settings
from anymessage.wechat import SourceError, WeChat
from conftest import OTHER_USER, TABLE, USER, create_database
from test_attachments import add_file


@pytest.fixture
def context_source(source):
    reader, root, manifest, keys = source
    positions = [(100, 1, "message/message_0.db", 1), (100, 2, "message/message_0.db", 2),
                 (101, 3, "message/message_0.db", 3), (100, 1, "message/message_1.db", 1),
                 (102, 4, "message/message_1.db", 2)]
    extra = [(identity, 100, 1) for identity in (4, 5, 6)]
    extra += [(identity, 110 + identity, identity) for identity in range(10, 140)]
    sql = "".join(f'INSERT INTO "{TABLE}" VALUES({identity},{1000 + identity},1,{sequence},1,{timestamp},X\'78\',0,NULL);' for identity, timestamp, sequence in extra)
    create_database(root / "message/message_0.db", sql, keys.get("message/message_0.db", {}).get("enc_key"))
    positions.extend((timestamp, sequence, "message/message_0.db", identity) for identity, timestamp, sequence in extra)
    relative = "message/message_01.db"
    sql = f'CREATE TABLE "{TABLE}"(local_id INTEGER,server_id INTEGER,local_type INTEGER,sort_seq INTEGER,real_sender_id INTEGER,create_time INTEGER,message_content BLOB); CREATE TABLE Name2Id(user_name TEXT); INSERT INTO Name2Id VALUES(\'padded_shard_sender\');'
    sql += "".join(f'INSERT INTO "{TABLE}" VALUES({identity},{2000 + identity},1,1,1,100,X\'78\');' for identity in (1, 2, 3))
    key = "ab" * 32 if keys else None
    create_database(root / relative, sql, key)
    if key:
        keys[relative] = {"enc_key": key, "salt": (root / relative).read_bytes()[:16].hex()}
        manifest.write_text(json.dumps(keys))
    positions.extend((100, 1, relative, identity) for identity in (1, 2, 3))
    return reader, sorted(positions)


def test_context_matches_independent_full_tuple_oracle(context_source):
    reader, positions = context_source
    for index, before, after in [(0, 3, 2), (3, 2, 3), (6, 3, 2), (7, 0, 0),
                                 (8, 0, 4), (9, 4, 0), (75, 50, 50), (len(positions) - 1, 2, 3)]:
        anchor = positions[index]
        message_id = f"{anchor[2]}:{anchor[3]}"
        result = reader.get_message_context(USER, message_id, before=before, after=after)
        start, end = max(0, index - before), min(len(positions), index + after + 1)
        expected = [(USER, f"{position[2]}:{position[3]}") for position in positions[start:end]]
        assert [(item["conversation_id"], item["id"]) for item in result["items"]] == expected
        assert result["anchor_index"] == index - start
        assert sum(item["id"] == message_id for item in result["items"]) == 1
        assert result["items"][result["anchor_index"]]["id"] == message_id
        assert result["has_more_before"] == (start > 0)
        assert result["has_more_after"] == (end < len(positions))
        assert result["conversation_id"] == USER and result["message_id"] == message_id
        assert result["shards_checked"] == 3
        assert result["coverage"] == "local_message_shards"
        assert result["snapshot_consistency"] == "per_query"


def test_context_default_nearest_and_decode_positions(source):
    reader, _, _, _ = source
    result = reader.get_message_context(USER, "message/message_0.db:2")
    assert [item["id"] for item in result["items"]] == ["message/message_0.db:1", "message/message_1.db:1", "message/message_0.db:2", "message/message_0.db:3", "message/message_1.db:2"]
    assert result["items"][2]["text"] == "圧縮された你好"
    assert result["items"][3]["content_state"] == "unsupported_type"
    failed = reader.get_message_context(OTHER_USER, "message/message_0.db:2", before=1, after=1)
    assert failed["items"][-1]["content_state"] == "decode_failed"
    assert failed["items"][-1]["text"] is None
    assert failed["anchor_index"] == 1
    assert all(item["conversation_id"] == OTHER_USER for item in failed["items"])
    assert failed["items"][1]["text"] == "new selected record"


def test_context_first_last_only_and_maximum_id(source):
    reader, root, _, keys = source
    key = keys.get("message/message_0.db", {}).get("enc_key")
    create_database(root / "message/message_0.db", f'DELETE FROM "{TABLE}"; INSERT INTO "{TABLE}" VALUES({2**63 - 1},1,1,1,1,1,X\'78\',0,NULL);', key)
    create_database(root / "message/message_1.db", f'DELETE FROM "{TABLE}";', keys.get("message/message_1.db", {}).get("enc_key"))
    identity = f"message/message_0.db:{2**63 - 1}"
    for count in (0, 3, 50):
        result = reader.get_message_context(USER, identity, before=count, after=count)
        assert [item["id"] for item in result["items"]] == [identity]
        assert result["anchor_index"] == 0
        assert not result["has_more_before"] and not result["has_more_after"]


@pytest.mark.parametrize("arguments,code", [
    ({"before": True}, "request_invalid"), ({"after": False}, "request_invalid"),
    ({"before": -1}, "request_invalid"), ({"after": 51}, "request_invalid"),
    ({"before": 1.0}, "request_invalid"), ({"after": "3"}, "request_invalid"),
    ({"conversation_id": None}, "request_invalid"), ({"conversation_id": 1}, "request_invalid"),
    ({"message_id": None}, "message_id_invalid"), ({"message_id": 1}, "message_id_invalid"),
    ({"message_id": "message/message_0.db:0"}, "message_id_invalid"),
    ({"message_id": "message/message_0.db:01"}, "message_id_invalid"),
    ({"message_id": "message/message_0.db:+1"}, "message_id_invalid"),
    ({"message_id": "message/message_0.db:١"}, "message_id_invalid"),
    ({"message_id": "message/message_0.db:1٢"}, "message_id_invalid"),
    ({"message_id": f"message/message_0.db:{2**63}"}, "message_id_invalid"),
    ({"message_id": "../../message_0.db:1"}, "message_id_invalid"),
    ({"message_id": "message/message_" + "0" * 128 + ".db:1"}, "message_id_invalid"),
])
def test_context_invalid_request_before_account_access(tmp_path, monkeypatch, arguments, code):
    reader = WeChat(Settings(tmp_path / "absent", None, None, None))
    monkeypatch.setattr(reader, "selected_root", lambda: pytest.fail("invalid input accessed an account"))
    with pytest.raises(SourceError) as error:
        reader.get_message_context(**{"conversation_id": USER, "message_id": "message/message_0.db:1", **arguments})
    assert error.value.code == code


def test_context_missing_duplicate_and_wrong_conversation(source):
    reader, root, _, keys = source
    for identity in ("message/message_0.db:999", "message/message_99.db:1"):
        with pytest.raises(SourceError) as error:
            reader.get_message_context(USER, identity)
        assert error.value.code == "message_not_found"
    with pytest.raises(SourceError) as error:
        reader.get_message_context(OTHER_USER, "message/message_1.db:2")
    assert error.value.code == "message_not_found"
    with pytest.raises(SourceError) as error:
        reader.get_message_context("absent-chat", "message/message_0.db:1")
    assert error.value.code == "conversation_not_found"
    for conversation in (USER, OTHER_USER):
        result = reader.get_message_context(conversation, "message/message_0.db:1", before=0, after=0)
        assert result["items"][0]["conversation_id"] == conversation
        assert result["items"][0]["text"] == ("你好 世界" if conversation == USER else "Selected 你好")
    key = keys.get("message/message_0.db", {}).get("enc_key")
    create_database(root / "message/message_0.db", f'ALTER TABLE "{TABLE}" RENAME TO original; CREATE TABLE "{TABLE}" AS SELECT * FROM original; INSERT INTO "{TABLE}" SELECT * FROM original WHERE local_id=1;', key)
    with pytest.raises(SourceError) as error:
        reader.get_message_context(USER, "message/message_0.db:1")
    assert error.value.code == "message_not_found"
    create_database(root / "message/message_0.db", f'DROP TABLE "{TABLE}";', key)
    with pytest.raises(SourceError) as error:
        reader.get_message_context(USER, "message/message_0.db:1")
    assert error.value.code == "message_not_found"


def test_context_attachment_identity_and_target_shard_compatibility(source):
    reader, _, identity = add_file(source)
    context = reader.get_message_context(USER, identity, before=1, after=0)
    message = context["items"][context["anchor_index"]]
    descriptor = message["attachments"][0]
    assert descriptor["state"] == "not_checked"
    assert reader.list_attachments(USER, identity)["items"][0]["id"] == descriptor["id"]
    assert reader.read_attachment(USER, identity, descriptor["id"]).text == "attachment text\nsecond line"
    _, root, _, keys = source
    create_database(root / "message/message_0.db", "DROP TABLE Name2Id;", keys.get("message/message_0.db", {}).get("enc_key"))
    (root / "message/message_1.db").write_bytes(b"unreadable supported shard")
    assert reader.list_attachments(USER, identity)["items"][0]["id"] == descriptor["id"]
    assert reader.read_attachment(USER, identity, descriptor["id"]).text == "attachment text\nsecond line"
    with pytest.raises(SourceError) as error:
        reader.get_message_context(USER, identity)
    assert error.value.code == "schema_unsupported"


def test_context_unreadable_supported_shard_fails_closed(source):
    reader, root, manifest, keys = source
    if keys:
        del keys["message/message_1.db"]
        manifest.write_text(json.dumps(keys))
    else:
        (root / "message/message_1.db").write_bytes(b"not a database")
    with pytest.raises(SourceError) as error:
        reader.get_message_context(USER, "message/message_0.db:1")
    assert error.value.code == "key_missing"
    if keys:
        keys["message/message_1.db"] = {"enc_key": "00" * 32}
        manifest.write_text(json.dumps(keys))
        with pytest.raises(SourceError) as error:
            reader.get_message_context(USER, "message/message_0.db:1")
        assert error.value.code == "key_invalid"
