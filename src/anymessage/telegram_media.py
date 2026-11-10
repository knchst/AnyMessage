"""Message-bound classic Telegram export media and bounded local reads."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import stat
from typing import Any, Literal

from PIL import Image

from anymessage.attachments import (
    AttachmentRead, MAX_BINARY_BYTES, MAX_FILE_BYTES, extract_in_worker,
    sniff_mime, verify_image,
)
from anymessage.errors import SourceError


Variant = Literal["photo", "file", "thumbnail", "contact_vcard"]
OmissionReason = Literal["unavailable", "file_type", "file_size"]
_FIELDS: tuple[tuple[Variant, str], ...] = (
    ("photo", "photo_file_size"), ("file", "file_size"),
    ("thumbnail", "thumbnail_file_size"), ("contact_vcard", "contact_vcard_file_size"),
)
_OMISSIONS: dict[str, OmissionReason] = {
    "(File unavailable, please try again later)": "unavailable",
    "(File not included. Change data exporting settings to download.)": "file_type",
    "(File exceeds maximum size. Change data exporting settings to download.)": "file_size",
}


@dataclass(frozen=True, slots=True)
class _Relative:
    parts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Omitted:
    reason: OmissionReason


@dataclass(frozen=True, slots=True)
class _Denied:
    pass


@dataclass(frozen=True, slots=True)
class _Unsupported:
    pass


@dataclass(frozen=True, slots=True)
class ExportAttachment:
    id: str
    variant: Variant
    location: _Relative | _Omitted | _Denied | _Unsupported
    display_name: str
    declared_size: int | None
    declared_mime: str | None

    def _regular_path(self, root: Path) -> Path:
        if isinstance(self.location, _Omitted):
            raise SourceError("not_exported", "Telegram did not include this file in the export.")
        if isinstance(self.location, _Denied):
            raise SourceError("attachment_path_invalid", "The exported locator is not a safe relative path.")
        if isinstance(self.location, _Unsupported):
            raise SourceError("unsupported", "The exported locator representation is unsupported.")
        path = root
        try:
            for index, part in enumerate(self.location.parts):
                path = path / part
                mode = path.lstat().st_mode
                if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) if index == len(self.location.parts) - 1 else stat.S_ISDIR(mode)):
                    raise SourceError("attachment_path_invalid", "Media must use regular files and directories without symlinks.")
        except FileNotFoundError:
            raise SourceError("attachment_missing", "The referenced file is absent from the selected export folder.") from None
        except PermissionError:
            raise SourceError("attachment_access_denied", "Cannot access the referenced export file.") from None
        except OSError:
            raise SourceError("attachment_path_invalid", "Cannot resolve this export-relative file.") from None
        return path

    def describe(self, *, root: Path | None = None) -> dict[str, Any]:
        metadata = {
            "id": self.id, "source": "telegram_export", "variant": self.variant,
            "kind": "image" if self.variant in ("photo", "thumbnail") else "file",
            "display_name": self.display_name, "declared_size": self.declared_size,
            "declared_mime": self.declared_mime, "content_verified": False,
            "media_consistency": "current_local_files", "state": "not_checked",
        }
        if isinstance(self.location, _Omitted):
            metadata.update(state="not_exported", omission_reason=self.location.reason)
        elif isinstance(self.location, _Denied):
            metadata["state"] = "attachment_path_invalid"
        elif isinstance(self.location, _Unsupported):
            metadata["state"] = "unsupported"
        elif root is not None:
            try:
                metadata.update(state="available", local_size=self._regular_path(root).lstat().st_size)
            except SourceError as error:
                metadata["state"] = error.code
            except PermissionError:
                metadata["state"] = "attachment_access_denied"
            except FileNotFoundError:
                metadata["state"] = "attachment_missing"
            except OSError:
                metadata["state"] = "attachment_path_invalid"
        return metadata

    def read(self, root: Path, offset: int, limit: int, as_binary: bool) -> AttachmentRead:
        metadata = self.describe()
        try:
            path = self._regular_path(root)
            try:
                with path.open("rb") as handle:
                    data = handle.read(MAX_FILE_BYTES + 1)
            except FileNotFoundError:
                raise SourceError("attachment_missing", "The referenced file is absent from the selected export folder.") from None
            except OSError:
                raise SourceError("attachment_access_denied", "Cannot read the referenced export file.") from None
            if len(data) > MAX_FILE_BYTES:
                raise SourceError("attachment_too_large", "Exported attachment exceeds the 16 MiB file limit.")
            metadata["local_size"] = len(data)
            metadata["local_sha256"] = hashlib.sha256(data).hexdigest()
            if self.declared_size is not None and len(data) != self.declared_size:
                raise SourceError("attachment_size_mismatch", "Local bytes do not match the exported file size.")
            mime = sniff_mime(data)
            metadata.update(state="ready", mime_type=mime)
            if as_binary:
                if offset:
                    raise SourceError("offset_invalid", "Binary results are complete. Use offset zero.")
                if len(data) > MAX_BINARY_BYTES:
                    raise SourceError("attachment_too_large", "Binary result exceeds the 8 MiB limit.")
                metadata.update(size=len(data), truncated=False, next_offset=None, representation="original_bytes")
                return AttachmentRead(metadata, data=data, mime_type=mime)
            if self.variant in ("photo", "thumbnail") or mime.startswith("image/"):
                if offset:
                    raise SourceError("offset_invalid", "Image results are complete. Use offset zero.")
                if len(data) > MAX_BINARY_BYTES:
                    raise SourceError("attachment_too_large", "Image result exceeds the 8 MiB limit.")
                verify_image(data)
                metadata.update(truncated=False, next_offset=None, representation="image")
                return AttachmentRead(metadata, data=data, mime_type=mime, image=True)
            result = extract_in_worker(data)
            state = result["state"]
            metadata.update(state=state, extraction_truncated=result.get("truncated", False), representation="text", offset=offset)
            if "text_normalizations" in result:
                metadata["text_normalizations"] = result["text_normalizations"]
            if state not in ("ready", "no_extractable_text"):
                return AttachmentRead(metadata)
            text = result["text"]
            end = min(offset + limit, len(text))
            metadata.update(total_characters=len(text), next_offset=end if end < len(text) else None, truncated=end < len(text) or result.get("truncated", False))
            return AttachmentRead(metadata, text=text[offset:end], mime_type=mime)
        except SourceError as error:
            metadata["state"] = error.code
        except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            metadata["state"] = "attachment_decode_failed"
        return AttachmentRead(metadata)


def classic_attachments(message: dict[str, Any], snapshot: str, chat_id: str, number: int) -> tuple[ExportAttachment, ...]:
    attachments = []
    for variant, size_field in _FIELDS:
        if variant not in message:
            continue
        value = message[variant]
        location: _Relative | _Omitted | _Denied | _Unsupported
        if not isinstance(value, str):
            location = _Unsupported()
        elif value in _OMISSIONS:
            location = _Omitted(_OMISSIONS[value])
        elif value.startswith("(File "):
            location = _Unsupported()
        elif not value or value.startswith("/") or "\\" in value or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", value) or any(part in ("", ".", "..") for part in value.split("/")) or any(ord(char) < 32 or 127 <= ord(char) < 160 or 0xD800 <= ord(char) <= 0xDFFF for char in value):
            location = _Denied()
        else:
            location = _Relative(tuple(value.split("/")))
        display_name = location.parts[-1] if isinstance(location, _Relative) else variant
        name = message.get("file_name") if variant == "file" else None
        if isinstance(name, str) and name and name not in (".", "..") and not any(char in name for char in "/\\") and not any(ord(char) < 32 or 127 <= ord(char) < 160 or 0xD800 <= ord(char) <= 0xDFFF for char in name):
            display_name = name
        size = message.get(size_field)
        declared_size = size if type(size) is int and size >= 0 else None
        mime = message.get("mime_type") if variant == "file" else None
        declared_mime = mime if isinstance(mime, str) and mime and all(32 <= ord(char) < 127 for char in mime) else None
        identity = json.dumps(["telegram-export-attachment-v1", snapshot, chat_id, str(number), variant], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        attachments.append(ExportAttachment(hashlib.sha256(identity).hexdigest()[:32], variant, location, display_name, declared_size, declared_mime))
    return tuple(attachments)
