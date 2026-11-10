"""AnyMessage stdio MCP entry point."""

from __future__ import annotations

import base64
import json
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import BlobResourceContents, CallToolResult, EmbeddedResource, ImageContent, TextContent, ToolAnnotations
from pydantic import Field

from anymessage.wechat import WeChat


def create_server(*, port: int = 8765) -> FastMCP:
    sources = {"wechat": WeChat()}
    server = FastMCP("AnyMessage", host="127.0.0.1", port=port, instructions="Read-only local messages. Call status first. Searches are bounded and conversation-scoped. A locked source requires local per-database keys.")
    annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

    @server.tool(annotations=annotations)
    def status() -> dict[str, Any]:
        """Inspect installed sources, accounts, encryption, keys and schema readiness without reading message content."""
        return {"sources": [source.status() for source in sources.values()]}

    @server.tool(annotations=annotations)
    def list_conversations(limit: Annotated[int, Field(ge=1, le=200)] = 50, offset: Annotated[int, Field(ge=0, le=100000)] = 0) -> dict[str, Any]:
        """List the selected WeChat account's local conversations, newest first."""
        return sources["wechat"].list_conversations(limit, offset)

    @server.tool(annotations=annotations)
    def get_messages(conversation_id: Annotated[str, Field(min_length=1, max_length=512)], limit: Annotated[int, Field(ge=1, le=200)] = 50, cursor: Annotated[str | None, Field(max_length=4096)] = None) -> dict[str, Any]:
        """Read one conversation's local messages across every available message shard. Reuse next_cursor for older messages."""
        return sources["wechat"].get_messages(conversation_id, limit, cursor)

    @server.tool(annotations=annotations)
    def search_messages(query: Annotated[str, Field(min_length=1, max_length=1000)], conversation_id: Annotated[str, Field(min_length=1, max_length=512)], limit: Annotated[int, Field(ge=1, le=200)] = 20, scan_limit: Annotated[int, Field(ge=1, le=10000)] = 1000, cursor: Annotated[str | None, Field(max_length=4096)] = None) -> dict[str, Any]:
        """Search decoded text in one conversation. Report scanned count and truncation rather than claiming a full-history search."""
        return sources["wechat"].search_messages(query, conversation_id, limit, scan_limit, cursor)

    @server.tool(annotations=annotations)
    def list_attachments(conversation_id: Annotated[str, Field(min_length=1, max_length=512)], message_id: Annotated[str, Field(min_length=1, max_length=128)]) -> dict[str, Any]:
        """Inspect attachment variants and local availability for one exact message. Cached bytes are verified only when read."""
        return sources["wechat"].list_attachments(conversation_id, message_id)

    @server.tool(annotations=annotations)
    def read_attachment(conversation_id: Annotated[str, Field(min_length=1, max_length=512)], message_id: Annotated[str, Field(min_length=1, max_length=128)], attachment_id: Annotated[str, Field(min_length=32, max_length=32)], offset: Annotated[int, Field(ge=0, le=2000000)] = 0, limit: Annotated[int, Field(ge=1, le=100000)] = 20000, as_binary: bool = False) -> CallToolResult:
        """Read verified local attachment text, an image, or original bytes. Text offsets count Unicode characters. Binary and image results require offset zero. No downloads or OCR."""
        result = sources["wechat"].read_attachment(conversation_id, message_id, attachment_id, offset, limit, as_binary)
        structured = {**result.metadata, **({"text": result.text} if result.text is not None else {})}
        content: list[TextContent | ImageContent | EmbeddedResource] = []
        if result.text is not None:
            content.append(TextContent(type="text", text=result.text))
        elif result.data is not None:
            encoded = base64.b64encode(result.data).decode("ascii")
            if result.image:
                content.append(ImageContent(type="image", data=encoded, mimeType=result.mime_type))
            else:
                content.append(EmbeddedResource(type="resource", resource=BlobResourceContents(uri="attachment://wechat/" + attachment_id, mimeType=result.mime_type, blob=encoded)))
        content.append(TextContent(type="text", text=json.dumps(structured)))
        return CallToolResult(content=content, structuredContent=structured, isError=result.metadata["state"] not in ("ready", "no_extractable_text"))

    return server


def main() -> None:
    from anymessage.cli import main as cli_main

    raise SystemExit(cli_main())


if __name__ == "__main__":
    main()
