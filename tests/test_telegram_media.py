from __future__ import annotations

import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import struct
import zlib

from PIL import Image
import pytest

from anymessage.attachments import MAX_BINARY_BYTES, MAX_FILE_BYTES
from anymessage.errors import SourceError
from anymessage.telegram_export import TelegramExport
from test_attachments import document_bytes, office_zip
from test_telegram_export import GROUP, PERSON, chat, load, message


def selected(reader, number="1", variant="file"):
    return next(ref for ref in reader.list_attachments(GROUP, number)["items"] if ref["variant"] == variant)


def read(reader, number="1", variant="file", **arguments):
    return reader.read_attachment(GROUP, number, selected(reader, number, variant)["id"], **arguments)


def png():
    output = BytesIO()
    Image.new("RGB", (2, 3), "blue").save(output, format="PNG")
    return output.getvalue()


def test_exact_identity_snapshot_variants_and_current_files(tmp_path, monkeypatch):
    (tmp_path / "spec.txt").write_text("Approved 你好 spec", encoding="utf-8")
    (tmp_path / "thumb.png").write_bytes(png())
    value = {"chats": {"list": [chat([message(1, text="proposal", file="spec.txt", thumbnail="thumb.png")]), chat([message(1, file="other.txt")], type="personal_chat")]}}
    reader = load(tmp_path, value)
    original_lstat = Path.lstat
    def no_media_stat(path, *args, **kwargs):
        assert path.name not in ("spec.txt", "thumb.png")
        return original_lstat(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "lstat", no_media_stat)
        hit = reader.search_messages("proposal", GROUP)["items"][0]
        history = reader.get_messages(GROUP)["items"][0]
        context = reader.get_message_context(GROUP, "1")["items"][0]
        assert hit["attachments"] == history["attachments"] == context["attachments"]
    file, thumbnail = hit["attachments"]
    assert file["state"] == thumbnail["state"] == "not_checked"
    assert file["id"] != thumbnail["id"]
    snapshot = hashlib.sha256((tmp_path / "export.json").read_bytes()).hexdigest()
    identity = ["telegram-export-attachment-v1", snapshot, GROUP, "1", "file"]
    assert file["id"] == hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:32]
    assert selected(reader)["id"] == selected(TelegramExport(tmp_path / "export.json"))["id"] == file["id"]
    with monkeypatch.context() as patch:
        patch.setattr(Path, "lstat", lambda *a, **k: pytest.fail("Unknown ID touched media"))
        for pair in ((PERSON, "1"), (GROUP, "1")):
            with pytest.raises(SourceError, match="attachment_not_found"):
                reader.read_attachment(*pair, file["id"] if pair[0] == PERSON else "a" * 32)
    first = read(reader, limit=10)
    second = read(reader, offset=first.metadata["next_offset"])
    assert first.text + second.text == "Approved 你好 spec"
    assert first.metadata["total_characters"] == len("Approved 你好 spec")
    assert not first.metadata["content_verified"] and first.metadata["media_consistency"] == "current_local_files"
    assert first.metadata["local_sha256"] == hashlib.sha256((tmp_path / "spec.txt").read_bytes()).hexdigest()
    (tmp_path / "spec.txt").write_text("Changed current bytes")
    changed = read(reader)
    assert changed.text == "Changed current bytes" and changed.metadata["local_sha256"] != first.metadata["local_sha256"]
    assert selected(reader)["id"] == file["id"]
    (tmp_path / "export.json").write_bytes((tmp_path / "export.json").read_bytes() + b" ")
    with pytest.raises(SourceError, match="attachment_not_found"):
        TelegramExport(tmp_path / "export.json").read_attachment(GROUP, "1", file["id"])
    assert read(reader, variant="thumbnail").image


