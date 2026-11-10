"""Read-only WeChat 4.x database access using the SQLCipher executable."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import plistlib
import re
import stat
import subprocess
from typing import Any

import zstandard

from anymessage.attachments import Attachments, AttachmentRead, message_attachments
from anymessage.errors import SourceError
from anymessage.settings import Settings


def sql_text(value: str) -> str:
    return "CAST(X'" + value.encode("utf-8").hex() + "' AS TEXT)"


Position = tuple[int, int, str, int, int]
ShardTables = list[tuple[str, list[tuple[int, str, set[str]]]]]


def request_integer(name: str, value: int, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise SourceError("request_invalid", f"{name} must be an integer between {minimum} and {maximum}.")


@dataclass(frozen=True)
class MessageScope:
    ids: tuple[str, ...]
    start: int | None
    end: int | None

    @classmethod
    def parse(cls, conversation_id: str | None, conversation_ids: list[str] | None, start_time: int | None, end_time: int | None) -> MessageScope:
        if (conversation_id is None) == (conversation_ids is None):
            raise SourceError("request_invalid", "Provide exactly one of conversation_id or conversation_ids.")
        if conversation_ids is not None and (not isinstance(conversation_ids, list) or not 1 <= len(conversation_ids) <= 8):
            raise SourceError("request_invalid", "conversation_ids must contain 1–8 explicit conversation ids.")
        ids = [conversation_id] if conversation_ids is None else conversation_ids
        if any(not isinstance(value, str) or not 1 <= len(value) <= 512 for value in ids):
            raise SourceError("request_invalid", "Each conversation id must be a string of 1–512 characters.")
        if len(set(ids)) != len(ids):
            raise SourceError("request_invalid", "conversation_ids must not contain duplicate ids.")
        for name, value in (("start_time", start_time), ("end_time", end_time)):
            if value is not None:
                request_integer(name, value, -(2**63), 2**63 - 1)
        if start_time is not None and end_time is not None and start_time >= end_time:
            raise SourceError("request_invalid", "start_time must be less than end_time.")
        return cls(tuple(sorted(ids)), start_time, end_time)

    def metadata(self) -> dict[str, Any]:
        return {"conversation_ids": list(self.ids), "start_time": self.start, "end_time": self.end}

    def cursor(self, digest: str, position: Position) -> str:
        payload = {"conversation": self.ids[0], "position": position[:4]} if len(self.ids) == 1 and self.start is None and self.end is None else {"v": 2, "s": digest, "p": position}
        return base64.urlsafe_b64encode(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()).decode("ascii")

    def position(self, cursor: str | None, digest: str, shards: list[str]) -> Position | None:
        if cursor is None:
            return None
        try:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 4096:
                raise ValueError()
            payload = json.loads(base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True))
            if not isinstance(payload, dict):
                raise ValueError()
            if set(payload) == {"conversation", "position"}:
                if len(self.ids) != 1 or self.start is not None or self.end is not None or payload["conversation"] != self.ids[0]:
                    raise ValueError()
                position = payload["position"]
                if not isinstance(position, list) or len(position) != 4:
                    raise ValueError()
                position = [*position, 0]
            else:
                if set(payload) != {"v", "s", "p"} or type(payload["v"]) is not int or payload["v"] != 2 or payload["s"] != digest:
                    raise ValueError()
                position = payload["p"]
            if not isinstance(position, list) or len(position) != 5:
                raise ValueError()
            if any(type(position[i]) is not int or not -(2**63) <= position[i] < 2**63 for i in (0, 1, 3, 4)):
                raise ValueError()
            if position[2] not in shards or not 0 <= position[4] < len(self.ids):
                raise ValueError()
            return tuple(position)
        except (ValueError, TypeError) as error:
            raise SourceError("cursor_invalid", "Use a cursor returned for this account, conversation selection, and time range.") from error


@dataclass(frozen=True)
class MessageWindow:
    rows: list[tuple[Position, dict[str, Any]]]
    more: bool


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

    @staticmethod
    def _message_reference(message_id: str) -> tuple[str, int]:
        match = re.fullmatch(r"(message/message_\d+\.db):([1-9][0-9]{0,18})", message_id) if isinstance(message_id, str) and 1 <= len(message_id) <= 128 else None
        if match is None or int(match[2]) > 2**63 - 1:
            raise SourceError("message_id_invalid", "Use a message id returned for this conversation.")
        return match[1], int(match[2])

    @staticmethod
    def _message_select(table: str, columns: set[str], rank: int = 0, *, with_sender: bool = True) -> str:
        compressed = "hex(m.compress_content)" if "compress_content" in columns else "NULL"
        packed = "hex(m.packed_info_data)" if "packed_info_data" in columns else "NULL"
        marker = "m.WCDB_CT_message_content" if "WCDB_CT_message_content" in columns else "NULL"
        sender = "n.user_name" if with_sender else "NULL"
        join = " LEFT JOIN Name2Id n ON n.rowid=m.real_sender_id" if with_sender else ""
        return f'SELECT m.local_id,m.server_id,m.local_type,m.sort_seq,m.real_sender_id,m.create_time,hex(m.message_content) AS body_hex,{sender} AS sender,{compressed} AS compressed_hex,{marker} AS compression,{packed} AS packed_hex,{rank} AS chat_rank FROM "{table}" m{join}'

    def _exact_message(self, root: Path, keys: dict[str, dict[str, str]], relative: str, table: str, columns: set[str], local_id: int, *, with_sender: bool = True) -> dict[str, Any]:
        rows = self.query(root, relative, self._message_select(table, columns, with_sender=with_sender) + f" WHERE m.local_id={local_id} LIMIT 2", keys)
        if len(rows) != 1:
            raise SourceError("message_not_found", "No unique message matches this conversation and message id.")
        return rows[0]

    def _message_context(self, scope: MessageScope, cursor: str | None) -> tuple[Path, dict[str, dict[str, str]], ShardTables, Position | None, str]:
        root, keys = self.selected_root(), self.keys()
        shards = self.shards(root)
        if not shards:
            raise SourceError("database_missing", "No message/message_N.db shards were found.")
        digest = hashlib.sha256(json.dumps([str(root), scope.ids, scope.start, scope.end], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        before = scope.position(cursor, digest, shards)
        tables = [(rank, "Msg_" + hashlib.md5(identity.encode()).hexdigest()) for rank, identity in enumerate(scope.ids)]
        selected: ShardTables = []
        found = set()
        for relative in shards:
            schema = self.schema(root, relative, keys)
            present = []
            for rank, table in tables:
                if table not in schema:
                    continue
                columns = self.require_table(schema, table, self.message_columns, relative)
                self.require_table(schema, "Name2Id", {"user_name"}, relative)
                present.append((rank, table, columns))
                found.add(rank)
            selected.append((relative, present))
        missing = [identity for rank, identity in enumerate(scope.ids) if rank not in found]
        if missing:
            raise SourceError("conversation_not_found", "No message table was found for conversation ids: " + ", ".join(missing))
        return root, keys, selected, before, digest

    def _read_window(self, scope: MessageScope, root: Path, keys: dict[str, dict[str, str]], shard_tables: ShardTables, boundary: Position | None, limit: int, *, newer: bool = False) -> MessageWindow:
        top: list[tuple[Position, dict[str, Any]]] = []
        comparison, order = (">", "ASC") if newer else ("<", "DESC")
        for relative, tables in shard_tables:
            if not tables:
                continue
            branches = []
            for rank, table, columns in tables:
                predicates = []
                if scope.start is not None:
                    predicates.append(f"m.create_time >= {scope.start}")
                if scope.end is not None:
                    predicates.append(f"m.create_time < {scope.end}")
                if boundary is not None:
                    timestamp, sequence, shard, local_id, chat_rank = boundary
                    predicates.append(f"(m.create_time,m.sort_seq,{sql_text(relative)},m.local_id,{rank}) {comparison} ({timestamp},{sequence},{sql_text(shard)},{local_id},{chat_rank})")
                predicate = " WHERE " + " AND ".join(predicates) if predicates else ""
                branches.append(self._message_select(table, columns, rank) + predicate)
            sql = " UNION ALL ".join(branches) + f" ORDER BY create_time {order},sort_seq {order},local_id {order},chat_rank {order} LIMIT {limit + 1}"
            rows = self.query(root, relative, sql, keys)
            top.extend(((int(row["create_time"]), int(row["sort_seq"]), relative, int(row["local_id"]), row["chat_rank"]), row) for row in rows)
            top.sort(key=lambda item: item[0], reverse=not newer)
            del top[limit + 1:]
        return MessageWindow(top[:limit], len(top) > limit)

    def _decode_message(self, scope: MessageScope, digest: str, position: Position, row: dict[str, Any]) -> dict[str, Any]:
        content = bytes.fromhex(row["compressed_hex"] or row["body_hex"] or "")
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
        conversation_id = scope.ids[position[4]]
        message_id = f"{position[2]}:{position[3]}"
        attachments, attachment_state = message_attachments(row, message_id, conversation_id)
        return {"source": self.source_id, "id": message_id, "conversation_id": conversation_id, "sender_id": row["sender"], "timestamp": position[0], "type": kind, "text": text, "content_state": content_state, "server_id": str(row["server_id"]), "attachments": [item.metadata() for item in attachments], "attachment_state": attachment_state, "cursor": scope.cursor(digest, position)}

    def get_messages(self, conversation_id: str, limit: int = 50, cursor: str | None = None, *, start_time: int | None = None, end_time: int | None = None) -> dict[str, Any]:
        scope = MessageScope.parse(conversation_id, None, start_time, end_time)
        request_integer("limit", limit, 1, 200)
        root, keys, shards, before, digest = self._message_context(scope, cursor)
        window = self._read_window(scope, root, keys, shards, before, limit)
        items = [self._decode_message(scope, digest, position, row) for position, row in window.rows]
        return {"items": items, "next_cursor": items[-1]["cursor"] if window.more else None, "shards_checked": len(shards), "coverage": "local_message_shards", "snapshot_consistency": "per_query", **scope.metadata()}

    def get_message_context(self, conversation_id: str, message_id: str, *, before: int = 3, after: int = 3) -> dict[str, Any]:
        scope = MessageScope.parse(conversation_id, None, None, None)
        request_integer("before", before, 0, 50)
        request_integer("after", after, 0, 50)
        relative, local_id = self._message_reference(message_id)
        root, keys, shards, _, digest = self._message_context(scope, None)
        tables = next((tables for shard, tables in shards if shard == relative), [])
        if not tables:
            raise SourceError("message_not_found", "This message is not available in the selected conversation and shard.")
        _, table, columns = tables[0]
        row = self._exact_message(root, keys, relative, table, columns, local_id)
        position = (int(row["create_time"]), int(row["sort_seq"]), relative, local_id, 0)
        older = self._read_window(scope, root, keys, shards, position, before)
        newer = self._read_window(scope, root, keys, shards, position, after, newer=True)
        rows = [*reversed(older.rows), (position, row), *newer.rows]
        items = [self._decode_message(scope, digest, point, record) for point, record in rows]
        return {"items": items, "anchor_index": len(older.rows), "conversation_id": conversation_id, "message_id": message_id, "has_more_before": older.more, "has_more_after": newer.more, "shards_checked": len(shards), "coverage": "local_message_shards", "snapshot_consistency": "per_query"}

    def attachment_context(self, conversation_id: str, message_id: str) -> Attachments:
        scope = MessageScope.parse(conversation_id, None, None, None)
        relative, local_id = self._message_reference(message_id)
        root, keys = self.selected_root(), self.keys()
        if relative not in self.shards(root):
            raise SourceError("message_not_found", "The message shard is not available.")
        table = "Msg_" + hashlib.md5(scope.ids[0].encode()).hexdigest()
        schema = self.schema(root, relative, keys)
        if table not in schema:
            raise SourceError("message_not_found", "This message does not belong to the selected conversation.")
        columns = self.require_table(schema, table, self.message_columns, relative)
        row = self._exact_message(root, keys, relative, table, columns, local_id, with_sender=False)
        return Attachments(self, root, keys, conversation_id, row, message_id)

    def list_attachments(self, conversation_id: str, message_id: str) -> dict[str, Any]:
        return self.attachment_context(conversation_id, message_id).list()

    def read_attachment(self, conversation_id: str, message_id: str, attachment_id: str, offset: int = 0, limit: int = 20000, as_binary: bool = False) -> AttachmentRead:
        return self.attachment_context(conversation_id, message_id).read(attachment_id, offset, limit, as_binary)

    def search_messages(self, query: str, conversation_id: str | None = None, limit: int = 20, scan_limit: int = 1000, cursor: str | None = None, *, conversation_ids: list[str] | None = None, start_time: int | None = None, end_time: int | None = None) -> dict[str, Any]:
        scope = MessageScope.parse(conversation_id, conversation_ids, start_time, end_time)
        if not isinstance(query, str) or not 1 <= len(query) <= 1000:
            raise SourceError("request_invalid", "query must be a string of 1–1000 characters.")
        request_integer("limit", limit, 1, 200)
        request_integer("scan_limit", scan_limit, 1, 10000)
        root, keys, shards, before, digest = self._message_context(scope, cursor)
        matches = []
        scanned = decode_failures = unsupported_types = 0
        next_cursor = None
        needle = query.casefold()
        while scanned < scan_limit:
            window = self._read_window(scope, root, keys, shards, before, min(200, scan_limit - scanned))
            for index, (position, row) in enumerate(window.rows):
                message = self._decode_message(scope, digest, position, row)
                scanned += 1
                decode_failures += message["content_state"] == "decode_failed"
                unsupported_types += message["content_state"] == "unsupported_type"
                next_cursor = message["cursor"] if index + 1 < len(window.rows) or window.more else None
                if message["text"] is not None and needle in message["text"].casefold():
                    matches.append(message)
                if len(matches) == limit or scanned == scan_limit:
                    break
            if not next_cursor or len(matches) == limit or scanned == scan_limit:
                break
            before = window.rows[-1][0]
        return {"items": matches, "scanned": scanned, "decode_failures": decode_failures, "unsupported_types": unsupported_types, "next_cursor": next_cursor, "truncated": next_cursor is not None, "incomplete": next_cursor is not None or decode_failures > 0, "coverage": "decoded_local_text_in_selected_conversations" if len(scope.ids) > 1 else "decoded_local_text_in_one_conversation", "query": query, "shards_checked": len(shards), "snapshot_consistency": "per_query", **scope.metadata()}
