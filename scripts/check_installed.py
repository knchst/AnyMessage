#!/usr/bin/env python3
"""Smoke-test installed CLI resources without accessing a real messaging account."""

from __future__ import annotations

import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


EXPECTED = {
    "bootstrap_keys.py": "ce8596107df3ca3715c8b63b522bbcc84148207217fade3b642975323c65b31c",
    "capture_keys.py": "0b0b9f9296bf02219c07537827ed9ce97671611ffeab36572bedb267a7704d65",
    "LICENSE": "72380798af61d6131287b0dd3c8dc5345535df641002fece3a02d7ef109c5f8e",
}


def main() -> None:
    resources = files("anymessage").joinpath("vendor/wechat-bootstrap")
    for name, digest in EXPECTED.items():
        assert hashlib.sha256(resources.joinpath(name).read_bytes()).hexdigest() == digest, name
    with tempfile.TemporaryDirectory() as directory:
        environment = {key: value for key, value in os.environ.items() if not key.startswith("ANYMESSAGE_")}
        environment.update(ANYMESSAGE_CONFIG_FILE=str(Path(directory) / "config.json"), ANYMESSAGE_DATA_DIR=str(Path(directory) / "no-account"))
        executable = str(Path(sys.executable).parent / "anymessage")
        for arguments in (["--version"], ["setup", "--help"], ["mcp", "config", "--format", "toml"]):
            subprocess.run([executable, *arguments], check=True, capture_output=True, text=True, env=environment)
        diagnostic = subprocess.run([executable, "doctor", "--json"], capture_output=True, text=True, env=environment)
        assert diagnostic.returncode == 1
        assert json.loads(diagnostic.stdout)["source"]["state"] == "data_missing"
        assert not (Path(directory) / "config.json").exists()
    print("Installed CLI and unchanged bootstrap resources passed.")


if __name__ == "__main__":
    main()
