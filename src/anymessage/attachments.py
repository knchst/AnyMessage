"""Message-bound attachment metadata, local cache resolution, and bounded views."""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
from io import BytesIO
import json
from pathlib import Path
import posixpath
import re
import subprocess
import sys
from typing import Any, TYPE_CHECKING
import warnings
import xml.etree.ElementTree as ET
import zipfile

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from PIL import Image, UnidentifiedImageError
import zstandard

from anymessage.errors import SourceError

if TYPE_CHECKING:
    from anymessage.wechat import WeChat

MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_BINARY_BYTES = 8 * 1024 * 1024
MAX_XML_BYTES = 4 * 1024 * 1024
MAX_TEXT_CHARS = 2_000_000
MAX_ZIP_BYTES = 32 * 1024 * 1024
MAX_ZIP_PARTS = 1000
MAX_PDF_PAGES = 100
MAX_IMAGE_PIXELS = 25_000_000
EXTRACT_TIMEOUT = 20
HASH = re.compile(r"[0-9a-fA-F]{32}\Z")


@dataclass(frozen=True)
class AttachmentRef:
    message_id: str
    server_id: str
    kind: str
    display_name: str
    size: int | None = None
    md5: str | None = None
    cache_basename: str | None = None
    variant: str = "original"
    conversation_id: str = ""

    @property
    def id(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:32]

    def metadata(self) -> dict[str, Any]:
        return {"id": self.id, **asdict(self), "state": "not_checked"}


@dataclass(frozen=True)
class AttachmentRead:
    metadata: dict[str, Any]
    text: str | None = None
    data: bytes | None = None
    mime_type: str | None = None
    image: bool = False


def xml_document(data: bytes) -> ET.Element:
    declarations = data.replace(b"\x00", b"").upper()
    if len(data) > MAX_XML_BYTES or b"<!DOCTYPE" in declarations or b"<!ENTITY" in declarations:
        raise ValueError("XML exceeds limits or declares entities")
    return ET.fromstring(data)


def message_body(row: dict[str, Any]) -> bytes:
    data = bytes.fromhex(row.get("compressed_hex") or row.get("body_hex") or "")
    if data.startswith(b"\x28\xb5\x2f\xfd"):
        with zstandard.ZstdDecompressor().stream_reader(BytesIO(data)) as stream:
            data = stream.read(MAX_XML_BYTES + 1)
    if len(data) > MAX_XML_BYTES:
        raise ValueError("Message metadata exceeds limits")
    return data


def protobuf_fields(data: bytes) -> list[tuple[int, int, bytes | int]]:
    def varint(position: int) -> tuple[int, int]:
        value = 0
        for shift in range(0, 70, 7):
            if position >= len(data):
                raise ValueError("Truncated protobuf")
            byte = data[position]
            position += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value, position
        raise ValueError("Invalid protobuf varint")

    if len(data) > MAX_XML_BYTES:
        raise ValueError("Protobuf exceeds limits")
    result = []
    position = 0
    while position < len(data):
        tag, position = varint(position)
        wire = tag & 7
        if tag >> 3 == 0:
            raise ValueError("Invalid protobuf tag")
        if wire == 0:
            value, position = varint(position)
        else:
            if wire == 2:
                size, position = varint(position)
            elif wire in (1, 5):
                size = 8 if wire == 1 else 4
            else:
                raise ValueError("Unsupported protobuf wire type")
            if size > len(data) - position:
                raise ValueError("Truncated protobuf field")
            value = data[position : position + size]
            position += size
        result.append((tag >> 3, wire, value))
    return result


