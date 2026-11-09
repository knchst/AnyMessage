#!/usr/bin/env python3
"""Check the index, working files, archives, and optional Git history for local material."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import zipfile


HOME_PATH = re.compile(rb"/(?:Users|home)/[^/\s\"'<>]+/")
ACCOUNT_ID = re.compile(rb"\bwxid_(?!fixture\b)[A-Za-z0-9]{6,}\b")
PRIVATE_KEY = re.compile(rb"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")
NOREPLY_EMAIL = re.compile(rb"(?:(?:[0-9]+\+)?[A-Za-z0-9-]+@users\.noreply\.github\.com|noreply@github\.com)")
PRIVATE_DIRS = {".git", ".venv", "private", "captures", ".wechat-local", ".audit", "__pycache__"}


def inspect_file(name: str, data: bytes) -> list[str]:
    path = PurePosixPath(name)
    reasons = []
    if any(part in PRIVATE_DIRS or part.endswith((".app", ".disabled")) for part in path.parts):
        reasons.append("local-only directory or application copy")
    filename = path.name.lower()
    if (filename in {".ds_store", "keys.json"} or filename.startswith(".env")
            or filename.endswith((".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".log", ".pyc", ".bundle", "-keys.json", "_keys.json"))):
        reasons.append("local database, key, log, or generated artifact")
    if HOME_PATH.search(data):
        reasons.append("personal absolute home path")
    if ACCOUNT_ID.search(data):
        reasons.append("non-fixture WeChat account identifier")
    if PRIVATE_KEY.search(data):
        reasons.append("private key material")
    if data.lstrip().startswith(b"{"):
        try:
            payload = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            payload = None
        if isinstance(payload, dict):
            entries = payload.get("keys", payload)
            if isinstance(entries, dict) and any(
                isinstance(entry, dict) and isinstance(entry.get("enc_key"), str)
                and re.fullmatch(r"[0-9a-fA-F]{64}", entry["enc_key"])
                for entry in entries.values()
            ):
                reasons.append("SQLCipher key manifest")
    return reasons


def git(root: Path, *arguments: str) -> bytes:
    return subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True).stdout


def check_tree(root: Path) -> list[tuple[str, str]]:
    names = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split(b"\0")
    findings = []
    for item in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not item:
            continue
        header, raw_name = item.split(b"\t", 1)
        _, object_id, _ = header.split()
        name = raw_name.decode("utf-8")
        data = git(root, "cat-file", "blob", object_id.decode())
        findings.extend(("index:" + name, reason) for reason in inspect_file(name, data))
    for raw_name in sorted(set(names) - {b""}):
        name = raw_name.decode("utf-8")
        path = root / name
        if path.is_file():
            findings.extend((name, reason) for reason in inspect_file(name, path.read_bytes()))
    return findings


def check_history(root: Path, require_noreply: bool) -> list[tuple[str, str]]:
    findings = []
    checked_blobs = set()
    for commit in git(root, "rev-list", "--all").decode().splitlines():
        label = "commit " + commit[:12]
        metadata = git(root, "cat-file", "commit", commit)
        findings.extend((label, reason) for reason in inspect_file("commit", metadata))
        if require_noreply:
            addresses = re.findall(rb"^(?:author|committer) .*<([^>]+)> ", metadata, re.MULTILINE)
            if any(not NOREPLY_EMAIL.fullmatch(address) for address in addresses):
                findings.append((label, "author or committer email is not a GitHub noreply address"))
        for item in git(root, "ls-tree", "-rz", "--full-tree", commit).split(b"\0"):
            if not item:
                continue
            header, raw_name = item.split(b"\t", 1)
            _, kind, object_id = header.split()
            name = raw_name.decode("utf-8")
            if kind != b"blob":
                continue
            identity = (object_id, name)
            if identity in checked_blobs:
                continue
            checked_blobs.add(identity)
            data = git(root, "cat-file", "blob", object_id.decode())
            findings.extend((label + ":" + name, reason) for reason in inspect_file(name, data))
    return findings


def check_archive(path: Path) -> list[tuple[str, str]]:
    findings = []
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                if not member.is_dir():
                    findings.extend((path.name + ":" + member.filename, reason) for reason in inspect_file(member.filename, archive.read(member)))
    else:
        with tarfile.open(path) as archive:
            for member in archive.getmembers():
                if member.isfile():
                    source = archive.extractfile(member)
                    assert source is not None
                    findings.extend((path.name + ":" + member.name, reason) for reason in inspect_file(member.name, source.read()))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--history", action="store_true", help="Also inspect all reachable commits, blobs and commit messages")
    parser.add_argument("--require-noreply", action="store_true", help="With --history, require GitHub noreply author and committer addresses")
    parser.add_argument("--archive", type=Path, nargs="+", default=[], help="Inspect distribution members instead of the index and working tree")
    args = parser.parse_args()
    if args.require_noreply and not args.history:
        parser.error("--require-noreply requires --history")
    findings = []
    if not args.archive:
        findings.extend(check_tree(args.root))
    for archive in args.archive:
        if not archive.is_file():
            parser.error("archive does not exist: " + str(archive))
        findings.extend(check_archive(archive))
    if args.history:
        findings.extend(check_history(args.root, args.require_noreply))
    for label, reason in findings:
        print(label + ": " + reason)
    if findings:
        print(f"Publication check failed: {len(findings)} finding(s).")
        return 1
    print("Publication check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
