#!/usr/bin/env python3
"""Build a Registry release pair from a verified, published AnyMessage sdist."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import tomllib
import zipfile

from check_publication import check_archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if hashlib.sha256(args.source.read_bytes()).hexdigest() != args.sha256.lower():
        raise ValueError("The source archive does not match the expected SHA-256.")
    if check_archive(args.source):
        raise ValueError("The source archive failed the publication check.")
    source_files: dict[str, bytes] = {}
    with tarfile.open(args.source) as archive:
        metadata = [item for item in archive.getmembers() if item.isfile() and len(PurePosixPath(item.name).parts) == 2 and PurePosixPath(item.name).name == "pyproject.toml"]
        if len(metadata) != 1:
            raise ValueError("Expected one project at the source archive root.")
        project_file = archive.extractfile(metadata[0])
        assert project_file is not None
        project = tomllib.loads(project_file.read().decode())["project"]
        version = project["version"]
        if project["name"] != "anymessage" or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
            raise ValueError("Expected an AnyMessage stable release.")
        root = f"anymessage-{version}"
        for item in archive.getmembers():
            path = PurePosixPath(item.name)
            if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != root:
                raise ValueError("Unsafe or inconsistent source archive path.")
            if not item.isfile():
                continue
            name = str(PurePosixPath(*path.parts[1:]))
            allowed = name in {"pyproject.toml", "uv.lock", "README.md", "README.zh-CN.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "SECURITY.md", "CONTRIBUTING.md", "CHANGELOG.md", "PKG-INFO", ".gitignore"} or name.startswith(("src/anymessage/", "tools/wechat-bootstrap/", "docs/", "examples/", "scripts/", "tests/", ".github/assets/"))
            if not allowed:
                continue
            if name in source_files:
                raise ValueError("Duplicate source archive path.")
            content = archive.extractfile(item)
            assert content is not None
            source_files[name] = content.read()
    required = {"pyproject.toml", "uv.lock", "README.md", "README.zh-CN.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "tools/wechat-bootstrap/LICENSE", "tools/wechat-bootstrap/bootstrap_keys.py", "tools/wechat-bootstrap/capture_keys.py", "src/anymessage/server.py"}
    if not required <= source_files.keys():
        raise ValueError("The release is missing project, runtime, or license files.")
    locked = [item for item in tomllib.loads(source_files["uv.lock"].decode())["package"] if item["name"] == "anymessage"]
    if len(locked) != 1 or locked[0]["version"] != version or locked[0]["source"] != {"editable": "."}:
        raise ValueError("The local project and lock disagree.")
    supports_export_config = tuple(int(part) for part in version.split(".")) >= (0, 9, 0)
    manifest = {
        "manifest_version": "0.4", "name": "anymessage", "display_name": "AnyMessage", "version": version,
        "description": "Read-only local Telegram JSON and WeChat chats. WeChat requires SQLCipher and account setup." if supports_export_config else "Read-only WeChat chats and local attachments for AI on macOS. Requires SQLCipher and setup.",
        "long_description": "For Apple Silicon macOS 14 or later with a compatible MCPB 0.4 UV host. The default entry reads WeChat. Reading your WeChat chats requires SQLCipher, account keys and explicit account setup. To read a local Telegram Desktop JSON export, generate a separate stdio entry with an explicit export path; this mode needs no WeChat setup, keys or SQLCipher. Installing the bundle does not capture keys or connect an account. A separate fictional demo is available." if supports_export_config else "For Apple Silicon macOS 14 or later. Requires a compatible MCPB 0.4 UV host, SQLCipher, and explicit AnyMessage account setup. Installation does not capture keys or connect an account. See the documentation for the separate fictional demo.",
        "author": {"name": "Kenichi Saito", "url": "https://github.com/knchst"},
        "repository": {"type": "git", "url": "https://github.com/knchst/AnyMessage"},
        "homepage": "https://github.com/knchst/AnyMessage", "documentation": "https://github.com/knchst/AnyMessage#install-and-set-up",
        "license": "MIT", "compatibility": {"platforms": ["darwin"], "runtimes": {"python": ">=3.11"}},
        "server": {"type": "uv", "entry_point": "src/anymessage/server.py", "mcp_config": {"command": "uv", "args": ["run", "--frozen", "--no-dev", "--directory", "${__dirname}", "python", "-m", "anymessage.server", "mcp"]}},
    }
    source_files["manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(source_files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    bundle = buffer.getvalue()
    filename = f"anymessage-{version}.mcpb"
    record = {
        "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
        "name": "io.github.knchst/anymessage", "title": "AnyMessage", "description": manifest["description"], "version": version,
        "repository": {"url": "https://github.com/knchst/AnyMessage", "source": "github"},
        "packages": [{"registryType": "mcpb", "identifier": f"https://github.com/knchst/AnyMessage/releases/download/v{version}/{filename}", "fileSha256": hashlib.sha256(bundle).hexdigest(), "transport": {"type": "stdio"}}],
    }
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / filename).write_bytes(bundle)
    (args.out / "server.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"Built {filename} and server.json from verified release {version}.")


if __name__ == "__main__":
    main()