def message_attachments(
    row: dict[str, Any], message_id: str, conversation_id: str = ""
) -> tuple[list[AttachmentRef], str]:
    kind = int(row["local_type"]) & 0xFFFFFFFF
    server_id = str(row["server_id"])
    if kind not in (3, 49):
        return [], "unsupported_type"
    try:
        if kind == 49:
            body = message_body(row)
            if b":\n" in body and not body.lstrip().startswith(b"<"):
                body = body.partition(b":\n")[2]
            document = xml_document(body)
            app = document if document.tag == "appmsg" else document.find("./appmsg")
            if app is None or app.findtext("type") != "6":
                return [], "unsupported_type"
            name = app.findtext("title") or "attachment"
            digest = app.findtext("appattach/md5") or app.findtext("md5") or ""
            size = app.findtext("appattach/totallen") or ""
            if not HASH.fullmatch(digest) or not size.isdecimal() or len(size) > 20:
                return [], "metadata_invalid"
            return [
                AttachmentRef(
                    message_id,
                    server_id,
                    "file",
                    name[:1024],
                    int(size),
                    digest.lower(),
                    conversation_id=conversation_id,
                )
            ], "metadata"
        packed = bytes.fromhex(row.get("packed_hex") or "")
        hashes = set()
        for number, wire, nested in protobuf_fields(packed):
            if number == 3 and wire == 2:
                for field, inner_wire, value in protobuf_fields(nested):
                    if field == 4 and inner_wire == 2:
                        candidate = value.decode("ascii")
                        if HASH.fullmatch(candidate):
                            hashes.add(candidate.lower())
        if len(hashes) > 1:
            return [], "metadata_ambiguous"
        basename = next(iter(hashes), None)
        return [
            AttachmentRef(
                message_id,
                server_id,
                "image",
                "image",
                cache_basename=basename,
                variant=variant,
                conversation_id=conversation_id,
            )
            for variant in ("original", "hd", "thumbnail")
        ], "metadata" if basename else "index_required"
    except (ValueError, UnicodeDecodeError, ET.ParseError, zstandard.ZstdError):
        return [], "metadata_invalid"


