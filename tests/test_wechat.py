from __future__ import annotations

import hashlib
import json
import subprocess
import ctypes

import pytest

from anymessage.wechat import SourceError, WeChat
from conftest import SQLCIPHER, TABLE, USER


def test_ready_and_conversations(source):
    reader, root, _, keys = source
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*.db")}
    status = reader.status()
    assert status["state"] == "ready"
    assert status["database_count"] == 4
    assert all(item["encrypted"] == bool(keys) for item in status["databases"])
    page = reader.list_conversations(limit=1)
    assert page["items"][0]["display_name"] == "連絡先の例"
    assert page["items"][0]["summary"] == "你好"
    assert page["next_offset"] is None
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*.db")}


def test_messages_across_shards_and_compression(source):
    reader, _, _, _ = source
    page = reader.get_messages(USER, limit=200)
    assert len(page["items"]) == 5
    assert page["shards_checked"] == 2
    assert page["items"][0]["timestamp"] == 102
    assert any(item["text"] == "圧縮された你好" for item in page["items"])
    assert all(item["sender_id"] == "sender_fixture" for item in page["items"])
    assert next(item for item in page["items"] if item["type"] == 3)["content_state"] == "unsupported_type"


def test_page_ties_do_not_duplicate_or_skip(source):
    reader, _, _, _ = source
    cursor = None
    messages = []
    while True:
        page = reader.get_messages(USER, limit=1, cursor=cursor)
        messages += page["items"]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(messages) == 5
    assert len({item["id"] for item in messages}) == 5


def test_search_literal_and_continuation(source):
    reader, _, _, _ = source
    literal = reader.search_messages("% _ and apostrophe '", USER)
    assert len(literal["items"]) == 1
    results = []
    cursor = None
    while True:
        page = reader.search_messages("你好", USER, limit=1, scan_limit=5, cursor=cursor)
        results += page["items"]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(results) == 3
    assert len({item["id"] for item in results}) == 3
    bounded = reader.search_messages("missing", USER, scan_limit=1)
    assert bounded["scanned"] == 1
    assert bounded["truncated"]


def test_missing_or_wrong_key_does_not_return_partial_history(source):
    reader, _, key_path, keys = source
    if not keys:
        pytest.skip("encrypted fixture only")
    del keys["message/message_1.db"]
    key_path.write_text(json.dumps(keys))
    with pytest.raises(SourceError, match="per-database") as error:
        reader.get_messages(USER)
    assert error.value.code == "key_missing"
    keys["message/message_1.db"] = {"enc_key": "00" * 32}
    key_path.write_text(json.dumps(keys))
    with pytest.raises(SourceError) as error:
        reader.get_messages(USER)
    assert error.value.code == "key_invalid"
    assert reader.status()["state"] == "key_invalid"


def test_key_permissions_and_cursor_validation(source):
    reader, _, key_path, _ = source
    key_path.chmod(0o644)
    assert reader.status()["state"] == "key_file_permissions"
    key_path.chmod(0o600)
    with pytest.raises(SourceError) as error:
        reader.get_messages(USER, cursor="bad cursor")
    assert error.value.code == "cursor_invalid"


def test_schema_mismatch_is_explicit(source):
    reader, root, _, keys = source
    path = root / "session/session.db"
    setup = f'PRAGMA key="x\'{keys["session/session.db"]["enc_key"]}\'";\n' if keys else ""
    result = subprocess.run([SQLCIPHER, str(path)], input=setup + "DROP TABLE SessionTable; CREATE TABLE SessionTable(other TEXT);", text=True, capture_output=True)
    assert result.returncode == 0
    assert reader.status()["state"] == "schema_unsupported"
    with pytest.raises(SourceError) as error:
        reader.list_conversations()
    assert error.value.code == "schema_unsupported"


def test_ready_requires_sender_lookup_schema(source):
    reader, root, _, keys = source
    path = root / "message/message_0.db"
    setup = f'PRAGMA key="x\'{keys["message/message_0.db"]["enc_key"]}\'";\n' if keys else ""
    result = subprocess.run([SQLCIPHER, str(path)], input=setup + "DROP TABLE Name2Id;", text=True, capture_output=True)
    assert result.returncode == 0
    assert reader.status()["state"] == "schema_unsupported"
    with pytest.raises(SourceError) as error:
        reader.get_messages(USER)
    assert error.value.code == "schema_unsupported"