@pytest.mark.parametrize("kind,expected", [("docx", "Document fixture"), ("xlsx", "A1=Sheet fixture\tB1=2"), ("pdf", "PDF fixture"), ("pptx", "Slide fixture")])
def test_supported_documents_and_declared_mime_is_only_metadata(tmp_path, kind, expected):
    data = document_bytes(kind) if kind != "pptx" else office_zip({
        "[Content_Types].xml": '<Types><Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/></Types>',
        "ppt/presentation.xml": '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="1" r:id="s1"/></p:sldIdLst></p:presentation>',
        "ppt/_rels/presentation.xml.rels": '<Relationships><Relationship Id="s1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/></Relationships>',
        "ppt/slides/slide1.xml": '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:p><a:r><a:t>Slide fixture</a:t></a:r></a:p></p:sld>',
    })
    (tmp_path / "file.bin").write_bytes(data)
    reader = load(tmp_path, chat([message(1, file="file.bin", file_size=len(data), mime_type="image/jpeg")]))
    result = read(reader)
    assert result.metadata["state"] == "ready" and expected in result.text
    assert result.metadata["mime_type"] != "image/jpeg" and result.metadata["declared_mime"] == "image/jpeg"
    assert not result.metadata["content_verified"]


def test_independent_omissions_missing_and_opaque_metadata(tmp_path, monkeypatch):
    warnings = ["(File unavailable, please try again later)", "(File not included. Change data exporting settings to download.)", "(File exceeds maximum size. Change data exporting settings to download.)"]
    (tmp_path / "thumb.png").write_bytes(png())
    reader = load(tmp_path, chat([
        *[message(i, file=warning) for i, warning in enumerate(warnings, 1)],
        message(4, file="absent", thumbnail="thumb.png"),
        message(5, rich_message={"blocks": [{"file": "thumb.png"}]}, file="thumb.png"),
        message(6, media_type="video"), message(7, file={"path": "thumb.png"}),
        message(8, file="(File not included: unknown export warning)"), message(9),
        message(10, type="unsupported", file="thumb.png"),
    ]))
    with monkeypatch.context() as patch:
        patch.setattr(Path, "lstat", lambda *a, **k: pytest.fail("Nonlocator probed filesystem"))
        for number, reason in zip(("1", "2", "3"), ("unavailable", "file_type", "file_size")):
            ref = selected(reader, number)
            assert ref["state"] == "not_exported" and ref["omission_reason"] == reason
            assert read(reader, number).metadata["state"] == "not_exported"
        for number in ("5", "6", "10"):
            assert reader.list_attachments(GROUP, number)["items"] == []
        for number in ("7", "8"):
            assert read(reader, number).metadata["state"] == "unsupported"
        assert reader.list_attachments(GROUP, "9")["metadata_state"] == "none"
    assert read(reader, "4").metadata["state"] == "attachment_missing"
    assert read(reader, "4", "thumbnail").image


def test_omitted_original_can_have_available_thumbnail(tmp_path):
    (tmp_path / "thumb.png").write_bytes(png())
    reader = load(tmp_path, chat([message(1, file="(File unavailable, please try again later)", thumbnail="thumb.png")]))
    assert read(reader).metadata["state"] == "not_exported"
    assert read(reader, variant="thumbnail").image


@pytest.mark.parametrize("locator", ["/secret", "../secret", "a/../secret", "a/./secret", "a//secret", "", "https://example.invalid/secret", "file:secret", "C:/secret", "C:\\secret", "//host/secret", "a\x00b", "a\nb", "a\ud800b"])
def test_unsafe_paths_never_probe_or_disclose(tmp_path, monkeypatch, locator):
    reader = load(tmp_path, chat([message(1, file=locator)]))
    monkeypatch.setattr(Path, "lstat", lambda *a, **k: pytest.fail("Unsafe path was probed"))
    result = read(reader)
    assert result.metadata["state"] == "attachment_path_invalid" and result.data is result.text is None
    assert result.metadata["display_name"] == "file"
    assert locator not in json.dumps(result.metadata) if locator else True


