"""AnyMessage stdio MCP entry point."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
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

    return server


def main() -> None:
    from anymessage.cli import main as cli_main

    raise SystemExit(cli_main())


if __name__ == "__main__":
    main()
