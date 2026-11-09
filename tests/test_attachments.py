from __future__ import annotations

import base64
import hashlib
from io import BytesIO
import zipfile
from datetime import datetime

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from PIL import Image
import pytest
import zstandard

from anymessage.attachments import (
    Attachments,
    MAX_FILE_BYTES,
    MAX_TEXT_CHARS,
    decode_image_cache,
    extract_document,
    extract_in_worker,
    message_attachments,
    selected_image_key,
)
from anymessage.wechat import SourceError
from conftest import TABLE, USER, create_database


def message_xml(name: str, data: bytes) -> bytes:
    return f"<msg><appmsg><title>{name}</title><type>6</type><md5>{hashlib.md5(data).hexdigest()}</md5><appattach><totallen>{len(data)}</totallen></appattach></appmsg></msg>".encode()


def add_file(source, data=b"attachment text\nsecond line", name="fixture.txt"):
    reader, root, _, keys = source
    body = message_xml(name, data)
    key = keys.get("message/message_0.db", {}).get("enc_key")
    create_database(
        root / "message/message_0.db",
        f"INSERT INTO \"{TABLE}\" VALUES(10,1001,25769803825,10,1,1704067200,X'{body.hex()}',0,NULL);",
        key,
    )
    digest = hashlib.md5(data).hexdigest()
    create_database(
        root / "hardlink/hardlink.db",
        f"CREATE TABLE dir2id(username TEXT); INSERT INTO dir2id VALUES('2024-01'); CREATE TABLE file_hardlink_info_v4(md5 TEXT,file_name TEXT,file_size INTEGER,dir1 INTEGER,dir2 INTEGER); INSERT INTO file_hardlink_info_v4 VALUES('{digest}','{name}',{len(data)},1,0);",
        None,
    )
    path = root.parent / "msg/file/2024-01" / name
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    return reader, path, "message/message_0.db:10"


def image_bytes():
    output = BytesIO()
    Image.new("RGB", (4, 3), (10, 50, 200)).save(output, "JPEG")
    return output.getvalue()


def image_config(tmp_path):
    account = tmp_path / "xwechat_files/wxid_fixture_abcd"
    account.mkdir(parents=True)
    config = tmp_path / "app_data/radium/ilink/abcd-fixture/kvcomm/config.ini"
    config.parent.mkdir(parents=True)
    config.write_text("[account]\nlast_uin=" + base64.b64encode(b"123456").decode() + "\n")
    return account, config


def encode_v2(data, key, aes_size=32, xor_size=10):
    padder = padding.PKCS7(128).padder()
    padded = padder.update(data[:aes_size]) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return (
        b"\x07\x08V2\x08\x07"
        + aes_size.to_bytes(4, "little")
        + xor_size.to_bytes(4, "little")
        + b"\0"
        + encrypted
        + data[aes_size:-xor_size]
        + bytes(value ^ 0x37 for value in data[-xor_size:])
    )


def test_metadata_compressed_group_and_forwarded():
    data = b"content"
    raw = b"sender_fixture:\n" + message_xml("fixture.txt", data)
    row = {
        "local_type": 25769803825,
        "server_id": 1001,
        "body_hex": b"wrong".hex(),
        "compressed_hex": zstandard.ZstdCompressor().compress(raw).hex(),
    }
    refs, state = message_attachments(row, "message/message_0.db:10")
    assert state == "metadata" and len(refs) == 1
    assert refs[0].md5 == hashlib.md5(data).hexdigest()
    assert refs[0].size == len(data)
    row["compressed_hex"] = (
        b"<msg><appmsg><type>19</type><recorditem>" + message_xml("fixture.txt", data) + b"</recorditem></appmsg></msg>"
    ).hex()
    assert message_attachments(row, "x") == ([], "unsupported_type")
    row["compressed_hex"] = b'<!DOCTYPE msg [<!ENTITY x "oops">]><msg/>'.hex()
    assert message_attachments(row, "x") == ([], "metadata_invalid")


