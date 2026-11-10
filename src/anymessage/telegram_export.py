from __future__ import annotations

import base64
from bisect import bisect_left
from dataclasses import dataclass
import hashlib
import heapq
import json
from pathlib import Path
import re
import stat
from types import MappingProxyType
from typing import Any, Iterator, Mapping

from anymessage.attachments import AttachmentRead
from anymessage.errors import SourceError
from anymessage.telegram_media import ExportAttachment, classic_attachments


MAX_EXPORT_BYTES = 16 * 1024 * 1024
_MIN_INT = -(2**63)
_MAX_INT = 2**63 - 1
_CHAT_TYPES = frozenset(("saved_messages", "replies", "verification_codes", "personal_chat", "bot_chat", "private_group", "private_supergroup", "public_supergroup", "private_channel", "public_channel"))
_MEDIA_KEYS = frozenset(("photo", "file", "thumbnail", "contact_vcard", "media_type"))
_STRUCTURED_KEYS = frozenset(("poll", "location_information", "place_name", "address", "contact_information", "game_title", "game_description", "game_link", "invoice_information", "todo_list", "giveaway_information", "giveaway_results"))
_COVERAGE_NOTE = "Only this exported snapshot is available. Exports may be time-filtered; Telegram documents that public group and channel exports contain only the exporter's messages. Opaque rich_message bodies, media, and structured objects are not searched. Undated unsupported records are counted outside chronology."
_MEDIA_NOTE = "Classic media uses current local files beneath the selected export folder. Exported sizes and local hashes do not establish Telegram authenticity."


def _invalid(location: str, requirement: str) -> None:
    raise SourceError("export_invalid", f"{location}: {requirement}")


