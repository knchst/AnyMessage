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