def test_symlinks_special_files_and_selected_parent_alias(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("OUTSIDE")
    root = tmp_path / "export"
    root.mkdir()
    (root / "safe").write_text("INSIDE")
    (root / "leaf").symlink_to(outside / "secret")
    (root / "internal").symlink_to(root / "safe")
    (root / "broken").symlink_to(outside / "absent")
    (root / "ancestor").symlink_to(outside, target_is_directory=True)
    (root / "directory").mkdir()
    os.mkfifo(root / "fifo")
    reader = load(root, chat([message(i, file=path) for i, path in enumerate(("leaf", "internal", "broken", "ancestor/secret", "directory", "fifo", "safe/child"), 1)]))
    for number in range(1, 8):
        result = read(reader, str(number))
        assert result.metadata["state"] == "attachment_path_invalid" and result.data is result.text is None
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    assert TelegramExport(alias / "export.json").status()["state"] == "ready"
    (root / "linked.json").symlink_to(root / "export.json")
    with pytest.raises(SourceError, match="export_unreadable"):
        TelegramExport(root / "linked.json")


def test_sizes_images_binary_and_access_failures(tmp_path, monkeypatch):
    (tmp_path / "file").write_bytes(b"hello")
    for size, state in ((0, "attachment_size_mismatch"), (4, "attachment_size_mismatch"), (5, "ready"), (True, "ready"), (-1, "ready"), ("5", "ready")):
        reader = load(tmp_path, chat([message(1, file="file", file_size=size)]))
        result = read(reader)
        assert result.metadata["state"] == state
        assert result.metadata["local_sha256"] == hashlib.sha256(b"hello").hexdigest()
        if state != "ready":
            assert result.data is result.text is None
    reader = load(tmp_path, chat([message(1, file="file"), message(2, photo="file"), message(3, contact_vcard="file")]))
    assert read(reader, "2", "photo").metadata["state"] == "image_format_unsupported"
    assert read(reader, "3", "contact_vcard").text == "hello"
    with monkeypatch.context() as patch:
        original = Path.open
        def denied(path, *args, **kwargs):
            if path.name == "file":
                raise PermissionError("SECRET_PATH")
            return original(path, *args, **kwargs)
        patch.setattr(Path, "open", denied)
        assert read(reader).metadata["state"] == "attachment_access_denied"
    (tmp_path / "file").write_bytes(b"\x00\xff")
    assert read(reader).metadata["state"] == "format_unsupported"
    assert read(reader, as_binary=True).data == b"\x00\xff"
    assert read(reader, as_binary=True, offset=1).metadata["state"] == "offset_invalid"
    (tmp_path / "file").write_bytes(png())
    assert read(reader).image and read(reader).mime_type == "image/png"
    assert not read(reader, as_binary=True).image and read(reader, as_binary=True).data == png()
    assert read(reader, offset=1).metadata["state"] == "offset_invalid"
    (tmp_path / "file").write_bytes(b"\x89PNG\r\n\x1a\ninvalid")
    assert read(reader).metadata["state"] == "attachment_decode_failed"
    (tmp_path / "file").write_bytes(b"x" * (MAX_BINARY_BYTES + 1))
    assert read(reader, as_binary=True).metadata["state"] == "attachment_too_large"
    (tmp_path / "file").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    result = read(reader)
    assert result.metadata["state"] == "attachment_too_large"
    assert "local_sha256" not in result.metadata and "local_size" not in result.metadata


def test_image_output_and_pixel_bounds_precede_decode(tmp_path):
    image = png()
    oversized_header = image[:16] + struct.pack(">II", 5001, 5000) + image[24:29]
    oversized_header += struct.pack(">I", zlib.crc32(oversized_header[12:29])) + image[33:]
    (tmp_path / "photo").write_bytes(oversized_header)
    reader = load(tmp_path, chat([message(1, photo="photo")]))
    assert read(reader, variant="photo").metadata["state"] == "image_too_large"
    (tmp_path / "photo").write_bytes(image + b"x" * MAX_BINARY_BYTES)
    assert read(reader, variant="photo").metadata["state"] == "attachment_too_large"


def test_byte_identical_copy_uses_selected_folder_and_literal_percent_path(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "%2e%2e.txt").write_text("First root")
    (second / "%2e%2e.txt").write_text("Second root")
    reader = load(first, chat([message(1, file="%2e%2e.txt")]))
    (second / "export.json").write_bytes((first / "export.json").read_bytes())
    copied = TelegramExport(second / "export.json")
    assert selected(reader)["id"] == selected(copied)["id"]
    assert read(reader).text == "First root" and read(copied).text == "Second root"
