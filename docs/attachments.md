# Attachment tool reference

All attachment tools use the selected local WeChat account.
They accept message and attachment ids, never arbitrary paths or URLs.
They do not send messages, download content, run OCR, or create a plaintext archive.

## Metadata and availability

`get_messages` adds `attachments` and `attachment_state` to each message.
These descriptors come only from the message row. Their `state` is `not_checked`.
History ordering, cursors, text values, and text-only search semantics are unchanged.
Unsupported message kinds have an empty attachment list.
Direct file app messages and image messages are supported. Nested forwarded attachments, voice, and video are not.

`list_attachments(conversation_id, message_id)` re-reads the exact message under that conversation.
It returns `items` and `metadata_state`.
Each item contains an opaque `id`, message and server identity, `kind`, `display_name`, declared `size` and `md5` for files, image `cache_basename` when present in the row, and `variant`.
Availability is `available` only when the resolved cache file exists.
`cached_size` is the local file size. `content_verified=false` means the bytes have not yet been checked.
A listed attachment can still fail during reading or extraction.

Image variants are `original`, `hd`, and `thumbnail`, with distinct ids and availability states.
A missing original never returns another variant's bytes.
When the image basename comes from the resource index, the row-derived descriptor keeps its stable id and empty `cache_basename`.

## Read content

`read_attachment(conversation_id, message_id, attachment_id, offset=0, limit=20000, as_binary=false)` returns native MCP content plus metadata.
Results for a resolved attachment include serialized metadata as a text block and the same object in `structuredContent`.
Invalid arguments, unavailable message contexts, and stale attachment ids produce ordinary MCP tool errors before an attachment result exists.
Clients that ignore structured results can still see the variant, errors, and continuation.
The content block preceding the metadata is one of:

| Representation | MCP content | Behavior |
| --- | --- | --- |
| Document text | `TextContent` | `offset` and `limit` count Unicode characters. |
| Supported image | `ImageContent` | Verified JPEG, PNG, GIF, or WebP bytes. A thumbnail remains labeled `thumbnail`. |
| `as_binary=true` | `EmbeddedResource` with `BlobResourceContents` | Complete original file bytes, or decoded image bytes for an image-cache attachment. |
| Failure | Metadata text only | `isError=true` and an explicit state. |

Binary and image reads require `offset=0` and return complete bounded bytes.
An embedded resource URI identifies the returned bytes. It is not a separate MCP resource endpoint or a filesystem URL.

Text metadata includes `total_characters`, `offset`, `next_offset`, `truncated`, and `extraction_truncated`.
Use `next_offset` unchanged for the next available text page.
`truncated=true` covers either remaining text pages or an extraction cap.
`extraction_truncated=true` means the parser stopped before all document content was extracted.
A null `next_offset` means no further text is available within the extraction cap.
Offsets beyond the extracted text return empty text and no continuation.

Files are matched through the hardlink index by name, MD5, and size, then actual bytes are checked again.
Image cache locators and image-key configuration paths reject symlink redirects, including redirects to another variant or account.
Image cache identities are scoped to the conversation and message; decoded images must pass format, pixel, and Pillow validation.
`content_verified` describes these checks. It does not prove that a document has extractable text or that its semantic contents are safe.
MIME types come from byte signatures. Office ZIP containers use `application/zip`; unknown files use `application/octet-stream`.
Filenames and extensions do not establish a supported format.

## Supported content

| Content | Text or image view |
| --- | --- |
| PDF | pypdf text from up to 100 pages. Encrypted PDFs report `encrypted_document`. Scans without text report `no_extractable_text`. No OCR. |
| DOCX | Main document paragraphs and table text, in document order. Headers, footers, comments, and embedded objects are not extracted. |
| XLSX | Worksheet names and cells in workbook order. Shared and inline strings and cached cell values are read. Formulas are not evaluated. |
| PPTX | Slide text in presentation order. Notes, images, and embedded objects are not extracted. |
| Text | UTF-8, optional UTF-8 BOM, and BOM-marked UTF-16, including CSV, JSON, and Markdown. Binary control characters are rejected. |
| Images | Raw JPEG, PNG, GIF, WebP, and supported V2 caches. V2 AES regions use existing configuration for the selected account, with strict padding. XOR tails require a validated JPEG footer. |
| Other files | `format_unsupported` for the text view. `as_binary=true` can still return verified original bytes within the binary limit. |

Office parsing requires ZIP and matching content-type declarations.
Ordinary ZIP archives are not expanded as text.
Macros, formulas, external relationships, and URLs are never executed or fetched.
OLE files, legacy Office formats, V1 image caches, legacy XOR caches, and WXGF have no text or image decoder.
OLE signatures alone do not establish that a file is an encrypted Excel document.

Supported V2 images require exactly one existing image configuration directory matching the selected account suffix.
The account identity and derived key remain in memory.
Missing, ambiguous, or unsupported account configuration reports `image_key_required`.
The reader never falls back to another account or starts key capture.

## Limits and states

| Limit | Value |
| --- | --- |
| Cached source bytes | 16 MiB per read |
| Returned image or binary bytes | 8 MiB |
| Message XML or protobuf bytes | 4 MiB |
| Office XML part | 4 MiB |
| ZIP entries and declared total uncompressed bytes | 1,000 entries and 32 MiB |
| PDF pages | 100 |
| PDF configured decoding work per stream | 128 MiB |
| PDF page content stream before text extraction | 4 MiB |
| PDF configured decompression and declared stream limits | 32 MiB |
| Extracted text | 2,000,000 characters |
| Text page | 1–100,000 characters; default 20,000 |
| Parser subprocess | 20 seconds per read |
| Image pixels | 25,000,000 |

These limits bound source reads, selected parser work, and returned content.
They are not an operating-system memory quota. PDF parsing and image decoding can allocate more memory than the source size.
Repeated text pages re-read and re-extract the current cache file; they are not an atomic snapshot.

Common states are:

| State | Meaning |
| --- | --- |
| `ready` | Content is available in this response. |
| `no_extractable_text` | The bounded text view is empty. No OCR was attempted. |
| `attachment_missing` | No matching index entry or requested cached variant exists. |
| `attachment_ambiguous` | More than one cache identity or path matches. |
| `attachment_integrity_failed` | Actual file size or MD5 differs from the message. |
| `attachment_path_invalid` | An index path or resolved symlink leaves the selected account cache. |
| `attachment_too_large`, `image_too_large` | A source, response, or pixel limit was exceeded. |
| `image_key_required` | No unique supported configuration matches this account. |
| `image_format_unsupported`, `image_decode_failed` | Unsupported cache format, invalid bounds, padding, or image data. |
| `encrypted_document` | A PDF or ZIP encryption marker prevents text extraction. |
| `format_unsupported` | No supported text decoder matches the bytes and container declarations. |
| `document_invalid`, `extraction_limit`, `extraction_timeout`, `extraction_failed` | The document could not be parsed within the supported constraints. |
| `database_missing`, `key_missing`, `key_invalid`, `schema_unsupported`, `database_read_failed` | A required attachment index cannot be read. |

A ready source status checks core conversation and message databases.
Attachment indexes, caches, and image configuration are checked only when an attachment tool needs them.
