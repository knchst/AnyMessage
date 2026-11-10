#!/usr/bin/env python3
"""Check a released MCPB pair; optionally exercise its isolated MCP runtime."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import tomllib
import zipfile

from check_publication import check_archive


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("metadata", type=Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--runtime", action="store_true")
    parser.add_argument("--uv", default="uv")
    args = parser.parse_args()
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", args.tag):
        raise ValueError("Expected a stable release tag vN.N.N.")
    version = args.tag[1:]
    filename = f"anymessage-{version}.mcpb"
    bundle = args.metadata.parent / filename
    record = json.loads(args.metadata.read_text())
    package = {"registryType": "mcpb", "identifier": f"https://github.com/knchst/AnyMessage/releases/download/{args.tag}/{filename}", "fileSha256": hashlib.sha256(bundle.read_bytes()).hexdigest(), "transport": {"type": "stdio"}}
    if record.get("name") != "io.github.knchst/anymessage" or record.get("version") != version or record.get("repository") != {"url": "https://github.com/knchst/AnyMessage", "source": "github"} or record.get("packages") != [package] or "remotes" in record:
        raise ValueError("Registry identity, release URL, version, or artifact hash does not match.")
    if check_archive(bundle):
        raise ValueError("The bundle failed the publication check.")
    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or any(PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts for name in names):
            raise ValueError("Unsafe or duplicate bundle path.")
        manifest = json.loads(archive.read("manifest.json"))
        launch = {"command": "uv", "args": ["run", "--frozen", "--no-dev", "--directory", "${__dirname}", "python", "-m", "anymessage.server", "mcp"]}
        if manifest.get("manifest_version") != "0.4" or manifest.get("name") != "anymessage" or manifest.get("version") != version or manifest.get("server") != {"type": "uv", "entry_point": "src/anymessage/server.py", "mcp_config": launch}:
            raise ValueError("The embedded manifest does not match the supported launch contract.")
        project = tomllib.loads(archive.read("pyproject.toml").decode())["project"]
        if project["name"] != "anymessage" or project["version"] != version:
            raise ValueError("The bundled project and release versions disagree.")
        supports_context = tuple(int(part) for part in version.split(".")) >= (0, 6, 0)
        expected_tools = {"status", "list_conversations", "get_messages", "search_messages", "list_attachments", "read_attachment"}
        if supports_context:
            expected_tools.add("get_message_context")
        required = {"README.md", "README.zh-CN.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "tools/wechat-bootstrap/LICENSE", "tools/wechat-bootstrap/bootstrap_keys.py", "tools/wechat-bootstrap/capture_keys.py", "uv.lock", "src/anymessage/server.py", "docs/demo.md", ".github/assets/demo.gif"}
        if not required <= set(names):
            raise ValueError("The bundle is missing runtime, documentation, or license files.")
        print(f"Release pair matches {args.tag}, its manifest, and SHA-256.")
        if not args.runtime:
            return
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        with tempfile.TemporaryDirectory(prefix="anymessage-mcpb-") as temporary:
            root = Path(temporary) / "bundle with spaces"
            archive.extractall(root)
            environment = {key: value for key, value in os.environ.items() if not key.startswith("ANYMESSAGE_") and key not in {"PYTHONPATH", "PYTHONHOME"}}
            environment.update(ANYMESSAGE_CONFIG_FILE=str(root / "absent-config.json"), ANYMESSAGE_DATA_DIR=str(root / "absent-data"), ANYMESSAGE_KEYS_FILE=str(root / "absent-manifest.json"), ANYMESSAGE_ACCOUNT="fixture")
            arguments = [item.replace("${__dirname}", str(root)) for item in launch["args"]]
            async with stdio_client(StdioServerParameters(command=args.uv, args=arguments, env=environment, cwd=temporary)) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    tools = await client.list_tools()
                    assert {tool.name for tool in tools.tools} == expected_tools
                    observed_count = len(tools.tools)
                    assert all(tool.annotations and tool.annotations.readOnlyHint and tool.annotations.openWorldHint is False for tool in tools.tools)
                    status = await client.call_tool("status", {})
                    assert status.structuredContent["sources"][0]["state"] == "data_missing"
            async with stdio_client(StdioServerParameters(command=args.uv, args=arguments[:-1] + ["demo", "--mcp"], env=environment, cwd=temporary)) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    status = await client.call_tool("status", {})
                    assert status.structuredContent["dataset"] == "synthetic" and status.structuredContent["sources"][0]["state"] == "ready"
                    history = await client.call_tool("get_messages", {"conversation_id": "atlas-demo@chatroom", "limit": 20})
                    assert len(history.structuredContent["items"]) == 8
                    if supports_context:
                        search = await client.call_tool("search_messages", {"conversation_id": "atlas-demo@chatroom", "query": "Proposal:"})
                        assert not search.isError
                        hit = search.structuredContent["items"][0]
                        context = await client.call_tool("get_message_context", {"conversation_id": hit["conversation_id"], "message_id": hit["id"], "before": 1, "after": 2})
                        assert not context.isError
                        neighborhood = context.structuredContent
                        assert neighborhood["anchor_index"] == 1 and neighborhood["items"][1] == hit
                        assert neighborhood["items"][2]["sender_id"] == "Maya" and neighborhood["items"][2]["text"].startswith("Approved:")
                        specification = neighborhood["items"][3]
                        assert specification["attachments"][0]["display_name"] == "atlas-spec-v2.txt"
                        attachment = await client.call_tool("read_attachment", {"conversation_id": specification["conversation_id"], "message_id": specification["id"], "attachment_id": specification["attachments"][0]["id"]})
                        assert not attachment.isError and attachment.structuredContent["content_verified"]
                        assert neighborhood["has_more_before"] is False and neighborhood["has_more_after"] is True
                        assert neighborhood["snapshot_consistency"] == "per_query"
            print(f"Extracted bundle passed {observed_count} read-only tools, unconfigured status, and eight synthetic messages outside the checkout.")
            if supports_context:
                print("Search-hit context returned the adjacent approval and verified specification attachment.")


if __name__ == "__main__":
    asyncio.run(main())