def _string(value: Any, location: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        _invalid(location, "Expected a Unicode string without unpaired surrogates, or null.")
    return value


def _number(value: Any, location: str) -> int:
    if type(value) is not int or not _MIN_INT <= value <= _MAX_INT:
        _invalid(location, "Expected a signed 64-bit JSON integer.")
    return value


def _text(value: Any, location: str) -> str:
    if isinstance(value, str):
        return _string(value, location)
    if not isinstance(value, list):
        _invalid(location, "Expected text or a flat string/entity array.")
    parts = []
    for index, part in enumerate(value):
        field = f"{location}[{index}]"
        if isinstance(part, str):
            parts.append(_string(part, field))
        elif isinstance(part, dict) and isinstance(part.get("type"), str) and isinstance(part.get("text"), str):
            parts.append(_string(part["text"], field + ".text"))
        else:
            _invalid(field, "Expected a string or an entity with string type and text.")
    return "".join(parts)


def _integer(name: str, value: int, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise SourceError("request_invalid", f"{name} must be an integer between {minimum} and {maximum}.")


@dataclass(frozen=True, slots=True)
class _Message:
    number: int
    timestamp: int
    kind: str
    sender_id: str | None
    sender_name: str | None
    text: str | None
    media: bool
    unsupported_reason: str | None
    attachments: tuple[ExportAttachment, ...]


@dataclass(frozen=True, slots=True)
class _Chat:
    id: str
    name: str | None
    kind: str
    rows: tuple[_Message, ...]
    by_number: Mapping[int, int]
    undated: frozenset[int]


@dataclass(frozen=True, slots=True)
class _Scope:
    ids: tuple[str, ...]
    start: int | None
    end: int | None
    digest: str


def _chat(value: Any, location: str, snapshot: str) -> _Chat:
    if not isinstance(value, dict):
        _invalid(location, "Expected a Chat object.")
    number = _number(value.get("id"), location + ".id")
    if "type" not in value:
        _invalid(location + ".type", "Required chat type is missing.")
    kind = value["type"]
    if kind is None or kind == "":
        kind = "unknown"
    elif not isinstance(kind, str) or kind not in _CHAT_TYPES:
        _invalid(location + ".type", "Unsupported chat type. Use a supported Telegram Desktop JSON schema.")
    name = _string(value.get("name"), location + ".name")
    chat_id = f"telegram:{kind}:{number}"
    messages = value.get("messages")
    if not isinstance(messages, list):
        _invalid(location + ".messages", "Expected a messages array.")
    seen: set[int] = set()
    undated: set[int] = set()
    rows = []
    for index, message in enumerate(messages):
        field = f"{location}.messages[{index}]"
        if not isinstance(message, dict):
            _invalid(field, "Expected a Message object.")
        message_id = _number(message.get("id"), field + ".id")
        if message_id in seen:
            _invalid(field + ".id", "Duplicate message ID within a chat.")
        seen.add(message_id)
        message_type = message.get("type")
        if message_type not in ("message", "service", "unsupported"):
            _invalid(field + ".type", "Expected message, service, or unsupported.")
        if message_type == "unsupported" and set(message) == {"id", "type"}:
            undated.add(message_id)
            continue
        raw_date = message.get("date_unixtime")
        if not isinstance(raw_date, str) or len(raw_date) > 20 or re.fullmatch(r"-?[0-9]+", raw_date) is None:
            _invalid(field + ".date_unixtime", "Expected a signed 64-bit decimal Unix-second string.")
        timestamp = _number(int(raw_date), field + ".date_unixtime")
        sender_key, name_key = ("actor_id", "actor") if message_type == "service" else ("from_id", "from")
        sender_id = _string(message.get(sender_key), field + "." + sender_key)
        sender_name = _string(message.get(name_key), field + "." + name_key)
        media = bool(_MEDIA_KEYS.intersection(message))
        reason = None
        if "rich_message" in message:
            rich = message["rich_message"]
            if not isinstance(rich, dict) or not isinstance(rich.get("blocks"), list):
                _invalid(field + ".rich_message", "Expected an opaque rich-message object with blocks.")
            text = None
            reason = "rich_message"
        elif message_type == "unsupported":
            text = None
            reason = "unsupported"
        else:
            if "text" in message:
                text = _text(message["text"], field + ".text")
            elif message_type == "service" or media or _STRUCTURED_KEYS.intersection(message):
                text = None
            else:
                _invalid(field + ".text", "Required text body is missing. Export JSON from Telegram Desktop.")
            if not text and (message_type == "service" or media or _STRUCTURED_KEYS.intersection(message)):
                text = None
                reason = "service" if message_type == "service" else "media" if media else "structured_content"
        attachments = () if reason in ("rich_message", "unsupported") else classic_attachments(message, snapshot, chat_id, message_id)
        rows.append(_Message(message_id, timestamp, message_type, sender_id, sender_name, text, media, reason, attachments))
    ordered = tuple(sorted(rows, key=lambda row: (row.timestamp, row.number)))
    return _Chat(chat_id, name, kind, ordered, MappingProxyType({row.number: index for index, row in enumerate(ordered)}), frozenset(undated))


class TelegramExport:
    source_id = "telegram_export"

    def __init__(self, path: Path) -> None:
        try:
            self._root = path.parent.resolve(strict=True)
            path = self._root / path.name
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode):
                raise SourceError("export_unreadable", "Select a regular Telegram Desktop JSON file without a filename symlink. Pass the target's real filename.")
            if metadata.st_size > MAX_EXPORT_BYTES:
                raise SourceError("export_too_large", "JSON exceeds 16 MiB. Export one chat or a shorter period.")
            with path.open("rb") as handle:
                data = handle.read(MAX_EXPORT_BYTES + 1)
        except OSError as error:
            raise SourceError("export_unreadable", "Cannot read the selected Telegram Desktop JSON file. Check its path and permissions.") from error
        if len(data) > MAX_EXPORT_BYTES:
            raise SourceError("export_too_large", "JSON exceeds 16 MiB. Export one chat or a shorter period.")
        self._snapshot = hashlib.sha256(data).hexdigest()
        try:
            raw = json.loads(data.decode("utf-8-sig"))
        except json.JSONDecodeError as error:
            raise SourceError("export_invalid", f"Invalid JSON at line {error.lineno}, column {error.colno}. Export JSON from Telegram Desktop.") from None
        except (UnicodeError, ValueError, RecursionError):
            raise SourceError("export_invalid", "Expected bounded UTF-8 Telegram Desktop JSON, optionally with a BOM.") from None
        del data
        if not isinstance(raw, dict):
            _invalid("root", "Expected a single Chat or account export object.")
        single = any(key in raw for key in ("id", "type", "messages"))
        account = any(key in raw for key in ("chats", "left_chats"))
        if single == account:
            _invalid("root", "Expected either a single Chat or account chats lists, without mixing them.")
        chats = {}
        if single:
            chat = _chat(raw, "chat", self._snapshot)
            chats[chat.id] = chat
        else:
            for section in ("chats", "left_chats"):
                if section not in raw:
                    continue
                group = raw[section]
                if not isinstance(group, dict) or not isinstance(group.get("list"), list):
                    _invalid(section + ".list", "Expected a chat list array.")
                for index, value in enumerate(group["list"]):
                    location = f"{section}.list[{index}]"
                    chat = _chat(value, location, self._snapshot)
                    if chat.id in chats:
                        _invalid(location + ".id", "Duplicate qualified chat ID across selected lists.")
                    chats[chat.id] = chat
        self._chats = MappingProxyType(chats)
        self._ordered = tuple(sorted(chats.values(), key=lambda chat: (not bool(chat.rows), -chat.rows[-1].timestamp if chat.rows else 0, chat.id)))

    def status(self) -> dict[str, Any]:
        return {"source": self.source_id, "state": "ready", "coverage": "telegram_desktop_json_export", "coverage_note": _COVERAGE_NOTE, "snapshot_consistency": "immutable_export", "media_consistency": "current_local_files", "max_export_bytes": MAX_EXPORT_BYTES, "text_only": False, "chat_count": len(self._chats), "dated_message_count": sum(len(chat.rows) for chat in self._ordered), "undated_unsupported_count": sum(len(chat.undated) for chat in self._ordered), "rich_message_count": sum(row.unsupported_reason == "rich_message" for chat in self._ordered for row in chat.rows)}

    def list_conversations(self, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        _integer("limit", limit, 1, 200)
        _integer("offset", offset, 0, 100000)
        labels = {"saved_messages": "Saved Messages", "replies": "Replies", "verification_codes": "Verification Codes"}
        items = [{"source": self.source_id, "id": chat.id, "display_name": chat.name or labels.get(chat.kind, chat.id), "updated_at": chat.rows[-1].timestamp if chat.rows else None, "summary": None, "is_group": chat.kind in ("private_group", "private_supergroup", "public_supergroup"), "chat_type": chat.kind} for chat in self._ordered[offset:offset + limit]]
        return {"items": items, "next_offset": offset + limit if offset + limit < len(self._ordered) else None, "coverage": "telegram_desktop_json_export"}

    def _scope(self, conversation_id: str | None, conversation_ids: list[str] | None, start_time: int | None, end_time: int | None, query: str | None) -> _Scope:
        if (conversation_id is None) == (conversation_ids is None):
            raise SourceError("request_invalid", "Provide exactly one of conversation_id or conversation_ids.")
        if conversation_ids is not None and (not isinstance(conversation_ids, list) or not 1 <= len(conversation_ids) <= 8):
            raise SourceError("request_invalid", "conversation_ids must contain 1–8 distinct IDs.")
        ids = conversation_ids if conversation_ids is not None else [conversation_id]
        if any(not isinstance(value, str) or not 1 <= len(value) <= 512 for value in ids) or len(set(ids)) != len(ids):
            raise SourceError("request_invalid", "Select distinct conversation IDs of 1–512 characters.")
        for bound, value in (("start_time", start_time), ("end_time", end_time)):
            if value is not None:
                _integer(bound, value, _MIN_INT, _MAX_INT)
        if start_time is not None and end_time is not None and start_time >= end_time:
            raise SourceError("request_invalid", "start_time must be less than end_time.")
        for chat_id in ids:
            if chat_id not in self._chats:
                raise SourceError("conversation_not_found", "The selected conversation is absent from this export.")
        selected = tuple(sorted(ids))
        digest = hashlib.sha256(json.dumps(["tg1", self._snapshot, selected, start_time, end_time, query], ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
        return _Scope(selected, start_time, end_time, digest)

    def _cursor(self, scope: _Scope, chat: _Chat, message: _Message) -> str:
        return base64.urlsafe_b64encode(json.dumps(["tg1", scope.digest, chat.id, str(message.number)], separators=(",", ":")).encode()).decode()

    def _position(self, scope: _Scope, cursor: str | None) -> tuple[int, int, str] | None:
        if cursor is None:
            return None
        try:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 4096:
                raise ValueError
            token = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
            if not isinstance(token, list) or len(token) != 4 or token[:2] != ["tg1", scope.digest] or not all(isinstance(value, str) for value in token):
                raise ValueError
            chat_id, message_id = token[2:]
            if chat_id not in scope.ids:
                raise ValueError
            chat = self._chats[chat_id]
            number = int(message_id)
            if str(number) != message_id or number not in chat.by_number:
                raise ValueError
            row = chat.rows[chat.by_number[number]]
            if (scope.start is not None and row.timestamp < scope.start) or (scope.end is not None and row.timestamp >= scope.end):
                raise ValueError
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise SourceError("cursor_invalid", "Use a returned cursor with the same export bytes, query, selection, and date bounds.") from None
        return row.timestamp, row.number, chat.id

    def _rows(self, scope: _Scope, before: tuple[int, int, str] | None) -> Iterator[tuple[_Chat, _Message]]:
        def window(chat: _Chat) -> Iterator[tuple[_Chat, _Message]]:
            low = 0 if scope.start is None else bisect_left(chat.rows, scope.start, key=lambda row: row.timestamp)
            high = len(chat.rows) if scope.end is None else bisect_left(chat.rows, scope.end, key=lambda row: row.timestamp)
            if before is not None:
                high = min(high, bisect_left(chat.rows, before, key=lambda row: (row.timestamp, row.number, chat.id)))
            for index in range(high - 1, low - 1, -1):
                yield chat, chat.rows[index]

        return heapq.merge(*(window(self._chats[chat_id]) for chat_id in scope.ids), key=lambda pair: (pair[1].timestamp, pair[1].number, pair[0].id), reverse=True)

    def _item(self, scope: _Scope, chat: _Chat, row: _Message) -> dict[str, Any]:
        return {"source": self.source_id, "id": str(row.number), "conversation_id": chat.id, "sender_id": row.sender_id, "sender_name": row.sender_name, "timestamp": row.timestamp, "type": row.kind, "text": row.text, "content_state": "text" if row.text is not None else "unsupported_type", "unsupported_reason": row.unsupported_reason, "server_id": str(row.number), "attachments": [ref.describe() for ref in row.attachments], "attachment_state": "available" if row.attachments else "unsupported" if row.media or row.unsupported_reason == "rich_message" else "none", "cursor": self._cursor(scope, chat, row)}

    def _coverage(self, scope: _Scope) -> dict[str, Any]:
        return {"coverage": "telegram_desktop_json_export", "snapshot_consistency": "immutable_export", "shards_checked": 0, "conversation_ids": list(scope.ids), "start_time": scope.start, "end_time": scope.end, "undated_unsupported_count": sum(len(self._chats[chat_id].undated) for chat_id in scope.ids)}

    def get_messages(self, conversation_id: str, limit: int = 50, cursor: str | None = None, *, start_time: int | None = None, end_time: int | None = None) -> dict[str, Any]:
        _integer("limit", limit, 1, 200)
        scope = self._scope(conversation_id, None, start_time, end_time, None)
        rows = self._rows(scope, self._position(scope, cursor))
        items = []
        for chat, row in rows:
            items.append(self._item(scope, chat, row))
            if len(items) == limit:
                break
        more = next(rows, None) is not None
        return {"items": items, "next_cursor": items[-1]["cursor"] if more else None, **self._coverage(scope)}

    def search_messages(self, query: str, conversation_id: str | None = None, limit: int = 20, scan_limit: int = 1000, cursor: str | None = None, *, conversation_ids: list[str] | None = None, start_time: int | None = None, end_time: int | None = None) -> dict[str, Any]:
        if not isinstance(query, str) or not 1 <= len(query) <= 1000 or any(0xD800 <= ord(char) <= 0xDFFF for char in query):
            raise SourceError("request_invalid", "query must contain 1–1000 Unicode characters.")
        _integer("limit", limit, 1, 200)
        _integer("scan_limit", scan_limit, 1, 10000)
        scope = self._scope(conversation_id, conversation_ids, start_time, end_time, query)
        rows = self._rows(scope, self._position(scope, cursor))
        items = []
        scanned = unsupported = 0
        last = None
        folded = query.casefold()
        for chat, row in rows:
            scanned += 1
            last = (chat, row)
            if row.text is None:
                unsupported += 1
            elif folded in row.text.casefold():
                items.append(self._item(scope, chat, row))
            if scanned == scan_limit or len(items) == limit:
                break
        more = next(rows, None) is not None
        next_cursor = self._cursor(scope, *last) if more else None
        coverage = self._coverage(scope)
        return {"items": items, "scanned": scanned, "decode_failures": 0, "unsupported_types": unsupported, "next_cursor": next_cursor, "truncated": more, "incomplete": more or coverage["undated_unsupported_count"] > 0, **coverage, "coverage": "telegram_desktop_json_export_text", "coverage_note": _COVERAGE_NOTE, "query": query}

    def _exact(self, conversation_id: str, message_id: str) -> tuple[_Chat, int, _Scope]:
        scope = self._scope(conversation_id, None, None, None, None)
        if not isinstance(message_id, str) or len(message_id) > 20 or re.fullmatch(r"0|[1-9][0-9]*|-[1-9][0-9]*", message_id) is None or not _MIN_INT <= int(message_id) <= _MAX_INT:
            raise SourceError("message_id_invalid", "Use the canonical signed 64-bit decimal id returned with its conversation_id.")
        chat = self._chats[conversation_id]
        number = int(message_id)
        if number in chat.undated:
            raise SourceError("message_undated", "This exported unsupported record has no timestamp and cannot be placed in chronology.")
        if number not in chat.by_number:
            raise SourceError("message_not_found", "The message ID is absent from the selected exported chat.")
        return chat, chat.by_number[number], scope

    def get_message_context(self, conversation_id: str, message_id: str, *, before: int = 3, after: int = 3) -> dict[str, Any]:
        _integer("before", before, 0, 50)
        _integer("after", after, 0, 50)
        chat, index, scope = self._exact(conversation_id, message_id)
        low, high = max(0, index - before), min(len(chat.rows), index + after + 1)
        return {"items": [self._item(scope, chat, row) for row in chat.rows[low:high]], "anchor_index": index - low, "conversation_id": conversation_id, "message_id": message_id, "has_more_before": low > 0, "has_more_after": high < len(chat.rows), **self._coverage(scope)}

    def list_attachments(self, conversation_id: str, message_id: str) -> dict[str, Any]:
        chat, index, _ = self._exact(conversation_id, message_id)
        row = chat.rows[index]
        return {"source": self.source_id, "conversation_id": conversation_id, "message_id": message_id, "items": [ref.describe(root=self._root) for ref in row.attachments], "metadata_state": "available" if row.attachments else "unsupported" if row.media or row.unsupported_reason == "rich_message" else "none", "media_declared": None if row.unsupported_reason == "rich_message" else row.media, "media_consistency": "current_local_files", "message": _MEDIA_NOTE}

    def read_attachment(self, conversation_id: str, message_id: str, attachment_id: str, offset: int = 0, limit: int = 20000, as_binary: bool = False) -> AttachmentRead:
        chat, index, _ = self._exact(conversation_id, message_id)
        _integer("offset", offset, 0, 2000000)
        _integer("limit", limit, 1, 100000)
        if not isinstance(attachment_id, str) or len(attachment_id) != 32 or type(as_binary) is not bool:
            raise SourceError("request_invalid", "attachment_id must be a 32-character string and as_binary a boolean.")
        ref = next((ref for ref in chat.rows[index].attachments if ref.id == attachment_id), None)
        if ref is None:
            raise SourceError("attachment_not_found", "Use an attachment id returned for this exact message and export snapshot.")
        result = ref.read(self._root, offset, limit, as_binary)
        result.metadata.update(conversation_id=conversation_id, message_id=message_id)
        return result
