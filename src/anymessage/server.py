"""Read-only local messages through stdio or loopback Streamable HTTP MCP."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import BlobResourceContents, CallToolResult, EmbeddedResource, ImageContent, TextContent, ToolAnnotations
from pydantic import Field

from anymessage.errors import SourceError
from anymessage.settings import Settings
from anymessage.telegram_export import TelegramExport
from anymessage.wechat import WeChat


ConversationId = Annotated[str, Field(strict=True, min_length=1, max_length=512)]
EpochSecond = Annotated[int, Field(strict=True, ge=-(2**63), le=2**63 - 1)]


def create_server(*, port: int = 8765, demo_settings: Settings | None = None, telegram_export: Path | None = None) -> FastMCP:
    if demo_settings is not None and telegram_export is not None:
        raise SourceError("request_invalid", "Choose either the WeChat demo or a Telegram export.")
    source = TelegramExport(telegram_export) if telegram_export is not None else WeChat(demo_settings) if demo_settings is not None else WeChat()
    instructions = "Read-only local messages. Call status first. Searches are bounded and conversation-scoped. A locked source requires local per-database keys."
    if demo_settings is not None:
        instructions += " This is a temporary fictional Atlas project dataset, not personal WeChat history."
    if telegram_export is not None:
        instructions = "Read-only messages from one immutable Telegram Desktop JSON export. Call status first. Searches require explicit conversations and cover exported text, not attachment contents. Classic attachments read current local files beneath the selected export folder; content_verified stays false because no trusted Telegram digest is available. Export coverage can be incomplete. Restart to load changed JSON bytes."
    server = FastMCP("AnyMessage Demo" if demo_settings is not None else "AnyMessage", host="127.0.0.1", port=port, instructions=instructions)
    annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

    @server.tool(annotations=annotations)
    def status() -> dict[str, Any]:
        """Inspect the selected source's readiness and coverage without returning message bodies."""
        return {"sources": [source.status()], **({"dataset": "synthetic"} if demo_settings is not None else {})}

    @server.tool(annotations=annotations)
    def list_conversations(limit: Annotated[int, Field(ge=1, le=200)] = 50, offset: Annotated[int, Field(ge=0, le=100000)] = 0) -> dict[str, Any]:
        """List the selected source's conversations, newest first."""
        return source.list_conversations(limit, offset)

    @server.tool(annotations=annotations)
    def get_messages(conversation_id: ConversationId, limit: Annotated[int, Field(ge=1, le=200)] = 50, cursor: Annotated[str | None, Field(max_length=4096)] = None, *, start_time: EpochSecond | None = None, end_time: EpochSecond | None = None) -> dict[str, Any]:
        """Read one conversation's local messages, newest first. Optional UTC Unix-second bounds include start_time and exclude end_time. Reuse next_cursor with the same conversation and bounds for older messages."""
        return source.get_messages(conversation_id, limit, cursor, start_time=start_time, end_time=end_time)

    @server.tool(annotations=annotations)
    def get_message_context(conversation_id: ConversationId, message_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)], *, before: Annotated[int, Field(strict=True, ge=0, le=50)] = 3, after: Annotated[int, Field(strict=True, ge=0, le=50)] = 3) -> dict[str, Any]:
        """Read an exact message and its nearest older and newer records in chronological order. Pass both conversation_id and id from a returned hit. The anchor appears once at anchor_index. Each side accepts 0–50 records; has_more_before and has_more_after describe remaining dated records even when a count is zero. Check source coverage and snapshot_consistency."""
        return source.get_message_context(conversation_id, message_id, before=before, after=after)

    @server.tool(annotations=annotations)
    def search_messages(query: Annotated[str, Field(min_length=1, max_length=1000)], conversation_id: ConversationId | None = None, limit: Annotated[int, Field(ge=1, le=200)] = 20, scan_limit: Annotated[int, Field(ge=1, le=10000)] = 1000, cursor: Annotated[str | None, Field(max_length=4096)] = None, *, conversation_ids: Annotated[list[ConversationId] | None, Field(min_length=1, max_length=8)] = None, start_time: EpochSecond | None = None, end_time: EpochSecond | None = None) -> dict[str, Any]:
        """Search decoded local text, newest first. Provide exactly one selector: conversation_id or 1–8 distinct conversation_ids. UTC Unix-second bounds include start_time and exclude end_time. scan_limit is shared across the selection. Continue with the original query, selection, and bounds. Cite each hit with both conversation_id and id; bare message ids can repeat across chats."""
        return source.search_messages(query, conversation_id, limit, scan_limit, cursor, conversation_ids=conversation_ids, start_time=start_time, end_time=end_time)

    @server.tool(annotations=annotations)
    def list_attachments(conversation_id: Annotated[str, Field(min_length=1, max_length=512)], message_id: Annotated[str, Field(min_length=1, max_length=128)]) -> dict[str, Any]:
        """Inspect attachment variants and local availability for one exact message. WeChat checks cached bytes when read. Telegram classic variants report current export-folder availability without authenticating media."""
        return source.list_attachments(conversation_id, message_id)

    @server.tool(annotations=annotations)
    def read_attachment(conversation_id: Annotated[str, Field(min_length=1, max_length=512)], message_id: Annotated[str, Field(min_length=1, max_length=128)], attachment_id: Annotated[str, Field(min_length=32, max_length=32)], offset: Annotated[int, Field(ge=0, le=2000000)] = 0, limit: Annotated[int, Field(ge=1, le=100000)] = 20000, as_binary: bool = False) -> CallToolResult:
        """Read local attachment text, an image, or explicit original bytes. Text offsets count Unicode characters. Binary and image results require offset zero. Check source-specific verification metadata; Telegram content_verified is always false. No downloads or OCR."""
        result = source.read_attachment(conversation_id, message_id, attachment_id, offset, limit, as_binary)
        structured = {**result.metadata, **({"text": result.text} if result.text is not None else {})}
        content: list[TextContent | ImageContent | EmbeddedResource] = []
        if result.text is not None:
            content.append(TextContent(type="text", text=result.text))
        elif result.data is not None:
            encoded = base64.b64encode(result.data).decode("ascii")
            if result.image:
                content.append(ImageContent(type="image", data=encoded, mimeType=result.mime_type))
            else:
                content.append(EmbeddedResource(type="resource", resource=BlobResourceContents(uri="attachment://" + source.source_id + "/" + attachment_id, mimeType=result.mime_type, blob=encoded)))
        content.append(TextContent(type="text", text=json.dumps(structured)))
        return CallToolResult(content=content, structuredContent=structured, isError=result.metadata["state"] not in ("ready", "no_extractable_text"))

    return server


def main() -> None:
    from anymessage.cli import main as cli_main

    raise SystemExit(cli_main())


if __name__ == "__main__":
    main()