def test_file_read_and_pagination(source):
    reader, path, message_id = add_file(source)
    message = reader.get_messages(USER)["items"][0]
    assert message["id"] == message_id
    assert message["text"] is None and message["content_state"] == "unsupported_type"
    assert message["attachments"][0]["state"] == "not_checked"
    listing = reader.list_attachments(USER, message_id)
    item = listing["items"][0]
    assert item["state"] == "available" and item["content_verified"] is False
    result = reader.read_attachment(USER, message_id, item["id"], limit=10)
    assert result.text == "attachment" and result.metadata["next_offset"] == 10
    result = reader.read_attachment(USER, message_id, item["id"], offset=10)
    assert result.text == " text\nsecond line" and result.metadata["next_offset"] is None
    result = reader.read_attachment(USER, message_id, item["id"], as_binary=True)
    assert result.data == path.read_bytes()
    path.write_bytes(b"tampered")
    assert reader.read_attachment(USER, message_id, item["id"]).metadata["state"] == "attachment_integrity_failed"


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_ansi_text_pagination_preserves_original_bytes(source, encoding):
    data = "\x1b[32m成功 😀\x1b[0m\r\n\x1b[1;31m次の行\x1b[0m".encode(encoding)
    expected = "成功 😀\r\n次の行"
    reader, _, message_id = add_file(source, data, "color.log")
    item = reader.list_attachments(USER, message_id)["items"][0]
    first = reader.read_attachment(USER, message_id, item["id"], limit=4)
    assert first.metadata["state"] == "ready"
    assert first.metadata["content_verified"] is True
    assert first.metadata["text_normalizations"] == ["ansi_csi_removed"]
    assert first.metadata["total_characters"] == len(expected)
    assert first.text == "成功 😀"
    assert first.metadata["next_offset"] == 4
    rest = reader.read_attachment(USER, message_id, item["id"], offset=first.metadata["next_offset"])
    assert rest.text == "\r\n次の行"
    assert rest.metadata["next_offset"] is None
    binary = reader.read_attachment(USER, message_id, item["id"], as_binary=True)
    assert binary.data == data
    assert "text_normalizations" not in binary.metadata


@pytest.mark.parametrize("control", ["\x1b", "\x1b[31", "\x1b]title\x07", "\x00", "\x01"])
def test_unknown_text_controls_remain_unsupported(control):
    result = extract_in_worker(("\x1b[32mcolored\x1b[0m" + control).encode())
    assert result["state"] == "format_unsupported"


def test_metadata_never_looks_up_cache(source, monkeypatch):
    reader, _, message_id = add_file(source)
    monkeypatch.setattr(Attachments, "path_for", lambda *_: pytest.fail("history resolved files"))
    assert reader.get_messages(USER)["items"][0]["attachments"]


def test_message_bound_ids(source):
    reader, _, message_id = add_file(source)
    item = reader.list_attachments(USER, message_id)["items"][0]
    with pytest.raises(SourceError, match="message_not_found"):
        reader.list_attachments("different_conversation", message_id)
    with pytest.raises(SourceError, match="message_id_invalid"):
        reader.list_attachments(USER, "../../secret:10")
    with pytest.raises(SourceError, match="attachment_not_found"):
        reader.read_attachment(USER, message_id, "a" * 32)
    assert reader.get_messages(USER)["items"][0]["attachments"][0]["id"] == item["id"]


def test_no_filename_only_lookup_and_ambiguity(source):
    reader, path, message_id = add_file(source)
    item = reader.list_attachments(USER, message_id)["items"][0]
    database = source[1] / "hardlink/hardlink.db"
    create_database(database, "UPDATE file_hardlink_info_v4 SET md5='" + "0" * 32 + "';", None)
    assert reader.list_attachments(USER, message_id)["items"][0]["state"] == "attachment_missing"
    assert reader.read_attachment(USER, message_id, item["id"]).data is None
    digest = hashlib.md5(path.read_bytes()).hexdigest()
    create_database(
        database,
        f"UPDATE file_hardlink_info_v4 SET md5='{digest}'; INSERT INTO dir2id VALUES('2024-02'); INSERT INTO file_hardlink_info_v4 SELECT md5,file_name,file_size,2,0 FROM file_hardlink_info_v4;",
        None,
    )
    assert reader.list_attachments(USER, message_id)["items"][0]["state"] == "attachment_ambiguous"


@pytest.mark.parametrize("mutation", ["missing_db", "locked_db", "missing_file", "outside_path", "symlink"])
def test_cache_failure_states(source, tmp_path, mutation):
    reader, path, message_id = add_file(source)
    expected = {
        "missing_db": "database_missing",
        "locked_db": "key_missing",
        "missing_file": "attachment_missing",
        "outside_path": "attachment_path_invalid",
        "symlink": "attachment_path_invalid",
    }[mutation]
    database = source[1] / "hardlink/hardlink.db"
    if mutation == "missing_db":
        database.unlink()
    elif mutation == "locked_db":
        database.write_bytes(b"encrypted fixture")
    elif mutation == "missing_file":
        path.unlink()
    elif mutation == "outside_path":
        create_database(database, "UPDATE dir2id SET username='../outside';", None)
    else:
        outside = tmp_path / "outside.txt"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
    assert reader.list_attachments(USER, message_id)["items"][0]["state"] == expected


