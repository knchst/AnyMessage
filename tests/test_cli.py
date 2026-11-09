from __future__ import annotations

from dataclasses import replace
import io
import json
from pathlib import Path
import stat
import sys

import pytest
import tomlkit

from anymessage import cli
from anymessage.clients import CLIENTS, configuration, json_document, register, server_entry
from anymessage.settings import Settings
from anymessage.wechat import WeChat


@pytest.fixture
def client_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "home/.codex"))
    monkeypatch.setattr(sys, "argv", ["/absolute/path/bin/anymessage"])
    return tmp_path / "home"


def test_settings_precedence_and_private_references(source, monkeypatch):
    reader, root, manifest, _ = source
    settings = Settings.load()
    settings.save()
    path = Settings.config_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "enc_key" not in path.read_text()
    monkeypatch.delenv("ANYMESSAGE_DATA_DIR")
    monkeypatch.delenv("ANYMESSAGE_KEYS_FILE")
    assert WeChat().status()["state"] == "ready"
    monkeypatch.setenv("ANYMESSAGE_KEYS_FILE", str(root / "from-env"))
    assert Settings.load().keys_file == root / "from-env"
    assert Settings.load(keys_file=manifest).keys_file == manifest
    assert Settings.load().sqlcipher == reader.executable


@pytest.mark.parametrize("client", CLIENTS)
def test_registration_preserves_other_config_and_is_idempotent(client_home, client):
    spec = CLIENTS[client]
    path = spec.path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if spec.format == "toml":
        original = '# retain this comment\nmodel = "fixture"\n[features]\nhello = true\n[mcp_servers.other]\ncommand = "/bin/other" # retain another comment\n'
    else:
        original = '{ // retain this comment\n "theme": "fixture",\n "mcpServers": {"other": {"command": "/bin/other"},},\n}\n'
    path.write_text(original)
    result = register(client)
    assert result.status == "added"
    assert result.backup.read_text() == original
    assert stat.S_IMODE(result.backup.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    updated = path.read_text()
    assert "retain this comment" in updated
    parsed = tomlkit.parse(updated) if spec.format == "toml" else json_document(updated)[0]
    assert parsed[spec.section]["other"]["command"] == "/bin/other"
    assert parsed.get("model", parsed.get("theme")) == "fixture"
    if client == "cursor":
        assert parsed[spec.section]["AnyMessage"]["type"] == "stdio"
    before = path.stat().st_mtime_ns
    assert register(client).status == "unchanged"
    assert path.read_text() == updated
    assert path.stat().st_mtime_ns == before
    assert len(list(path.parent.glob(path.name + ".backup-*"))) == 1


def test_conflicting_entry_requires_replace(client_home):
    path = CLIENTS["codex"].path()
    path.parent.mkdir(parents=True)
    original = '[mcp_servers.AnyMessage]\ncommand = "/old"\nargs = []\n'
    path.write_text(original)
    with pytest.raises(ValueError, match="--replace"):
        register("codex")
    assert path.read_text() == original
    result = register("codex", replace=True)
    assert result.status == "replaced"
    assert result.backup.read_text() == original
    assert tomlkit.parse(path.read_text())["mcp_servers"]["AnyMessage"]["args"] == ["mcp"]


@pytest.mark.parametrize("original", ['{"mcpServers": 1}', '{"mcpServers":{},"mcpServers":{}}', '{"a":1 2}', '{"mcpServers":{}, /* unfinished'])
def test_invalid_client_config_is_unchanged(client_home, original):
    path = CLIENTS["cursor"].path()
    path.parent.mkdir(parents=True)
    path.write_text(original)
    with pytest.raises(ValueError):
        register("cursor")
    assert path.read_text() == original
    assert not list(path.parent.glob("*.backup-*"))


def test_json_comment_and_trailing_comma_preservation_on_replacement(client_home):
    path = CLIENTS["cursor"].path()
    path.parent.mkdir(parents=True)
    original = '{"mcpServers": { // before\n"AnyMessage": {"command":"old"}, /* after */\n"other": {"command":"keep"},}, "trailing":true}\n'
    path.write_text(original)
    register("cursor", replace=True)
    updated = path.read_text()
    assert "// before" in updated and "/* after */" in updated
    assert '"other": {"command":"keep"}' in updated
    assert '"trailing":true' in updated
    assert json_document(updated)[0]["mcpServers"]["AnyMessage"]["args"] == ["mcp"]


def test_registration_uses_invoked_symlink_not_other_path_install(client_home, monkeypatch):
    monkeypatch.setattr("anymessage.clients.shutil.which", lambda _: "/other/bin/anymessage")
    entry = server_entry()
    assert entry["command"] == "/absolute/path/bin/anymessage"
    assert server_entry(command="/chosen/bin/anymessage")["command"] == "/chosen/bin/anymessage"
    assert entry["env"]["ANYMESSAGE_CONFIG_FILE"] == str(Settings.config_path())


def test_setup_reuses_keys_registers_codex_and_doctor_reads_no_messages(source, client_home, capsys, monkeypatch):
    reader, _, manifest, _ = source
    monkeypatch.setattr(cli, "capture", lambda *_: pytest.fail("Ready keys must not trigger capture"))
    monkeypatch.setattr(WeChat, "get_messages", lambda *_: pytest.fail("Doctor must not read messages"))
    assert cli.main(["setup", "--non-interactive", "--keys-file", str(manifest)]) == 0
    assert Settings.config_path().exists()
    assert CLIENTS["codex"].path().is_file()
    assert cli.main(["setup", "--non-interactive"]) == 0
    capsys.readouterr()
    assert cli.main(["doctor", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ready"] and report["source"]["schema_verified"]
    assert "enc_key" not in json.dumps(report)
    assert cli.main(["--status"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "ready"


def test_setup_does_not_save_or_capture_without_acknowledgment(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ANYMESSAGE_DATA_DIR", str(tmp_path / "missing"))
    monkeypatch.setattr(cli, "capture", lambda *_: pytest.fail("No capture without consent"))
    assert cli.main(["setup", "--non-interactive", "--no-register"]) == 2
    assert not Settings.config_path().exists()
    assert "--acknowledge-debug-copy" in capsys.readouterr().err


def test_setup_does_not_replace_existing_references(source, client_home, tmp_path):
    _, _, manifest, _ = source
    assert cli.main(["setup", "--non-interactive", "--no-register"]) == 0
    config = Settings.config_path()
    previous = config.read_bytes()
    second = tmp_path / "other-manifest.json"
    second.write_bytes(manifest.read_bytes())
    second.chmod(0o600)
    assert cli.main(["setup", "--non-interactive", "--no-register", "--keys-file", str(second)]) == 2
    assert config.read_bytes() == previous
    assert cli.main(["setup", "--non-interactive", "--no-register", "--keys-file", str(second), "--replace"]) == 0


def test_capture_readiness_is_required_even_after_zero_exit(source, client_home, monkeypatch):
    settings = Settings.load()
    real_doctor = cli.doctor
    calls = []
    def not_ready(candidate):
        report = real_doctor(candidate)
        return replace(report, ready=False, source={**report.source, "state": "key_missing"})
    monkeypatch.setattr(cli, "doctor", not_ready)
    monkeypatch.setattr(cli, "capture", lambda *args: calls.append(args) or settings)
    assert cli.main(["setup", "--non-interactive", "--acknowledge-debug-copy", "--no-register"]) == 2
    assert len(calls) == 1
    assert not Settings.config_path().exists()


def test_capture_filters_secret_events_and_manual_restore_fails(source, tmp_path, monkeypatch, capsys):
    settings = Settings.load()
    report = cli.DoctorReport(False, {}, {"bootstrap_fixture": True, "wechat_installed": True, "sqlcipher": True}, [])
    commands = []
    class FakeProcess:
        stdout = io.StringIO('\n'.join(json.dumps(item) for item in [
            {"type": "copy_prepared", "enc_key": "fixture-secret"},
            {"type": "waiting", "keys": "fixture-secret"},
            {"type": "unknown", "secret": "fixture-secret"},
            {"type": "manual_restore_required", "reason": "fixture-secret"},
        ]))
        def wait(self):
            return 0
    monkeypatch.setattr(cli.os, "geteuid", lambda: 501)
    monkeypatch.setattr(cli.subprocess, "Popen", lambda command, **_: commands.append(command) or FakeProcess())
    state = tmp_path / "new-private-state"
    with pytest.raises(ValueError, match="manual_restore_required"):
        cli.capture(settings, report, state)
    output = capsys.readouterr().err
    assert "fixture-secret" not in output
    assert "phone" in output
    assert "--db-root" in commands[0]
    assert str(WeChat(settings).selected_root()) in commands[0]
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    with pytest.raises(FileExistsError):
        cli.capture(settings, report, state)


def test_doctor_never_calls_lldb_without_developer_tools(source, monkeypatch):
    calls = []
    original_run = cli.subprocess.run
    def run(command, **kwargs):
        calls.append(command)
        if command[0] == "/usr/bin/xcode-select":
            return cli.subprocess.CompletedProcess(command, 1)
        return original_run(command, **kwargs)
    monkeypatch.setattr(cli.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(cli.subprocess, "run", run)
    assert not cli.doctor(Settings.load()).checks["bootstrap_lldb"]
    assert not any(command[0] == "/usr/bin/lldb" for command in calls)


def test_manual_configs_and_hosted_client_guidance(client_home, capsys):
    assert json.loads(configuration("json"))["mcpServers"]["AnyMessage"]["args"] == ["mcp"]
    assert tomlkit.parse(configuration("toml"))["mcp_servers"]["AnyMessage"]["command"] == "/absolute/path/bin/anymessage"
    assert json.loads(configuration("json", server_only=True))["command"] == "/absolute/path/bin/anymessage"
    assert cli.main(["mcp", "config", "--client", "grokbot"]) == 2
    assert "Grok Bot's computer" in capsys.readouterr().err
    assert cli.main(["mcp", "add", "grokbot"]) == 2
    assert not (client_home / ".grok").exists()
