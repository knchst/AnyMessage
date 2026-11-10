from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import ctypes.util
import shutil
import subprocess

import pytest
import zstandard

from anymessage.wechat import WeChat


configured_sqlcipher = os.environ.get("ANYMESSAGE_SQLCIPHER")
SQLCIPHER = shutil.which(configured_sqlcipher or "sqlcipher")
if not SQLCIPHER and not configured_sqlcipher:
    SQLCIPHER = next((str(path) for path in (
        Path("/opt/homebrew/opt/sqlcipher/bin/sqlcipher"),
        Path("/usr/local/opt/sqlcipher/bin/sqlcipher"),
    ) if path.is_file() and os.access(path, os.X_OK)), None)
USER = "wxid_fixture"
TABLE = "Msg_" + hashlib.md5(USER.encode()).hexdigest()
OTHER_USER = "zz_selected_fixture"
OTHER_TABLE = "Msg_" + hashlib.md5(OTHER_USER.encode()).hexdigest()


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("ANYMESSAGE_CONFIG_FILE", str(tmp_path / "config.json"))


@pytest.fixture(scope="session")
def sqlcipher():
    if not SQLCIPHER:
        pytest.fail("SQLCipher is required. Install it with brew install sqlcipher or set ANYMESSAGE_SQLCIPHER to its executable.")
    return SQLCIPHER


@pytest.fixture(scope="session")
def sqlcipher_library(sqlcipher):
    configured = os.environ.get("ANYMESSAGE_SQLCIPHER_LIBRARY")
    if configured:
        return configured
    prefix = Path(sqlcipher).resolve().parent.parent
    adjacent = next((str(path) for path in (
        prefix / "lib/libsqlcipher.dylib",
        prefix / "lib/libsqlcipher.so",
    ) if path.is_file()), None)
    library = adjacent or ctypes.util.find_library("sqlcipher")
    if not library:
        pytest.fail("SQLCipher's shared library is required for the WAL test. Set ANYMESSAGE_SQLCIPHER_LIBRARY to its path.")
    return library


def create_database(path: Path, sql: str, key: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    setup = ".bail on\n"
    if key:
        setup += f'PRAGMA key="x\'{key}\'";\nPRAGMA cipher_compatibility=4;\n'
    result = subprocess.run([SQLCIPHER, str(path)], input=setup + sql, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


@pytest.fixture(params=[False, True], ids=["plain", "encrypted"])
def source(tmp_path, monkeypatch, request, sqlcipher):
    root = tmp_path / "db_storage"
    keys = {}
    sqls = {
        "session/session.db": "CREATE TABLE SessionTable(username TEXT,sort_timestamp INTEGER,summary TEXT); INSERT INTO SessionTable VALUES('wxid_fixture',100,'你好');",
        "contact/contact.db": "CREATE TABLE contact(username TEXT,remark TEXT,nick_name TEXT,alias TEXT); INSERT INTO contact VALUES('wxid_fixture','連絡先の例','Fixture Contact','');",
    }
    compressed = zstandard.ZstdCompressor().compress("圧縮された你好".encode()).hex()
    schema = f'CREATE TABLE "{TABLE}"(local_id INTEGER PRIMARY KEY,server_id INTEGER,local_type INTEGER,sort_seq INTEGER,real_sender_id INTEGER,create_time INTEGER,message_content BLOB,WCDB_CT_message_content INTEGER,compress_content BLOB); CREATE TABLE Name2Id(user_name TEXT); INSERT INTO Name2Id VALUES(\'sender_fixture\');'
    sqls["message/message_0.db"] = schema + f'INSERT INTO "{TABLE}" VALUES(1,101,1,1,1,100,CAST(\'你好 世界\' AS BLOB),0,NULL); INSERT INTO "{TABLE}" VALUES(2,102,1,2,1,100,X\'{compressed}\',4,NULL); INSERT INTO "{TABLE}" VALUES(3,103,3,3,1,101,X\'0102\',0,NULL);'
    sqls["message/message_1.db"] = schema + f'INSERT INTO "{TABLE}" VALUES(1,201,1,1,1,100,CAST(\'你好 世界\' AS BLOB),0,NULL); INSERT INTO "{TABLE}" VALUES(2,202,1,4,1,102,CAST(\'literal % _ and apostrophe \'\'\' AS BLOB),0,NULL);'
    sqls["message/message_0.db"] += f'CREATE TABLE "{OTHER_TABLE}"(local_id INTEGER PRIMARY KEY,server_id INTEGER,local_type INTEGER,sort_seq INTEGER,real_sender_id INTEGER,create_time INTEGER,message_content BLOB); INSERT INTO "{OTHER_TABLE}" VALUES(1,301,1,1,1,100,CAST(\'Selected 你好\' AS BLOB)); INSERT INTO "{OTHER_TABLE}" VALUES(2,302,1,6,1,103,CAST(\'new selected record\' AS BLOB)); INSERT INTO "{OTHER_TABLE}" VALUES(3,303,1,7,1,104,X\'FF\');'
    sqls["message/message_1.db"] += f'CREATE TABLE "{OTHER_TABLE}"(local_id INTEGER PRIMARY KEY,server_id INTEGER,local_type INTEGER,sort_seq INTEGER,real_sender_id INTEGER,create_time INTEGER,message_content BLOB); INSERT INTO "{OTHER_TABLE}" VALUES(1,401,1,1,1,100,CAST(\'Other selected 你好\' AS BLOB));'
    for relative, sql in sqls.items():
        key = os.urandom(32).hex() if request.param else None
        path = root / relative
        create_database(path, sql, key)
        if key:
            keys[relative] = {"enc_key": key, "salt": path.read_bytes()[:16].hex()}
    key_path = tmp_path / "manifest.json"
    key_path.write_text(json.dumps(keys))
    key_path.chmod(0o600)
    monkeypatch.setenv("ANYMESSAGE_DATA_DIR", str(root))
    monkeypatch.setenv("ANYMESSAGE_SQLCIPHER", SQLCIPHER)
    monkeypatch.setenv("ANYMESSAGE_KEYS_FILE", str(key_path))
    monkeypatch.delenv("ANYMESSAGE_ACCOUNT", raising=False)
    return WeChat(), root, key_path, keys