def test_image_variants_and_decode(source):
    reader, root, _, keys = source
    digest = "a" * 32
    packed = b"\x1a\x22\x22\x20" + digest.encode()
    key = keys.get("message/message_0.db", {}).get("enc_key")
    create_database(
        root / "message/message_0.db",
        f'ALTER TABLE "{TABLE}" ADD COLUMN packed_info_data BLOB; UPDATE "{TABLE}" SET packed_info_data=X\'{packed.hex()}\' WHERE local_id=3;',
        key,
    )
    folder = (
        root.parent
        / "msg/attach"
        / hashlib.md5(USER.encode()).hexdigest()
        / datetime.fromtimestamp(101).strftime("%Y-%m")
        / "Img"
    )
    folder.mkdir(parents=True)
    (folder / (digest + "_t.dat")).write_bytes(image_bytes())
    items = reader.list_attachments(USER, "message/message_0.db:3")["items"]
    assert [item["variant"] for item in items] == ["original", "hd", "thumbnail"]
    assert [item["state"] for item in items] == ["attachment_missing", "attachment_missing", "available"]
    result = reader.read_attachment(USER, "message/message_0.db:3", items[2]["id"])
    assert result.image and result.data == image_bytes() and result.mime_type == "image/jpeg"
    assert (
        reader.read_attachment(USER, "message/message_0.db:3", items[0]["id"]).metadata["state"] == "attachment_missing"
    )


def test_image_key_selection_v2_and_padding(tmp_path):
    account, config = image_config(tmp_path)
    key = hashlib.md5(b"123456wxid_fixture").hexdigest()[:16].encode()
    assert selected_image_key(account) == key
    data = image_bytes()
    encoded = encode_v2(data, key)
    assert decode_image_cache(encoded, account) == data
    assert decode_image_cache(data, account) == data
    with pytest.raises(SourceError, match="image_decode_failed"):
        decode_image_cache(encoded[:15] + b"\0" * 48 + encoded[63:], account)
    with pytest.raises(SourceError, match="image_format_unsupported"):
        decode_image_cache(b"wxgf" + b"a" * 100, account)
    config.unlink()
    with pytest.raises(SourceError, match="image_key_required"):
        decode_image_cache(encoded, account)
    other = config.parent.parent.parent / "ffff-other/kvcomm/config.ini"
    other.parent.mkdir(parents=True)
    other.write_text("last_uin=MTIzNDU2")
    with pytest.raises(SourceError, match="image_key_required"):
        selected_image_key(account)
    config.write_text("last_uin=MTIzNDU2")
    duplicate = config.parent.parent.parent / "abcd-other/kvcomm/config.ini"
    duplicate.parent.mkdir(parents=True)
    duplicate.write_text(config.read_text())
    with pytest.raises(SourceError, match="image_key_required"):
        selected_image_key(account)


def office_zip(parts):
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in parts.items():
            archive.writestr(name, text)
    return output.getvalue()


def document_bytes(kind):
    if kind == "docx":
        return office_zip(
            {
                "[Content_Types].xml": '<Types><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
                "word/document.xml": '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Document fixture</w:t></w:r></w:p></w:body></w:document>',
            }
        )
    if kind == "xlsx":
        return office_zip(
            {
                "[Content_Types].xml": '<Types><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>',
                "xl/workbook.xml": '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sales" r:id="sheet1"/></sheets></workbook>',
                "xl/_rels/workbook.xml.rels": '<Relationships><Relationship Id="sheet1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
                "xl/sharedStrings.xml": '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>Sheet fixture</t></si></sst>',
                "xl/worksheets/sheet1.xml": '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c r="A1" t="s"><v>0</v></c><c r="B1"><f>1+1</f><v>2</v></c></row></sheetData></worksheet>',
            }
        )
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject, DictionaryObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=200)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 100 Td (PDF fixture) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize(
    "kind,expected", [("docx", "Document fixture"), ("xlsx", "A1=Sheet fixture\tB1=2"), ("pdf", "PDF fixture")]
)
def test_common_documents_end_to_end(source, kind, expected):
    reader, _, message_id = add_file(source, document_bytes(kind), "misleading.bin")
    item = reader.list_attachments(USER, message_id)["items"][0]
    result = reader.read_attachment(USER, message_id, item["id"])
    assert result.metadata["state"] == "ready" and expected in result.text


