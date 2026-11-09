"""Read-only WeChat 4.x database access using the SQLCipher executable."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import plistlib
import re
import stat
import subprocess
from typing import Any

import zstandard

from anymessage.settings import Settings


class SourceError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def sql_text(value: str) -> str:
    return "CAST(X'" + value.encode("utf-8").hex() + "' AS TEXT)"


class WeChat:
    source_id = "wechat"

    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or Settings.load()
        self.data_dir = settings.data_dir
        self.account_id = settings.account
        self.key_file = settings.keys_file
        self.executable = settings.sqlcipher

    def accounts(self) -> list[tuple[str, Path]]:
        if self.data_dir.name == "db_storage" and self.data_dir.is_dir():
            roots = [self.data_dir]
        elif (self.data_dir / "db_storage").is_dir():
            roots = [self.data_dir / "db_storage"]
        elif self.data_dir.is_dir():
            roots = sorted(p / "db_storage" for p in self.data_dir.iterdir() if (p / "db_storage").is_dir())
        else:
            roots = []
        return [(hashlib.sha256(str(p.resolve()).encode()).hexdigest()[:16], p.resolve()) for p in roots]

    def selected_root(self) -> Path:
        accounts = self.accounts()
        if self.account_id:
            accounts = [item for item in accounts if item[0] == self.account_id]
        if not accounts:
            raise SourceError("data_missing", "No matching WeChat account database directory was found.")
        if len(accounts) != 1:
            raise SourceError("account_required", "Set ANYMESSAGE_ACCOUNT to one account id returned by status.")
        return accounts[0][1]

    def keys(self) -> dict[str, dict[str, str]]:
        if self.key_file is None:
            return {}
        if not self.key_file.is_file():
            raise SourceError("key_missing", "ANYMESSAGE_KEYS_FILE does not name an existing private file.")
        if stat.S_IMODE(self.key_file.stat().st_mode) & 0o077:
            raise SourceError("key_file_permissions", "The key manifest must be private. Run chmod 600 on it.")
        try:
            data = json.loads(self.key_file.read_text())
        except (ValueError, OSError) as error:
            raise SourceError("key_manifest_invalid", "The key manifest must contain readable JSON.") from error
        if isinstance(data, dict) and "keys" in data:
            data = data["keys"]
        if not isinstance(data, dict):
            raise SourceError("key_manifest_invalid", "The key manifest must be an object keyed by relative database path.")
        parsed = {}
        for name, entry in data.items():
            if not isinstance(name, str) or not isinstance(entry, dict):
                raise SourceError("key_manifest_invalid", "Each key entry must contain enc_key and an optional salt.")
            relative = Path(name.replace("\\", "/"))
            if relative.is_absolute() or ".." in relative.parts:
                raise SourceError("key_manifest_invalid", "Database key paths must remain relative to db_storage.")
            normalized = relative.as_posix().removeprefix("db_storage/")
            key = entry.get("enc_key")
            salt = entry.get("salt")
            if not isinstance(key, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", key):
                raise SourceError("key_manifest_invalid", "Each enc_key must contain exactly 64 hexadecimal characters.")
            if salt is not None and (not isinstance(salt, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", salt)):
                raise SourceError("key_manifest_invalid", "Each optional salt must contain exactly 32 hexadecimal characters.")
            if normalized in parsed:
                raise SourceError("key_manifest_invalid", "The manifest contains duplicate normalized database paths.")
            parsed[normalized] = {"enc_key": key.lower(), **({"salt": salt.lower()} if salt else {})}
        return parsed

    def query(self, root: Path, relative: str, sql: str, keys: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
        if not self.executable:
            raise SourceError("sqlcipher_missing", "Install SQLCipher with brew install sqlcipher or set ANYMESSAGE_SQLCIPHER.")
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise SourceError("database_missing", f"Required database is missing: {relative}")
        with path.open("rb") as handle:
            header = handle.read(16)
        encrypted = header != b"SQLite format 3\x00"
        preamble = ".bail on\n.timeout 2000\n"
        if encrypted:
            entry = keys.get(relative)
            if not entry:
                raise SourceError("key_missing", f"A per-database key is required for {relative}.")
            if entry.get("salt") and entry["salt"] != header.hex():
                raise SourceError("key_invalid", f"The recorded salt does not match {relative}.")
            preamble += f"PRAGMA key = \"x'{entry['enc_key']}'\";\nPRAGMA cipher_compatibility=4;\n"
        preamble += "PRAGMA query_only=ON;\n"
        result = subprocess.run(
            [self.executable, "-readonly", "-json", str(path)],
            input=preamble + sql + ";\n", text=True, capture_output=True, timeout=20,
        )
        if result.returncode:
            code = "key_invalid" if encrypted and "not a database" in result.stderr.lower() else "database_read_failed"
            raise SourceError(code, f"SQLCipher could not read {relative}. Verify its key, schema, and WAL availability.")
        output = result.stdout.strip()
        if output.startswith('[{"ok":'):
            output = output.partition("\n")[2].strip()
        if not output:
            return []
        try:
            return json.loads(output)
        except ValueError as error:
            raise SourceError("database_read_failed", f"SQLCipher returned an unexpected result for {relative}.") from error

    def schema(self, root: Path, relative: str, keys: dict[str, dict[str, str]]) -> dict[str, set[str]]:
        rows = self.query(root, relative, "SELECT m.name AS table_name, p.name AS column_name FROM sqlite_master m JOIN pragma_table_info(m.name) p WHERE m.type='table'", keys)
        schema: dict[str, set[str]] = {}
        for row in rows:
            schema.setdefault(row["table_name"], set()).add(row["column_name"])
        return schema

    def require_table(self, schema: dict[str, set[str]], table: str, required: set[str], relative: str) -> set[str]:
        columns = schema.get(table, set())
        if not required.issubset(columns):
            raise SourceError("schema_unsupported", f"Unsupported WeChat schema in {relative}, table {table}.")
        return columns

    def status(self) -> dict[str, Any]:
        app = Path("/Applications/WeChat.app/Contents/Info.plist")
        version = None
        if app.is_file():
            with app.open("rb") as handle:
                version = plistlib.load(handle).get("CFBundleShortVersionString")
        result: dict[str, Any] = {
            "source": self.source_id, "installed": app.is_file(), "version": version,
            "accounts": [],
            "sqlcipher_available": bool(self.executable), "state": "locked", "databases": [],
            "schema_verified": False,
        }
        try:
            accounts = self.accounts()
            result["accounts"] = [{"id": account, "selected": account == self.account_id or len(accounts) == 1} for account, _ in accounts]
            root = self.selected_root()
            keys = self.keys()
            databases = sorted(p for p in root.rglob("*.db") if p.is_file())
            relevant = ["session/session.db", "contact/contact.db"] + self.shards(root)
            for path in databases:
                with path.open("rb") as handle:
                    encrypted = handle.read(16) != b"SQLite format 3\x00"
                result["databases"].append({"path": path.relative_to(root).as_posix(), "encrypted": encrypted})
            result["database_count"] = len(databases)
            for relative in relevant:
                schema = self.schema(root, relative, keys)
                if relative == "session/session.db":
                    self.require_table(schema, "SessionTable", {"username", "sort_timestamp", "summary"}, relative)
                elif relative == "contact/contact.db":
                    self.require_table(schema, "contact", {"username"}, relative)
                else:
                    for table in schema:
                        if re.fullmatch(r"Msg_[0-9a-fA-F]{32}", table):
                            self.require_table(schema, table, self.message_columns, relative)
                            self.require_table(schema, "Name2Id", {"user_name"}, relative)
            if not self.shards(root):
                raise SourceError("database_missing", "No message/message_N.db shards were found.")
            result["state"] = "ready"
            result["schema_verified"] = True
        except (SourceError, OSError, subprocess.TimeoutExpired) as error:
            result["state"] = error.code if isinstance(error, SourceError) else "database_read_failed"
            result["message"] = str(error) if isinstance(error, SourceError) else "A local database could not be inspected."
        return result

    @staticmethod
    def shards(root: Path) -> list[str]:
        return sorted(p.relative_to(root).as_posix() for p in (root / "message").glob("message_*.db") if re.fullmatch(r"message_\d+\.db", p.name))

    def list_conversations(self, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        root, keys = self.selected_root(), self.keys()
        relative = "session/session.db"
        schema = self.schema(root, relative, keys)
        self.require_table(schema, "SessionTable", {"username", "sort_timestamp", "summary"}, relative)
        rows = self.query(root, relative, f"SELECT username, sort_timestamp, summary FROM SessionTable ORDER BY sort_timestamp DESC, username DESC LIMIT {limit + 1} OFFSET {offset}", keys)
        contacts_schema = self.schema(root, "contact/contact.db", keys)
        columns = self.require_table(contacts_schema, "contact", {"username"}, "contact/contact.db")
        names = [c for c in ("remark", "nick_name", "alias") if c in columns]
        names_predicate = ",".join(sql_text(row["username"]) for row in rows) or "NULL"
        contacts = self.query(root, "contact/contact.db", "SELECT username" + "".join("," + name for name in names) + f" FROM contact WHERE username IN ({names_predicate})", keys)
        display = {row["username"]: next((row.get(name) for name in names if row.get(name)), row["username"]) for row in contacts}
        items = [{"source": self.source_id, "id": row["username"], "display_name": display.get(row["username"], row["username"]), "updated_at": row["sort_timestamp"], "summary": row["summary"], "is_group": row["username"].endswith("@chatroom")} for row in rows[:limit]]
        return {"items": items, "next_offset": offset + limit if len(rows) > limit else None, "coverage": "local_sessions"}

    message_columns = {"local_id", "server_id", "local_type", "sort_seq", "real_sender_id", "create_time", "message_content"}

    def get_messages(self, conversation_id: str, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
        root, keys = self.selected_root(), self.keys()
        table = "Msg_" + hashlib.md5(conversation_id.encode()).hexdigest()
        bound = None
        if cursor:
            try:
                payload = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                if payload["conversation"] != conversation_id or len(payload["position"]) != 4:
                    raise ValueError()
                bound = payload["position"]
                if not all(isinstance(v, int) and not isinstance(v, bool) for v in bound[:2]) or not isinstance(bound[2], str) or not isinstance(bound[3], int):
                    raise ValueError()
            except (ValueError, KeyError, TypeError) as error:
                raise SourceError("cursor_invalid", "Use the next_cursor returned for this conversation.") from error
        shards = self.shards(root)
        if not shards:
            raise SourceError("database_missing", "No message/message_N.db shards were found.")
        items = []
        found = False
        for relative in shards:
            schema = self.schema(root, relative, keys)
            if table not in schema:
                continue
            found = True
            columns = self.require_table(schema, table, self.message_columns, relative)
            self.require_table(schema, "Name2Id", {"user_name"}, relative)
            compressed = ",hex(m.compress_content) AS compressed_hex" if "compress_content" in columns else ",NULL AS compressed_hex"
            marker = ",m.WCDB_CT_message_content AS compression" if "WCDB_CT_message_content" in columns else ",NULL AS compression"
            predicate = ""
            if bound:
                timestamp, sequence, shard, local_id = bound
                predicate = f" WHERE (m.create_time, m.sort_seq, {sql_text(relative)}, m.local_id) < ({timestamp}, {sequence}, {sql_text(shard)}, {local_id})"
            sql = f'SELECT m.local_id, m.server_id, m.local_type, m.sort_seq, m.real_sender_id, m.create_time, hex(m.message_content) AS body_hex, n.user_name AS sender{compressed}{marker} FROM "{table}" m LEFT JOIN Name2Id n ON n.rowid=m.real_sender_id{predicate} ORDER BY m.create_time DESC, m.sort_seq DESC, m.local_id DESC LIMIT {limit + 1}'
            for row in self.query(root, relative, sql, keys):
                content = bytes.fromhex(row["body_hex"] or "")
                compressed_body = bytes.fromhex(row["compressed_hex"] or "")
                if compressed_body:
                    content = compressed_body
                kind = int(row["local_type"]) & 0xFFFFFFFF
                text = None
                content_state = "unsupported_type"
                if kind == 1:
                    try:
                        if content.startswith(b"\x28\xb5\x2f\xfd"):
                            content = zstandard.ZstdDecompressor().decompress(content, max_output_size=4 * 1024 * 1024)
                        text = content.decode("utf-8")
                        content_state = "text"
                    except (UnicodeDecodeError, zstandard.ZstdError):
                        content_state = "decode_failed"
                items.append({"source": self.source_id, "id": f"{relative}:{row['local_id']}", "conversation_id": conversation_id, "sender_id": row["sender"], "timestamp": int(row["create_time"]), "type": kind, "text": text, "content_state": content_state, "server_id": str(row["server_id"]), "_position": [int(row["create_time"]), int(row["sort_seq"]), relative, int(row["local_id"])]})
        if not found:
            raise SourceError("conversation_not_found", "This conversation has no message table in the available local shards.")
        items.sort(key=lambda item: tuple(item["_position"]), reverse=True)
        more = len(items) > limit
        items = items[:limit]
        next_cursor = base64.urlsafe_b64encode(json.dumps({"conversation": conversation_id, "position": items[-1]["_position"]}).encode()).decode() if more else None
        for item in items:
            item["cursor"] = base64.urlsafe_b64encode(json.dumps({"conversation": conversation_id, "position": item["_position"]}).encode()).decode()
            del item["_position"]
        return {"items": items, "next_cursor": next_cursor, "shards_checked": len(shards), "coverage": "local_message_shards", "snapshot_consistency": "per_query"}

    def search_messages(self, query: str, conversation_id: str, limit: int = 20, scan_limit: int = 1000, cursor: str | None = None) -> dict[str, Any]:
        matches = []
        scanned = 0
        decode_failures = 0
        unsupported_types = 0
        next_cursor = cursor
        while scanned < scan_limit and len(matches) < limit:
            page = self.get_messages(conversation_id, limit=min(scan_limit - scanned, 200), cursor=next_cursor)
            for index, message in enumerate(page["items"]):
                scanned += 1
                decode_failures += message["content_state"] == "decode_failed"
                unsupported_types += message["content_state"] == "unsupported_type"
                next_cursor = message["cursor"] if index < len(page["items"]) - 1 or page["next_cursor"] else None
                if message["text"] is not None and query.casefold() in message["text"].casefold():
                    matches.append(message)
                if len(matches) == limit:
                    break
            if not next_cursor:
                break
        return {"items": matches, "scanned": scanned, "decode_failures": decode_failures, "unsupported_types": unsupported_types, "next_cursor": next_cursor, "truncated": bool(next_cursor), "incomplete": bool(next_cursor) or decode_failures > 0, "coverage": "decoded_local_text_in_one_conversation", "query": query}