def test_readonly_connection_sees_committed_wal(source, sqlcipher_library):
    reader, root, _, keys = source
    path = root / "message/message_0.db"
    lib = ctypes.CDLL(sqlcipher_library)
    lib.sqlite3_open.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
    lib.sqlite3_exec.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    lib.sqlite3_close.argtypes = [ctypes.c_void_p]
    writer = ctypes.c_void_p()
    assert lib.sqlite3_open(str(path).encode(), ctypes.byref(writer)) == 0
    setup = f'PRAGMA key="x\'{keys["message/message_0.db"]["enc_key"]}\'";\nPRAGMA cipher_compatibility=4;\n' if keys else ""
    command = setup + f'PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0; PRAGMA wal_checkpoint(TRUNCATE); INSERT INTO "{TABLE}" VALUES(9,109,1,9,1,109,CAST(\'WAL 中の新着\' AS BLOB),0,NULL);'
    try:
        assert lib.sqlite3_exec(writer, command.encode(), None, None, None) == 0
        assert any(item["text"] == "WAL 中の新着" for item in reader.get_messages(USER)["items"])
        with pytest.raises(SourceError):
            reader.query(root, "message/message_0.db", f'DELETE FROM "{TABLE}"', keys)
    finally:
        assert lib.sqlite3_close(writer) == 0


def test_date_bounds_and_empty_local_range(source):
    reader, _, _, _ = source
    page = reader.get_messages(USER, 1, start_time=100, end_time=101)
    messages = page["items"]
    while page["next_cursor"]:
        page = reader.get_messages(USER, 1, page["next_cursor"], start_time=100, end_time=101)
        messages.extend(page["items"])
    assert len(messages) == 3
    assert {item["timestamp"] for item in messages} == {100}
    assert page["conversation_ids"] == [USER]
    assert (page["start_time"], page["end_time"]) == (100, 101)
    assert reader.get_messages(USER, start_time=101)["items"][0]["timestamp"] == 102
    assert len(reader.get_messages(USER, end_time=101)["items"]) == 3
    empty = reader.search_messages("你好", USER, start_time=0, end_time=100)
    assert empty["items"] == [] and empty["scanned"] == 0
    assert empty["next_cursor"] is None and not empty["incomplete"]
    assert reader.get_messages(USER, start_time=-(2**63), end_time=2**63 - 1)["items"]


def test_selected_search_total_order_identity_and_shared_budget(source):
    from conftest import OTHER_USER

    reader, _, _, _ = source
    arguments = {"conversation_ids": [OTHER_USER, USER], "start_time": 100, "end_time": 104}
    full = reader.search_messages("你好", **arguments)
    assert full["scanned"] == 8
    assert full["unsupported_types"] == 1 and full["decode_failures"] == 0
    assert full["conversation_ids"] == sorted([USER, OTHER_USER])
    assert full["coverage"] == "decoded_local_text_in_selected_conversations"
    assert full["shards_checked"] == 2 and full["snapshot_consistency"] == "per_query"
    pairs = [(item["conversation_id"], item["id"]) for item in full["items"]]
    assert pairs == [
        (USER, "message/message_0.db:2"),
        (OTHER_USER, "message/message_1.db:1"), (USER, "message/message_1.db:1"),
        (OTHER_USER, "message/message_0.db:1"), (USER, "message/message_0.db:1"),
    ]
    assert len(set(pairs)) == 5 and len({identity for _, identity in pairs}) == 3
    first = reader.search_messages("你好", scan_limit=1, **arguments)
    assert first["items"] == [] and first["scanned"] == 1 and first["truncated"]
    cursor = first["next_cursor"]
    hits = []
    scanned = first["scanned"]
    while cursor:
        page = reader.search_messages("你好", limit=1, scan_limit=1, cursor=cursor,
                                      **{**arguments, "conversation_ids": [USER, OTHER_USER]})
        hits.extend(page["items"])
        scanned += page["scanned"]
        cursor = page["next_cursor"]
    assert [(item["conversation_id"], item["id"]) for item in hits] == pairs
    assert scanned == 8
    assert reader.search_messages("missing", conversation_ids=[USER, OTHER_USER])["decode_failures"] == 1