def test_text_limits_and_unsupported_formats():
    for data in [b"plain\ntext", "日本語,CSV\n列,値".encode("utf-16"), b"\xef\xbb\xbfBOM"]:
        assert extract_in_worker(data)["state"] == "ready"
    result = extract_document(b"a" * (MAX_TEXT_CHARS + 1))
    assert result["truncated"] and len(result["text"]) == MAX_TEXT_CHARS
    assert extract_in_worker(b"\x00\xff")["state"] == "format_unsupported"
    assert extract_in_worker(office_zip({"test.txt": "ordinary zip"}))["state"] == "format_unsupported"
    assert extract_in_worker(b"%PDF-invalid")["state"] == "document_invalid"
    assert extract_in_worker(b"<not a file>")["state"] == "ready"


def test_encrypted_pdf_and_no_ocr():
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    output = BytesIO()
    writer.write(output)
    assert extract_in_worker(output.getvalue())["state"] == "no_extractable_text"
    writer.encrypt("fixture-password")
    output = BytesIO()
    writer.write(output)
    assert extract_in_worker(output.getvalue())["state"] == "encrypted_document"


def test_oversized_and_binary_offsets(source):
    reader, path, message_id = add_file(source)
    item = reader.list_attachments(USER, message_id)["items"][0]
    path.write_bytes(b"a" * (MAX_FILE_BYTES + 1))
    assert reader.read_attachment(USER, message_id, item["id"]).metadata["state"] == "attachment_too_large"


def test_nested_digest_and_declared_zstd_bound():
    from anymessage.attachments import MAX_XML_BYTES

    correct = hashlib.md5(b"fixture").hexdigest()
    raw = f"<msg><appmsg><title>fixture.txt</title><type>6</type><md5>{'0' * 32}</md5><appattach><md5>{correct}</md5><totallen>7</totallen></appattach></appmsg></msg>".encode()
    row = {"local_type": 49, "server_id": 1, "body_hex": raw.hex()}
    assert message_attachments(row, "fixture")[0][0].md5 == correct
    row["compressed_hex"] = zstandard.ZstdCompressor(write_content_size=True).compress(b"a" * (MAX_XML_BYTES + 1)).hex()
    assert message_attachments(row, "fixture") == ([], "metadata_invalid")


def test_ordered_pptx_and_external_relationship():
    parts = {
        "[Content_Types].xml": '<Types><Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/></Types>',
        "ppt/presentation.xml": '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId r:id="second"/><p:sldId r:id="first"/></p:sldIdLst></p:presentation>',
        "ppt/_rels/presentation.xml.rels": '<Relationships><Relationship Id="first" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/><Relationship Id="second" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide2.xml"/></Relationships>',
        "ppt/slides/slide1.xml": '<sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:p><a:r><a:t>First named slide</a:t></a:r></a:p></sld>',
        "ppt/slides/slide2.xml": '<sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:p><a:r><a:t>Second named slide</a:t></a:r></a:p></sld>',
    }
    result = extract_in_worker(office_zip(parts))
    assert result["state"] == "ready"
    assert result["text"].index("Second named slide") < result["text"].index("First named slide")
    parts["ppt/_rels/presentation.xml.rels"] = parts["ppt/_rels/presentation.xml.rels"].replace(
        'Target="slides/', 'Target="/ppt/slides/'
    )
    assert extract_in_worker(office_zip(parts))["text"] == result["text"]
    parts["ppt/_rels/presentation.xml.rels"] = parts["ppt/_rels/presentation.xml.rels"].replace(
        'Id="second"', 'Id="second" TargetMode="External"'
    )
    assert extract_in_worker(office_zip(parts))["state"] == "document_invalid"