class Attachments:
    def __init__(
        self,
        reader: WeChat,
        root: Path,
        keys: dict[str, dict[str, str]],
        conversation_id: str,
        row: dict[str, Any],
        message_id: str,
    ):
        self.reader, self.root, self.keys = reader, root, keys
        self.conversation_id, self.row, self.message_id = conversation_id, row, message_id
        self.refs, self.metadata_state = message_attachments(row, message_id, conversation_id)

    def image_basename(self) -> str:
        from anymessage.wechat import sql_text

        relative = "message/message_resource.db"
        schema = self.reader.schema(self.root, relative, self.keys)
        self.reader.require_table(schema, "ChatName2Id", {"user_name"}, relative)
        self.reader.require_table(
            schema, "MessageResourceInfo", {"chat_id", "message_local_id", "message_svr_id", "packed_info"}, relative
        )
        rows = self.reader.query(
            self.root,
            relative,
            "SELECT hex(i.packed_info) AS packed_hex FROM MessageResourceInfo i JOIN ChatName2Id n ON n.rowid=i.chat_id WHERE n.user_name="
            + sql_text(self.conversation_id)
            + f" AND i.message_local_id={int(self.row['local_id'])} AND i.message_svr_id={int(self.row['server_id'])} LIMIT 3",
            self.keys,
        )
        if len(rows) != 1:
            raise SourceError(
                "attachment_ambiguous" if rows else "attachment_missing",
                "No unique image resource matches this message.",
            )
        hashes = set()
        for number, wire, nested in protobuf_fields(bytes.fromhex(rows[0]["packed_hex"] or "")):
            if number == 2 and wire == 2:
                for field, inner_wire, value in protobuf_fields(nested):
                    if field == 1 and inner_wire == 2:
                        candidate = value.decode("ascii")
                        if HASH.fullmatch(candidate):
                            hashes.add(candidate.lower())
        if len(hashes) != 1:
            raise SourceError("metadata_invalid", "Image resource has no unique supported cache identity.")
        return next(iter(hashes))

    def path_for(self, ref: AttachmentRef) -> Path:
        from anymessage.wechat import sql_text

        account = self.root.parent.resolve()
        if ref.kind == "image":
            basename = ref.cache_basename or self.image_basename()
            month = datetime.fromtimestamp(int(self.row["create_time"])).strftime("%Y-%m")
            suffix = {"original": ".dat", "hd": "_h.dat", "thumbnail": "_t.dat"}[ref.variant]
            candidate = (
                account
                / "msg/attach"
                / hashlib.md5(self.conversation_id.encode()).hexdigest()
                / month
                / "Img"
                / (basename + suffix)
            )
        else:
            relative = "hardlink/hardlink.db"
            schema = self.reader.schema(self.root, relative, self.keys)
            self.reader.require_table(
                schema, "file_hardlink_info_v4", {"md5", "file_name", "file_size", "dir1", "dir2"}, relative
            )
            self.reader.require_table(schema, "dir2id", {"username"}, relative)
            rows = self.reader.query(
                self.root,
                relative,
                "SELECT DISTINCT f.file_name,f.file_size,COALESCE(d1.username,'') AS dir1,COALESCE(d2.username,'') AS dir2 FROM file_hardlink_info_v4 f LEFT JOIN dir2id d1 ON d1.rowid=f.dir1 LEFT JOIN dir2id d2 ON d2.rowid=f.dir2 WHERE lower(f.md5)="
                + sql_text(ref.md5)
                + " AND f.file_name="
                + sql_text(ref.display_name)
                + f" AND f.file_size={ref.size} LIMIT 3",
                self.keys,
            )
            candidates = set()
            for row in rows:
                components = [row["dir1"], row["dir2"], row["file_name"]]
                if any(part and (Path(part).name != part or part in (".", "..")) for part in components):
                    raise SourceError("attachment_path_invalid", "Attachment index path is outside the selected cache.")
                candidates.add(account / "msg/file" / Path(*[part for part in components if part]))
            if len(candidates) != 1:
                raise SourceError(
                    "attachment_ambiguous" if candidates else "attachment_missing",
                    "No unique file index matches the message name, digest, and size.",
                )
            candidate = next(iter(candidates))
        resolved = candidate.resolve()
        if ref.kind == "image" and resolved != candidate:
            raise SourceError("attachment_path_invalid", "Image cache locators must not redirect to another path.")
        if not resolved.is_relative_to(account / "msg"):
            raise SourceError("attachment_path_invalid", "Attachment path is outside the selected account cache.")
        if not resolved.is_file():
            raise SourceError(
                "attachment_missing", "This attachment variant is not cached locally. Open or download it in WeChat."
            )
        return resolved

    def list(self) -> dict[str, Any]:
        items = []
        for ref in self.refs:
            item = ref.metadata()
            try:
                path = self.path_for(ref)
                item.update(state="available", cached_size=path.stat().st_size, content_verified=False)
            except (SourceError, OSError, ValueError) as error:
                item["state"] = error.code if isinstance(error, SourceError) else "attachment_read_failed"
            items.append(item)
        return {
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "items": items,
            "metadata_state": self.metadata_state,
        }

    def read(self, attachment_id: str, offset: int, limit: int, as_binary: bool) -> AttachmentRead:
        ref = next((item for item in self.refs if item.id == attachment_id), None)
        if ref is None:
            raise SourceError("attachment_not_found", "Use an attachment id returned for this exact message.")
        metadata = {**ref.metadata(), "content_verified": False}
        try:
            path = self.path_for(ref)
            with path.open("rb") as handle:
                data = handle.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise SourceError("attachment_too_large", "Cached attachment exceeds the 16 MiB file limit.")
            if ref.kind == "file" and (len(data) != ref.size or hashlib.md5(data).hexdigest() != ref.md5):
                raise SourceError(
                    "attachment_integrity_failed", "Cached bytes do not match the message digest and size."
                )
            metadata.update(cached_size=len(data), state="ready")
            if ref.kind == "image":
                data = decode_image_cache(data, self.root.parent)
            metadata["content_verified"] = True
            mime = sniff_mime(data)
            metadata["mime_type"] = mime
            if as_binary:
                if offset:
                    raise SourceError("offset_invalid", "Binary results are complete. Use offset zero.")
                if len(data) > MAX_BINARY_BYTES:
                    raise SourceError("attachment_too_large", "Binary result exceeds the 8 MiB limit.")
                metadata.update(
                    size=len(data),
                    truncated=False,
                    next_offset=None,
                    representation="decoded_image" if ref.kind == "image" else "original_bytes",
                )
                return AttachmentRead(metadata, data=data, mime_type=mime)
            if mime.startswith("image/"):
                if offset:
                    raise SourceError("offset_invalid", "Image results are complete. Use offset zero.")
                verify_image(data)
                if len(data) > MAX_BINARY_BYTES:
                    raise SourceError("attachment_too_large", "Image result exceeds the 8 MiB limit.")
                metadata.update(truncated=False, next_offset=None, representation="image")
                return AttachmentRead(metadata, data=data, mime_type=mime, image=True)
            result = extract_in_worker(data)
            state = result["state"]
            metadata.update(
                state=state, extraction_truncated=result.get("truncated", False), representation="text", offset=offset
            )
            if state not in ("ready", "no_extractable_text"):
                return AttachmentRead(metadata)
            text = result["text"]
            end = min(offset + limit, len(text))
            metadata.update(
                total_characters=len(text),
                next_offset=end if end < len(text) else None,
                truncated=end < len(text) or result.get("truncated", False),
            )
            return AttachmentRead(metadata, text=text[offset:end], mime_type=mime)
        except (
            SourceError,
            OSError,
            ValueError,
            UnidentifiedImageError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as error:
            metadata["state"] = error.code if isinstance(error, SourceError) else "attachment_decode_failed"
            return AttachmentRead(metadata)


def selected_image_key(account: Path) -> bytes:
    match = re.fullmatch(r"(wxid_[A-Za-z0-9_]+)_([0-9a-fA-F]{4})", account.name)
    if not match:
        raise SourceError("image_key_required", "No unambiguous image key configuration matches this account.")
    canonical, suffix = match.groups()
    config_root = account.parent.parent / "app_data/radium/ilink"
    directories = [path for path in config_root.glob(suffix + "*") if path.is_dir()]
    if len(directories) != 1:
        raise SourceError("image_key_required", "No unambiguous image key configuration matches this account.")
    config = directories[0] / "kvcomm/config.ini"
    if config.resolve() != config.absolute() or not config.is_file():
        raise SourceError("image_key_required", "No unambiguous image key configuration matches this account.")
    if config.stat().st_size > 64 * 1024:
        raise SourceError("image_key_required", "Image key configuration exceeds the supported size.")
    values = re.findall(r"^\s*last_uin\s*=\s*(\S+)\s*$", config.read_text(), re.MULTILINE)
    if len(values) != 1:
        raise SourceError("image_key_required", "Image key configuration has no unique account identity.")
    try:
        uin = base64.b64decode(values[0], validate=True).decode("ascii")
    except (ValueError, UnicodeDecodeError) as error:
        raise SourceError("image_key_required", "Image key configuration has no supported account identity.") from error
    if not re.fullmatch(r"[0-9]{1,20}", uin):
        raise SourceError("image_key_required", "Image key configuration has no supported account identity.")
    return hashlib.md5((uin + canonical).encode()).hexdigest()[:16].encode("ascii")


def decode_image_cache(data: bytes, account: Path) -> bytes:
    if sniff_mime(data).startswith("image/"):
        verify_image(data)
        return data
    if not data.startswith(b"\x07\x08V2\x08\x07"):
        raise SourceError("image_format_unsupported", "This image cache encoding is unsupported.")
    if len(data) < 15:
        raise SourceError("image_decode_failed", "Image cache header is truncated.")
    aes_size = int.from_bytes(data[6:10], "little")
    xor_size = int.from_bytes(data[10:14], "little")
    encrypted_size = (aes_size // 16 + 1) * 16
    if aes_size > MAX_FILE_BYTES or encrypted_size > len(data) - 15 or xor_size > len(data) - 15 - encrypted_size:
        raise SourceError("image_decode_failed", "Image cache regions overlap or exceed the available bytes.")
    decryptor = Cipher(algorithms.AES(selected_image_key(account)), modes.ECB()).decryptor()
    padded = decryptor.update(data[15 : 15 + encrypted_size]) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    try:
        prefix = unpadder.update(padded) + unpadder.finalize()
    except ValueError as error:
        raise SourceError("image_decode_failed", "Image cache padding is invalid for the selected account.") from error
    if len(prefix) != aes_size:
        raise SourceError("image_decode_failed", "Image cache plaintext length does not match its header.")
    middle_end = len(data) - xor_size
    middle = data[15 + encrypted_size : middle_end]
    tail = data[middle_end:]
    if tail:
        if not prefix.startswith(b"\xff\xd8\xff") or xor_size < 2 or (tail[-2] ^ 0xFF) != (tail[-1] ^ 0xD9):
            raise SourceError("image_format_unsupported", "Image cache has no verified supported XOR footer.")
        xor_key = tail[-1] ^ 0xD9
        tail = bytes(value ^ xor_key for value in tail)
    decoded = prefix + middle + tail
    verify_image(decoded)
    return decoded


def sniff_mime(data: bytes) -> str:
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    for signature, mime in (
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
    ):
        if data.startswith(signature):
            return mime
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"PK\x03\x04"):
        return "application/zip"
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "application/x-ole-storage"
    return "application/octet-stream"


def verify_image(data: bytes) -> None:
    if not sniff_mime(data).startswith("image/"):
        raise SourceError("image_format_unsupported", "Decoded data has no supported image signature.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(BytesIO(data)) as image:
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise SourceError("image_too_large", "Image exceeds 25 million pixels.")
            image.verify()
        with Image.open(BytesIO(data)) as image:
            image.load()


def extract_in_worker(data: bytes) -> dict[str, Any]:
    try:
        process = subprocess.run(
            [sys.executable, "-m", "anymessage.attachments"],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=EXTRACT_TIMEOUT,
        )
        if process.returncode:
            return {"state": "extraction_failed"}
        return json.loads(process.stdout)
    except subprocess.TimeoutExpired:
        return {"state": "extraction_timeout"}


def extract_document(data: bytes) -> dict[str, Any]:
    mime = sniff_mime(data)
    chunks: list[str] = []
    truncated = False
    length = 0

    def emit(value: str, separator: str = "\n") -> bool:
        nonlocal length, truncated
        prefix = separator if chunks else ""
        remaining = MAX_TEXT_CHARS - length
        truncated |= len(prefix) + len(value) > remaining
        fragment = prefix[:remaining]
        remaining -= len(fragment)
        fragment += value[:remaining]
        chunks.append(fragment)
        length += len(fragment)
        return not truncated

    if mime == "application/pdf":
        from pypdf import PdfReader, overwrite_configuration

        overwrite_configuration(
            zlib_maximum_output_length=MAX_ZIP_BYTES,
            maximum_declared_stream_length=MAX_ZIP_BYTES,
            array_based_stream_maximum_output_length=MAX_ZIP_BYTES,
            lzw_maximum_output_length=MAX_ZIP_BYTES,
            run_length_maximum_output_length=MAX_ZIP_BYTES,
            brotli_maximum_output_length=MAX_ZIP_BYTES,
            jbig2_maximum_output_length=MAX_ZIP_BYTES,
            image_maximum_buffer_size=MAX_ZIP_BYTES,
            stream_decoding_work_maximum_length=MAX_ZIP_BYTES * 4,
        )
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted:
            return {"state": "encrypted_document"}
        count = len(reader.pages)
        for index in range(min(count, MAX_PDF_PAGES)):
            page = reader.pages[index]
            stream = page.get_contents()
            if stream is not None and len(stream.get_data()) > MAX_XML_BYTES:
                truncated = True
                break
            text = page.extract_text() or ""
            if not emit(text):
                break
            if length >= MAX_TEXT_CHARS and index + 1 < count:
                truncated = True
                break
        truncated |= count > MAX_PDF_PAGES
    elif mime == "application/zip":
        with zipfile.ZipFile(BytesIO(data)) as archive:
            infos = archive.infolist()
            if (
                len(infos) > MAX_ZIP_PARTS
                or sum(item.file_size for item in infos) > MAX_ZIP_BYTES
                or any(item.file_size > MAX_XML_BYTES and item.filename.endswith(".xml") for item in infos)
            ):
                return {"state": "extraction_limit"}
            if any(item.flag_bits & 1 for item in infos):
                return {"state": "encrypted_document"}
            names = [item.filename for item in infos]
            if len(names) != len(set(names)):
                return {"state": "document_invalid"}
            if "[Content_Types].xml" not in names:
                return {"state": "format_unsupported"}
            content_types = xml_document(archive.read("[Content_Types].xml"))
            types = {node.attrib.get("PartName"): node.attrib.get("ContentType", "") for node in content_types}
            if (
                types.get("/word/document.xml")
                == "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
            ):
                document = xml_document(archive.read("word/document.xml"))
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                for paragraph in document.findall(".//w:p", ns):
                    if not emit(""):
                        break
                    for node in paragraph.findall(".//w:t", ns):
                        if not emit(node.text or "", ""):
                            break
                    if truncated:
                        break
            elif (
                types.get("/xl/workbook.xml")
                == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
            ):
                ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                strings = []
                if "xl/sharedStrings.xml" in names:
                    shared = xml_document(archive.read("xl/sharedStrings.xml"))
                    strings = ["".join(node.text or "" for node in item.findall(".//s:t", ns)) for item in shared]
                workbook = xml_document(archive.read("xl/workbook.xml"))
                relationships = xml_document(archive.read("xl/_rels/workbook.xml.rels"))
                relation_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
                sheets = {
                    node.attrib.get("Id"): node
                    for node in relationships
                    if node.attrib.get("Type") == relation_ns + "/worksheet"
                }
                for entry in workbook.findall("s:sheets/s:sheet", ns):
                    relationship = sheets[entry.attrib["{" + relation_ns + "}id"]]
                    if relationship.attrib.get("TargetMode", "Internal") != "Internal":
                        return {"state": "document_invalid"}
                    target = relationship.attrib["Target"]
                    name = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "xl/" + target)
                    if not name.startswith("xl/worksheets/") or name not in names:
                        return {"state": "document_invalid"}
                    if not emit("[" + entry.attrib["name"] + "]"):
                        break
                    sheet = xml_document(archive.read(name))
                    for row in sheet.findall(".//s:sheetData/s:row", ns):
                        if not emit(""):
                            break
                        for index, cell in enumerate(row.findall("s:c", ns)):
                            if not emit(cell.attrib.get("r", "?") + "=", "\t" if index else ""):
                                break
                            value = cell.findtext("s:v", "", ns)
                            if cell.attrib.get("t") == "s":
                                value = strings[int(value)]
                            elif cell.attrib.get("t") == "inlineStr":
                                value = "".join(node.text or "" for node in cell.findall(".//s:t", ns))
                            if not emit(value, ""):
                                break
                        if truncated:
                            break
                    if truncated:
                        break
            elif (
                types.get("/ppt/presentation.xml")
                == "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
            ):
                presentation = xml_document(archive.read("ppt/presentation.xml"))
                relationships = xml_document(archive.read("ppt/_rels/presentation.xml.rels"))
                ns = {
                    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
                    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
                }
                relation_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
                slides = {
                    node.attrib.get("Id"): node
                    for node in relationships
                    if node.attrib.get("Type") == relation_ns + "/slide"
                }
                for index, slide_id in enumerate(presentation.findall("p:sldIdLst/p:sldId", ns), 1):
                    relationship = slides[slide_id.attrib["{" + relation_ns + "}id"]]
                    if relationship.attrib.get("TargetMode", "Internal") != "Internal":
                        return {"state": "document_invalid"}
                    target = relationship.attrib["Target"]
                    target = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "ppt/" + target)
                    if not target.startswith("ppt/slides/") or target not in names:
                        return {"state": "document_invalid"}
                    slide = xml_document(archive.read(target))
                    if not emit("[Slide " + str(index) + "]"):
                        break
                    for paragraph in slide.findall(".//a:p", ns):
                        if not emit(""):
                            break
                        for node in paragraph.findall(".//a:t", ns):
                            if not emit(node.text or "", ""):
                                break
                        if truncated:
                            break
                    if truncated:
                        break
            else:
                return {"state": "format_unsupported"}
    elif mime in ("application/octet-stream",):
        encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            return {"state": "format_unsupported"}
        if any(ord(char) < 32 and char not in "\n\r\t\f" for char in text):
            return {"state": "format_unsupported"}
        emit(text)
    else:
        return {"state": "format_unsupported"}
    text = "".join(chunks)
    return {"state": "ready" if text.strip() else "no_extractable_text", "text": text, "truncated": truncated}


if __name__ == "__main__":
    try:
        payload = sys.stdin.buffer.read(MAX_FILE_BYTES + 1)
        result = extract_document(payload) if len(payload) <= MAX_FILE_BYTES else {"state": "attachment_too_large"}
    except Exception:
        # Parser diagnostics can include document content. Return only a fixed state.
        result = {"state": "document_invalid"}
    sys.stdout.write(json.dumps(result))
