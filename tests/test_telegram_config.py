from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest
import tomlkit

from anymessage import cli
from anymessage.clients import CLIENTS, ClientSpec, configuration, server_entry
from anymessage.settings import Settings


@pytest.fixture
def isolated_generation(tmp_path, monkeypatch):
    directory = tmp_path / "client settings"
    directory.mkdir()
    paths = {name: directory / name for name in CLIENTS}
    for path in paths.values():
        path.write_bytes(b"unrelated client sentinel\n")
    config = tmp_path / "broken WeChat config.json"
    config.write_text("unrelated invalid JSON [")
    monkeypatch.setenv("ANYMESSAGE_CONFIG_FILE", str(config))
    monkeypatch.setattr(ClientSpec, "path", lambda self: paths[self.name])
    monkeypatch.setattr(sys, "argv", ["/invoked path/anymessage"])
    monkeypatch.chdir(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("Configuration generation read settings or wrote a client file")

    monkeypatch.setattr(Settings, "load", forbidden)
    monkeypatch.setattr("anymessage.settings.resolve_sqlcipher", forbidden)
    monkeypatch.setattr("anymessage.clients.private_write", forbidden)
    monkeypatch.setattr(cli, "register", forbidden)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    yield tmp_path
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("client", [None, *CLIENTS])
@pytest.mark.parametrize("format", ["json", "toml"])
@pytest.mark.parametrize("server_only", [False, True])
@pytest.mark.parametrize("position", ["parent", "child"])
def test_telegram_config_shapes_and_flag_positions(isolated_generation, capsys, monkeypatch, client, format, server_only, position):
    export = Path("東京 chat/alias/../selected.json")
    arguments = ["mcp", "--telegram-export", str(export), "config"] if position == "parent" else ["mcp", "config", "--telegram-export", str(export)]
    arguments += ["--format", format]
    if client:
        arguments += ["--client", client]
    if server_only:
        arguments.append("--server-only")
    original_open = Path.open

    def formatting_open(path, *args, **kwargs):
        if path.is_relative_to(isolated_generation):
            pytest.fail("Formatting accessed an export, settings, or client file")
        return original_open(path, *args, **kwargs)

    with monkeypatch.context() as formatting:
        formatting.setattr(Path, "open", formatting_open)
        assert cli.main(arguments) == 0
    output = capsys.readouterr()
    assert not output.err
    document = json.loads(output.out) if format == "json" else tomlkit.parse(output.out).unwrap()
    if not server_only:
        wrapper = "mcpServers" if format == "json" else "mcp_servers"
        assert set(document) == {wrapper} and set(document[wrapper]) == {"telegram-export"}
        document = document[wrapper]["telegram-export"]
    assert document == {
        **({"type": "stdio"} if client == "cursor" else {}),
        "command": "/invoked path/anymessage",
        "args": ["mcp", "--telegram-export", str(isolated_generation / export)],
    }


@pytest.mark.parametrize("arguments", [
    ["mcp", "--telegram-export", "one", "--telegram-export", "one", "config"],
    ["mcp", "config", "--telegram-export", "one", "--telegram-export", "two"],
    ["mcp", "--telegram-export", "one", "config", "--telegram-export", "one"],
    ["mcp", "--telegram-export", "one", "config", "--telegram-export", "two"],
    ["mcp", "config", "--telegram-export"],
    ["mcp", "--telegram-export"],
    ["mcp", "--transport", "streamable-http", "config", "--telegram-export", "one"],
    ["mcp", "--port", "8765", "--telegram-export", "one", "config"],
    ["mcp", "--port", "0", "config", "--telegram-export", "one"],
    ["mcp", "config", "--telegram-export", "one", "--transport", "stdio"],
    ["mcp", "config", "--telegram-export", "one", "--port", "8765"],
    ["mcp", "--telegram-export", "one", "add", "cursor"],
    ["mcp", "add", "cursor", "--telegram-export", "one"],
])
def test_telegram_config_usage_errors_do_not_print_or_register(isolated_generation, capsys, arguments):
    with pytest.raises(SystemExit) as error:
        cli.main(arguments)
    assert error.value.code == 2
    output = capsys.readouterr()
    assert not output.out and output.err


@pytest.mark.parametrize("format", ["json", "toml"])
@pytest.mark.parametrize("server_only", [False, True])
def test_grokbot_guidance_precedes_entry_creation(isolated_generation, capsys, monkeypatch, format, server_only):
    monkeypatch.setattr("anymessage.clients.server_entry", lambda *_args, **_kwargs: pytest.fail("Grok Bot must refuse before constructing an entry"))
    arguments = ["mcp", "config", "--telegram-export", "missing.json", "--client", "grokbot", "--format", format, "--command", "relative executable"]
    if server_only:
        arguments.append("--server-only")
    assert cli.main(arguments) == 2
    output = capsys.readouterr()
    assert not output.out and "Grok Bot's computer" in output.err


def test_generation_preserves_export_aliases_and_executable_precedence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "actual"
    (target / "nested").mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(target / "nested", target_is_directory=True)
    selected = Path("alias/../chosen.json")
    monkeypatch.setattr(sys, "argv", ["relative bin/anymessage"])
    monkeypatch.setattr("anymessage.clients.shutil.which", lambda _: "/path bin/anymessage")
    assert server_entry(telegram_export=selected)["command"] == str(tmp_path / "relative bin/anymessage")
    explicit = server_entry(command="./chosen bin/anymessage", telegram_export=selected)
    assert explicit["command"] == str(tmp_path / "chosen bin/anymessage")
    assert explicit["args"][-1] == str(tmp_path / "alias/../chosen.json")
    monkeypatch.setattr(sys, "argv", ["python"])
    assert server_entry(telegram_export=selected)["command"] == "/path bin/anymessage"
    assert server_entry(telegram_export=Path("~/選択.json"))["args"][-1] == str(Path.home() / "選択.json")


@pytest.mark.parametrize("kind", ["missing", "invalid", "symlink"])
def test_generation_defers_export_validation(tmp_path, monkeypatch, kind):
    export = tmp_path / "selected.json"
    if kind == "invalid":
        export.write_text("invalid JSON")
    elif kind == "symlink":
        target = tmp_path / "target.json"
        target.write_text('{"messages": []}')
        export.symlink_to(target)
    entry = json.loads(configuration("json", command="/chosen/anymessage", telegram_export=export))["mcpServers"]["telegram-export"]
    assert entry["args"][-1] == str(export)


@pytest.mark.parametrize("export", [False, True])
@pytest.mark.parametrize("port", [None, 4321, 0])
def test_standalone_keeps_last_export_and_explicit_port(tmp_path, monkeypatch, export, port):
    calls = []

    class Server:
        def run(self, **options):
            calls.append(options)

    def create(**options):
        calls.append(options)
        return Server()

    monkeypatch.setattr("anymessage.server.create_server", create)
    arguments = ["mcp", "--transport", "streamable-http"]
    if export:
        arguments += ["--telegram-export", "first.json", "--telegram-export", "last.json"]
    if port is not None:
        arguments += ["--port", str(port)]
    if port == 0:
        with pytest.raises(SystemExit) as error:
            cli.main(arguments)
        assert error.value.code == 2 and calls == []
    else:
        assert cli.main(arguments) == 0
        assert calls == [{"port": 8765 if port is None else port, "telegram_export": Path("last.json") if export else None}, {"transport": "streamable-http"}]


def test_wechat_configuration_keeps_ignored_parent_transport_options(isolated_generation, capsys):
    assert cli.main(["mcp", "--transport", "streamable-http", "--port", "0", "config"]) == 0
    entry = json.loads(capsys.readouterr().out)["mcpServers"]["AnyMessage"]
    assert entry["args"] == ["mcp"]
    assert entry["env"]["ANYMESSAGE_CONFIG_FILE"] == str(isolated_generation / "broken WeChat config.json")