def test_image_resource_fallback_is_message_scoped(source):
    reader, root, _, _ = source
    digest = "b" * 32
    packed = b"\x12\x22\x0a\x20" + digest.encode()
    relative = "message/message_resource.db"
    create_database(
        root / relative,
        f"CREATE TABLE ChatName2Id(user_name TEXT); INSERT INTO ChatName2Id VALUES('{USER}'); CREATE TABLE MessageResourceInfo(chat_id INTEGER,message_local_id INTEGER,message_svr_id INTEGER,packed_info BLOB); INSERT INTO MessageResourceInfo VALUES(1,3,103,X'{packed.hex()}');",
        None,
    )
    folder = (
        root.parent
        / "msg/attach"
        / hashlib.md5(USER.encode()).hexdigest()
        / datetime.fromtimestamp(101).strftime("%Y-%m")
        / "Img"
    )
    folder.mkdir(parents=True)
    (folder / (digest + "_t.dat")).write_bytes(image_bytes())
    items = reader.list_attachments(USER, "message/message_0.db:3")["items"]
    assert items[-1]["state"] == "available"
    assert reader.read_attachment(USER, "message/message_0.db:3", items[-1]["id"]).image
    create_database(root / relative, "UPDATE MessageResourceInfo SET message_svr_id=999;", None)
    assert reader.list_attachments(USER, "message/message_0.db:3")["items"][-1]["state"] == "attachment_missing"


def test_xlsx_workbook_order_and_names():
    data = document_bytes("xlsx")
    with zipfile.ZipFile(BytesIO(data)) as archive:
        parts = {name: archive.read(name).decode() for name in archive.namelist()}
    parts["xl/workbook.xml"] = parts["xl/workbook.xml"].replace(
        '<sheet name="Sales" r:id="sheet1"/>',
        '<sheet name="Inventory" r:id="sheet10"/><sheet name="Sales" r:id="sheet1"/>',
    )
    parts["xl/_rels/workbook.xml.rels"] = parts["xl/_rels/workbook.xml.rels"].replace(
        "</Relationships>",
        '<Relationship Id="sheet10" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet10.xml"/></Relationships>',
    )
    parts["xl/worksheets/sheet10.xml"] = parts["xl/worksheets/sheet1.xml"].replace("<v>2</v>", "<v>10</v>")
    result = extract_in_worker(office_zip(parts))
    assert result["state"] == "ready"
    assert result["text"].index("[Inventory]") < result["text"].index("[Sales]")
    assert "B1=10" in result["text"]
    parts["xl/_rels/workbook.xml.rels"] = parts["xl/_rels/workbook.xml.rels"].replace(
        'Target="worksheets/', 'Target="/xl/worksheets/'
    )
    assert extract_in_worker(office_zip(parts))["text"] == result["text"]


def test_zip_xml_limits_and_timeout(monkeypatch):
    import anymessage.attachments as attachments

    oversized = office_zip({"part.xml": "a" * (attachments.MAX_XML_BYTES + 1)})
    assert extract_in_worker(oversized)["state"] == "extraction_limit"
    monkeypatch.setattr(attachments, "EXTRACT_TIMEOUT", 0.001)
    assert extract_in_worker(b"text")["state"] == "extraction_timeout"


def test_binary_limit_and_unsupported_original_bytes(source):
    reader, path, message_id = add_file(source, b"\x00\xffopaque\x00", "fixture.bin")
    item = reader.list_attachments(USER, message_id)["items"][0]
    assert reader.read_attachment(USER, message_id, item["id"]).metadata["state"] == "format_unsupported"
    result = reader.read_attachment(USER, message_id, item["id"], as_binary=True)
    assert result.data == path.read_bytes() and result.metadata["representation"] == "original_bytes"
    assert (
        reader.read_attachment(USER, message_id, item["id"], offset=1, as_binary=True).metadata["state"]
        == "offset_invalid"
    )


def test_binary_byte_limit(source):
    from anymessage.attachments import MAX_BINARY_BYTES

    reader, _, message_id = add_file(source, b"x" * (MAX_BINARY_BYTES + 1), "fixture.bin")
    item = reader.list_attachments(USER, message_id)["items"][0]
    assert (
        reader.read_attachment(USER, message_id, item["id"], as_binary=True).metadata["state"] == "attachment_too_large"
    )


def test_image_error_does_not_claim_content_verification(source):
    reader, root, _, keys = source
    digest = "a" * 32
    packed = b"\x1a\x22\x22\x20" + digest.encode()
    key = keys.get("message/message_0.db", {}).get("enc_key")
    create_database(
        root / "message/message_0.db",
        f'ALTER TABLE "{TABLE}" ADD COLUMN packed_info_data BLOB; UPDATE "{TABLE}" SET packed_info_data=X\'{packed.hex()}\' WHERE local_id=3;',
        key,
    )
    folder = (
        root.parent
        / "msg/attach"
        / hashlib.md5(USER.encode()).hexdigest()
        / datetime.fromtimestamp(101).strftime("%Y-%m")
        / "Img"
    )
    folder.mkdir(parents=True)
    (folder / (digest + "_t.dat")).write_bytes(encode_v2(image_bytes(), b"0123456789abcdef"))
    item = reader.list_attachments(USER, "message/message_0.db:3")["items"][-1]
    result = reader.read_attachment(USER, "message/message_0.db:3", item["id"])
    assert result.metadata["state"] == "image_key_required"
    assert result.metadata["content_verified"] is False and result.data is None