def test_scoped_cursor_binding_and_legacy_interchange(source, tmp_path):
    import base64
    from anymessage.settings import Settings
    from conftest import OTHER_USER

    reader, _, _, _ = source
    bounded = reader.get_messages(USER, 1, start_time=100, end_time=103)
    token = bounded["next_cursor"]
    assert json.loads(base64.urlsafe_b64decode(token))["v"] == 2
    assert reader.search_messages("你好", USER, cursor=token, start_time=100, end_time=103)["items"]
    for changed in [{"start_time": 99, "end_time": 103}, {"start_time": 100, "end_time": 104}, {},
                    {"conversation_ids": [USER, OTHER_USER], "start_time": 100, "end_time": 103}]:
        selector = {} if "conversation_ids" in changed else {"conversation_id": USER}
        with pytest.raises(SourceError) as error:
            reader.search_messages("你好", cursor=token, **selector, **changed)
        assert error.value.code == "cursor_invalid"
    other_root = tmp_path / "another" / "db_storage"
    (other_root / "message").mkdir(parents=True)
    (other_root / "message/message_0.db").touch()
    (other_root / "message/message_1.db").touch()
    other = WeChat(Settings(other_root, None, None, reader.executable))
    with pytest.raises(SourceError) as error:
        other.get_messages(USER, cursor=token, start_time=100, end_time=103)
    assert error.value.code == "cursor_invalid"
    legacy = reader.get_messages(USER, 1)["next_cursor"]
    assert set(json.loads(base64.urlsafe_b64decode(legacy))) == {"conversation", "position"}
    array = reader.search_messages("你好", conversation_ids=[USER], scan_limit=1)
    assert set(json.loads(base64.urlsafe_b64decode(array["next_cursor"]))) == {"conversation", "position"}
    assert reader.get_messages(USER, cursor=array["next_cursor"])["items"]
    assert reader.search_messages("你好", conversation_ids=[USER], cursor=legacy)["items"]
    old_spelling = base64.urlsafe_b64encode(json.dumps({"conversation": USER, "position": [102, 4, "message/message_1.db", 2]}).encode()).decode()
    assert reader.get_messages(USER, cursor=old_spelling)["items"] == reader.get_messages(USER, cursor=legacy)["items"]


@pytest.mark.parametrize("arguments", [
    {}, {"conversation_id": USER, "conversation_ids": [USER]}, {"conversation_ids": []},
    {"conversation_ids": [USER, USER]}, {"conversation_ids": [str(i) for i in range(9)]},
    {"conversation_ids": USER}, {"conversation_ids": [""]}, {"conversation_id": "x" * 513},
    *({"conversation_id": USER, "start_time": value} for value in (True, 1.0, "100", -(2**63) - 1, 2**63)),
    {"conversation_id": USER, "start_time": 100, "end_time": 100},
    {"conversation_id": USER, "start_time": 101, "end_time": 100},
])
def test_scope_inputs_fail_before_database_access(monkeypatch, arguments):
    reader = WeChat()
    def forbidden_root():
        raise AssertionError("Invalid scope must fail before account access")
    monkeypatch.setattr(reader, "selected_root", forbidden_root)
    with pytest.raises(SourceError) as error:
        reader.search_messages("needle", **arguments)
    assert error.value.code == "request_invalid"


def test_cursor_rejects_malformed_positions(source):
    import base64

    reader, _, _, _ = source
    token = reader.get_messages(USER, 1, start_time=0)["next_cursor"]
    payload = json.loads(base64.urlsafe_b64decode(token))
    invalid = [{**payload, "v": True}, {**payload, "v": 3}, {**payload, "p": [100, 1, "../other.db", 1, 0]},
               {**payload, "p": [100, 1, "message/message_0.db", True, 0]},
               {**payload, "p": [100, 1, "message/message_0.db", 1, 1]},
               {**payload, "p": [100, 1, "message/message_0.db", 1, 0, 0]}, [], None]
    for value in invalid:
        malformed = base64.urlsafe_b64encode(json.dumps(value).encode()).decode()
        with pytest.raises(SourceError) as error:
            reader.get_messages(USER, cursor=malformed, start_time=0)
        assert error.value.code == "cursor_invalid"


def test_long_unicode_cursor_and_max_selection(source):
    from conftest import create_database

    reader, root, _, keys = source
    ids = ["𠮷" * 511 + str(index) for index in range(8)]
    sql = ""
    for identity in ids:
        table = "Msg_" + hashlib.md5(identity.encode()).hexdigest()
        sql += f'CREATE TABLE "{table}" AS SELECT * FROM "{TABLE}";'
    create_database(root / "message/message_0.db", sql, keys.get("message/message_0.db", {}).get("enc_key"))
    for arguments in [{"conversation_ids": ids}, {"conversation_ids": ids[:1]},
                      {"conversation_ids": ids[:1], "start_time": 0}]:
        first = reader.search_messages("你好", scan_limit=1, **arguments)
        assert len(first["next_cursor"]) <= 4096
        resumed = reader.search_messages("你好", cursor=first["next_cursor"], **arguments)
        assert resumed["items"]
        if len(arguments["conversation_ids"]) == 1 and "start_time" not in arguments:
            assert reader.get_messages(ids[0], cursor=first["next_cursor"])["items"]


