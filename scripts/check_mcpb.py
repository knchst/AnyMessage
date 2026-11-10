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
import shutil
import subprocess
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
        supports_export = tuple(int(part) for part in version.split(".")) >= (0, 7, 0)
        supports_export_media = tuple(int(part) for part in version.split(".")) >= (0, 8, 0)
        supports_export_config = tuple(int(part) for part in version.split(".")) >= (0, 9, 0)
        if supports_export_config:
            description = manifest.get("description", "")
            long_description = manifest.get("long_description", "")
            if len(description) > 100 or record.get("description") != description or not all(text in description for text in ("Telegram JSON", "WeChat", "WeChat requires SQLCipher and account setup")):
                raise ValueError("The manifest and Registry must describe both sources and qualify WeChat requirements.")
            if not all(text in long_description for text in ("Apple Silicon macOS 14", "MCPB 0.4 UV host", "default entry reads WeChat", "SQLCipher, account keys and explicit account setup", "separate stdio entry", "explicit export path", "no WeChat setup, keys or SQLCipher")):
                raise ValueError("The manifest must explain the default WeChat entry and independent Telegram setup.")
        expected_tools = {"status", "list_conversations", "get_messages", "search_messages", "list_attachments", "read_attachment"}
        if supports_context:
            expected_tools.add("get_message_context")
        required = {"README.md", "README.zh-CN.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "tools/wechat-bootstrap/LICENSE", "tools/wechat-bootstrap/bootstrap_keys.py", "tools/wechat-bootstrap/capture_keys.py", "uv.lock", "src/anymessage/server.py", "docs/demo.md", ".github/assets/demo.gif"}
        if supports_export:
            required.update(("src/anymessage/telegram_export.py", "docs/telegram-export.md", "examples/telegram-fictional.json"))
        if supports_export_media:
            required.update(("src/anymessage/telegram_media.py", "examples/river-spec-v2.txt"))
        if not required <= set(names):
            raise ValueError("The bundle is missing runtime, documentation, or license files.")
        print(f"Release pair matches {args.tag}, its manifest, and SHA-256.")
        if not args.runtime:
            return
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        with tempfile.TemporaryDirectory(prefix="anymessage-mcpb-") as temporary:
            temporary = str(Path(temporary).resolve())
            root = Path(temporary) / "bundle with spaces"
            archive.extractall(root)
            environment = {key: value for key, value in os.environ.items() if not key.startswith("ANYMESSAGE_") and key not in {"PYTHONPATH", "PYTHONHOME"}}
            environment.update(ANYMESSAGE_CONFIG_FILE=str(root / "absent-config.json"), ANYMESSAGE_DATA_DIR=str(root / "absent-data"), ANYMESSAGE_KEYS_FILE=str(root / "absent-manifest.json"), ANYMESSAGE_ACCOUNT="fixture")
            environment["UV_PROJECT_ENVIRONMENT"] = str(root / ".venv")
            arguments = [item.replace("${__dirname}", str(root)) for item in launch["args"]]
            async with stdio_client(StdioServerParameters(command=args.uv, args=arguments, env=environment, cwd=temporary)) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    tools = await client.list_tools()
                    assert {tool.name for tool in tools.tools} == expected_tools
                    expected_schemas = {tool.name: tool.inputSchema for tool in tools.tools}
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
            if supports_export:
                (root / "absent-config.json").write_text("unrelated broken WeChat configuration")
                if supports_export_config:
                    selected = Path(temporary) / "東京 export with spaces"
                    selected.mkdir()
                    export = selected / "fictional export.json"
                    shutil.copyfile(root / "examples/telegram-fictional.json", export)
                    shutil.copyfile(root / "examples/river-spec-v2.txt", selected / "river-spec-v2.txt")
                    executable = root / ".venv/bin/anymessage"
                    generated = subprocess.run([str(executable), "mcp", "config", "--telegram-export", str(export.relative_to(temporary)), "--client", "codex", "--format", "toml"], check=True, capture_output=True, text=True, cwd=temporary, env=environment)
                    assert not generated.stderr
                    entry = tomllib.loads(generated.stdout)["mcp_servers"]["telegram-export"]
                    assert entry == {"command": str(executable), "args": ["mcp", "--telegram-export", str(export)]}
                    parameters = StdioServerParameters(command=entry["command"], args=entry["args"], env={**environment, **entry.get("env", {})}, cwd=str(root))
                else:
                    export_arguments = [*arguments, "--telegram-export", str(root / "examples/telegram-fictional.json")]
                    parameters = StdioServerParameters(command=args.uv, args=export_arguments, env=environment, cwd=temporary)
                async with stdio_client(parameters) as (read, write):
                    async with ClientSession(read, write) as client:
                        await client.initialize()
                        tools = (await client.list_tools()).tools
                        assert {tool.name: tool.inputSchema for tool in tools} == expected_schemas
                        status = await client.call_tool("status", {})
                        assert len(status.structuredContent["sources"]) == 1
                        assert status.structuredContent["sources"][0]["source"] == "telegram_export" and status.structuredContent["sources"][0]["state"] == "ready"
                        search = await client.call_tool("search_messages", {"query": "proposal", "conversation_id": "telegram:private_group:42"})
                        hit = search.structuredContent["items"][0]
                        context = await client.call_tool("get_message_context", {"conversation_id": hit["conversation_id"], "message_id": hit["id"], "before": 1, "after": 2})
                        assert not context.isError and [row["id"] for row in context.structuredContent["items"]] == ["1", "2", "3", "4"]
                        assert context.structuredContent["items"][-1]["text"].startswith("Approved:")
                        assert context.structuredContent["snapshot_consistency"] == "immutable_export"
                        media = await client.call_tool("list_attachments", {"conversation_id": hit["conversation_id"], "message_id": "5"})
                        if supports_export_media:
                            assert media.structuredContent["items"][0]["state"] == "attachment_missing"
                            expanded = await client.call_tool("get_message_context", {"conversation_id": hit["conversation_id"], "message_id": hit["id"], "before": 0, "after": 4})
                            specification = expanded.structuredContent["items"][-1]
                            assert specification["id"] == "6"
                            listing = await client.call_tool("list_attachments", {"conversation_id": specification["conversation_id"], "message_id": specification["id"]})
                            attachment = await client.call_tool("read_attachment", {"conversation_id": specification["conversation_id"], "message_id": specification["id"], "attachment_id": listing.structuredContent["items"][0]["id"]})
                            assert not attachment.isError and attachment.content[0].type == "text"
                            assert "Battery: 500 mAh" in attachment.content[0].text
                            if supports_export_config:
                                assert attachment.content[0].text == (selected / "river-spec-v2.txt").read_text()
                            assert not attachment.structuredContent["content_verified"]
                            assert attachment.structuredContent["media_consistency"] == "current_local_files"
                        else:
                            assert media.structuredContent["items"] == [] and media.structuredContent["metadata_state"] == "unsupported"
                print("Explicit Telegram export passed actual search-hit context with broken unrelated WeChat configuration.")
                assert (root / "absent-config.json").read_text() == "unrelated broken WeChat configuration"
                if supports_export_config:
                    print("The extracted environment generated TOML and its parsed command launched unchanged from another directory.")
                print("Telegram specification bytes were read." if supports_export_media else "This older release reports unsupported Telegram media.")


if __name__ == "__main__":
    asyncio.run(main())