def test_shared_string_expansion_stops_at_output_cap():
    with zipfile.ZipFile(BytesIO(document_bytes("xlsx"))) as archive:
        parts = {name: archive.read(name).decode() for name in archive.namelist()}
    parts["xl/sharedStrings.xml"] = (
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>'
        + "x" * 32768
        + "</t></si></sst>"
    )
    cells = "".join(f'<c r="A{index}" t="s"><v>0</v></c>' for index in range(1, 71))
    # The unvisited suffix must not be expanded or interpreted after the output cap.
    cells += '<c r="A71" t="s"><v>999</v></c>'
    parts["xl/worksheets/sheet1.xml"] = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row>'
        + cells
        + "</row></sheetData></worksheet>"
    )
    result = extract_in_worker(office_zip(parts))
    assert result["state"] == "ready" and result["truncated"]
    assert len(result["text"]) == MAX_TEXT_CHARS
    assert result["text"].startswith("[Sales]\nA1=" + "x" * 32768 + "\tA2=")
    assert "A70=" not in result["text"]


@pytest.mark.parametrize("destination", ["thumbnail", "other_conversation"])
def test_image_locator_rejects_symlink_redirects(source, destination):
    reader, root, _, keys = source
    digest = "a" * 32
    packed = b"\x1a\x22\x22\x20" + digest.encode()
    key = keys.get("message/message_0.db", {}).get("enc_key")
    create_database(
        root / "message/message_0.db",
        f'ALTER TABLE "{TABLE}" ADD COLUMN packed_info_data BLOB; UPDATE "{TABLE}" SET packed_info_data=X\'{packed.hex()}\' WHERE local_id=3;',
        key,
    )
    folder = (
        root.parent
        / "msg/attach"
        / hashlib.md5(USER.encode()).hexdigest()
        / datetime.fromtimestamp(101).strftime("%Y-%m")
        / "Img"
    )
    folder.mkdir(parents=True)
    target = (
        folder / (digest + "_t.dat")
        if destination == "thumbnail"
        else folder.parent.parent.parent / "other_conversation" / "image.dat"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(image_bytes())
    (folder / (digest + ".dat")).symlink_to(target)
    item = reader.list_attachments(USER, "message/message_0.db:3")["items"][0]
    assert item["state"] == "attachment_path_invalid"
    result = reader.read_attachment(USER, "message/message_0.db:3", item["id"])
    assert result.metadata["state"] == "attachment_path_invalid" and result.data is None


@pytest.mark.parametrize("redirect", ["file", "directory"])
def test_image_key_configuration_rejects_other_account_symlinks(tmp_path, redirect):
    account, config = image_config(tmp_path)
    other = config.parent.parent.parent / "ffff-other" / "kvcomm/config.ini"
    other.parent.mkdir(parents=True)
    other.write_text(config.read_text())
    if redirect == "file":
        config.unlink()
        config.symlink_to(other)
    else:
        directory = config.parent.parent
        directory.rename(tmp_path / "detached-selected-config")
        directory.symlink_to(other.parent.parent, target_is_directory=True)
    with pytest.raises(SourceError, match="image_key_required"):
        selected_image_key(account)


def test_duplicate_index_rows_do_not_hide_ambiguity(source):
    reader, _, message_id = add_file(source)
    create_database(
        source[1] / "hardlink/hardlink.db",
        "INSERT INTO file_hardlink_info_v4 SELECT * FROM file_hardlink_info_v4; INSERT INTO file_hardlink_info_v4 SELECT * FROM file_hardlink_info_v4 LIMIT 1; INSERT INTO dir2id VALUES('2024-02'); INSERT INTO file_hardlink_info_v4 SELECT md5,file_name,file_size,2,0 FROM file_hardlink_info_v4 LIMIT 1;",
        None,
    )
    assert reader.list_attachments(USER, message_id)["items"][0]["state"] == "attachment_ambiguous"