def test_selected_missing_chat_and_unreadable_shard_fail_closed(source):
    from conftest import OTHER_USER

    reader, root, _, _ = source
    with pytest.raises(SourceError, match="missing-chat") as error:
        reader.search_messages("你好", conversation_ids=[USER, "missing-chat"])
    assert error.value.code == "conversation_not_found"
    (root / "message/message_2.db").write_bytes(b"unreadable supported shard")
    with pytest.raises(SourceError) as error:
        reader.search_messages("你好", conversation_ids=[USER, OTHER_USER], scan_limit=1)
    assert error.value.code in {"key_missing", "key_invalid", "database_read_failed"}


def test_selected_windows_bound_data_reads_and_inspected_decoding(source, monkeypatch):
    from conftest import OTHER_USER

    reader, _, _, _ = source
    reads = []
    query = reader.query
    def observe_query(root, relative, sql, keys):
        rows = query(root, relative, sql, keys)
        reads.append((sql, len(rows)))
        return rows
    monkeypatch.setattr(reader, "query", observe_query)
    result = reader.search_messages("missing", conversation_ids=[USER, OTHER_USER], scan_limit=1, end_time=104)
    assert result["scanned"] == 1 and result["decode_failures"] == 0
    data = [(sql, count) for sql, count in reads if "AS body_hex" in sql]
    assert len(data) == 2
    assert all(" UNION ALL " in sql and count <= 2 for sql, count in data)
    assert sum("sqlite_master" in sql for sql, _ in reads) == 2


def test_local_day_conversion_and_zero_bound(source):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    reader, _, _, _ = source
    zone = ZoneInfo("Asia/Tokyo")
    start = int(datetime(1970, 1, 1, tzinfo=zone).timestamp())
    end = int(datetime(1970, 1, 2, tzinfo=zone).timestamp())
    page = reader.get_messages(USER, start_time=start, end_time=end)
    assert len(page["items"]) == 5
    assert (page["start_time"], page["end_time"]) == (-32400, 54000)
    zero = reader.get_messages(USER, limit=1, start_time=0)
    with pytest.raises(SourceError) as error:
        reader.get_messages(USER, cursor=zero["next_cursor"])
    assert error.value.code == "cursor_invalid"


def test_selected_search_across_multiple_windows(source, monkeypatch):
    from conftest import OTHER_TABLE, OTHER_USER, create_database

    reader, root, _, keys = source
    sql = ""
    for identity in range(10, 230):
        sql += f'INSERT INTO "{OTHER_TABLE}" VALUES({identity},{1000 + identity},1,{identity},1,{identity + 200},X\'6E6F206D61746368\');'
    create_database(root / "message/message_0.db", sql, keys.get("message/message_0.db", {}).get("enc_key"))
    schemas = []
    schema = reader.schema
    def observe_schema(root, relative, keys):
        schemas.append(relative)
        return schema(root, relative, keys)
    monkeypatch.setattr(reader, "schema", observe_schema)
    first = reader.search_messages("你好", conversation_ids=[USER, OTHER_USER], scan_limit=221)
    assert first["scanned"] == 221 and first["items"] == []
    assert first["decode_failures"] == 1 and first["next_cursor"]
    assert schemas == ["message/message_0.db", "message/message_1.db"]
    rest = reader.search_messages("你好", conversation_ids=[OTHER_USER, USER], cursor=first["next_cursor"])
    assert rest["scanned"] == 8 and len(rest["items"]) == 5
    assert not rest["incomplete"]


def test_selected_empty_range_keeps_canonical_rank(source):
    import base64
    from conftest import OTHER_USER

    reader, _, _, _ = source
    arguments = {"conversation_ids": [OTHER_USER, USER], "start_time": 101, "end_time": 103}
    first = reader.search_messages("missing", scan_limit=1, **arguments)
    assert first["scanned"] == 1 and first["conversation_ids"] == [USER, OTHER_USER]
    assert json.loads(base64.urlsafe_b64decode(first["next_cursor"]))["p"][4] == 0
    last = reader.search_messages("missing", cursor=first["next_cursor"], **arguments)
    assert last["scanned"] == 1 and last["unsupported_types"] == 1
    assert last["next_cursor"] is None


def test_incompatible_selected_table_fails_call(source):
    from conftest import OTHER_TABLE, OTHER_USER, create_database

    reader, root, _, keys = source
    create_database(root / "message/message_0.db", f'DROP TABLE "{OTHER_TABLE}"; CREATE TABLE "{OTHER_TABLE}"(local_id INTEGER);', keys.get("message/message_0.db", {}).get("enc_key"))
    with pytest.raises(SourceError) as error:
        reader.search_messages("你好", conversation_ids=[USER, OTHER_USER], scan_limit=1)
    assert error.value.code == "schema_unsupported"
