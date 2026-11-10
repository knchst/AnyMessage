"""Onboarding, read-only diagnostics, and explicit MCP client registration."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
from importlib.metadata import version
from importlib.resources import files
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from anymessage.clients import CLIENTS, GROKBOT_GUIDANCE, configuration, register
from anymessage.settings import Settings
from anymessage.wechat import SourceError, WeChat


@dataclass(frozen=True)
class DoctorReport:
    ready: bool
    source: dict[str, Any]
    checks: dict[str, bool]
    next_steps: list[str]


def doctor(settings: Settings) -> DoctorReport:
    source = WeChat(settings).status()
    native = platform.system() == "Darwin" and platform.machine() == "arm64"
    developer_tools = False
    lldb = False
    if platform.system() == "Darwin" and Path("/usr/bin/xcode-select").exists():
        developer_tools = subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True).returncode == 0
        if developer_tools:
            lldb = subprocess.run(["/usr/bin/lldb", "-P"], capture_output=True).returncode == 0
    checks = {
        "macos": platform.system() == "Darwin",
        "sqlcipher": source["sqlcipher_available"],
        "wechat_installed": source["installed"],
        "bootstrap_native_apple_silicon": native,
        "bootstrap_developer_tools": developer_tools,
        "bootstrap_lldb": lldb,
        "bootstrap_system_python": Path("/usr/bin/python3").is_file(),
        "bootstrap_codesign": bool(shutil.which("codesign")),
        "bootstrap_ditto": bool(shutil.which("ditto")),
    }
    steps = []
    if source["state"] != "ready":
        steps.append(source.get("message", "Run anymessage setup to select an account and provide keys."))
    if not checks["sqlcipher"]:
        steps.append("Install SQLCipher with brew install sqlcipher, or correct ANYMESSAGE_SQLCIPHER or the saved sqlcipher reference to an executable command or path.")
    if not developer_tools:
        steps.append("First-time capture needs Xcode command line tools. Install them yourself with xcode-select --install, then rerun doctor.")
    elif not lldb:
        steps.append("First-time capture needs the LLDB Python module. Check /usr/bin/lldb -P.")
    return DoctorReport(source["state"] == "ready", source, checks, steps)


CAPTURE_EVENTS = {
    "copy_prepared": "In the temporary WeChat window, choose Enter Weixin. Finish phone login or approval if requested.",
    "launched": "Temporary WeChat started. Complete phone login if requested.",
    "waiting": "Waiting for verified database keys. Complete login in the temporary window.",
    "verified_progress": "Verified additional database keys. Keys remain in the private manifest.",
    "capture_finished": "Key capture finished. Checking reader readiness next.",
    "owned_process_closed": "Temporary capture process closed.",
    "original_launch_requested": "Original WeChat launch requested. Confirm that the original client is running.",
    "original_signature_unchanged": "Original signature verified unchanged. Temporary application copy disabled.",
    "keys_saved_privately": "Verified keys saved privately.",
    "manual_restore_required": "Quit the exact temporary WeChat copy normally, then open /Applications/WeChat.app yourself. Setup is incomplete.",
    "bootstrap_error": "Bootstrap failed. Check prerequisites and the original WeChat client before retrying with a new state directory.",
    "capture_error": "Capture failed. Check the original WeChat client before retrying with a new state directory.",
    "launch_failed": "Temporary WeChat could not start. Check prerequisites.",
    "process_ended": "Temporary WeChat ended before capture completed.",
}


def capture(settings: Settings, report: DoctorReport, state_dir: Path | None) -> Settings:
    required = [name for name in report.checks if name.startswith("bootstrap_")]
    if not all(report.checks[name] for name in required) or not report.checks["wechat_installed"] or not report.checks["sqlcipher"]:
        raise ValueError("First-time capture prerequisites are incomplete. Run anymessage doctor and resolve its guidance.")
    if os.geteuid() == 0:
        raise ValueError("Run setup as your ordinary macOS user, without sudo.")
    root = WeChat(settings).selected_root()
    if state_dir is None:
        parent = Settings.config_path().parent / "wechat"
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        state_dir = Path(tempfile.mkdtemp(prefix="capture-", dir=parent))
    else:
        state_dir = state_dir.expanduser().absolute()
        state_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    state_dir.chmod(0o700)
    installed = files("anymessage").joinpath("vendor/wechat-bootstrap/bootstrap_keys.py")
    script = Path(str(installed))
    if not script.is_file():
        script = Path(__file__).resolve().parents[2] / "tools/wechat-bootstrap/bootstrap_keys.py"
    if not script.is_file():
        raise ValueError("Packaged bootstrap resources are missing. Reinstall AnyMessage.")
    command = ["/usr/bin/python3", str(script), "--acknowledge-debug-copy", "--db-root", str(root), "--state-dir", str(state_dir)]
    manual_restore = False
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    assert process.stdout is not None
    for line in process.stdout:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            kind = event.get("type")
            if isinstance(kind, str) and kind in CAPTURE_EVENTS:
                print(CAPTURE_EVENTS[kind], file=sys.stderr, flush=True)
                manual_restore |= kind == "manual_restore_required"
    returncode = process.wait()
    if manual_restore:
        raise ValueError("manual_restore_required. Restore the original WeChat client before continuing. Settings were not saved.")
    if returncode:
        raise ValueError(f"Key capture did not complete successfully. Exit code {returncode}. Settings were not saved.")
    return replace(settings, keys_file=state_dir / "keys.json")


def setup(args: argparse.Namespace) -> int:
    settings = Settings.load(data_dir=args.data_dir, account=args.account, keys_file=args.keys_file, sqlcipher=args.sqlcipher)
    if settings.keys_file is None:
        standard_manifest = Settings.config_path().parent / "wechat/keys.json"
        if standard_manifest.is_file():
            settings = replace(settings, keys_file=standard_manifest)
    source = WeChat(settings)
    accounts = source.accounts()
    if len(accounts) > 1 and not settings.account:
        if args.non_interactive or not sys.stdin.isatty():
            raise ValueError("Several accounts were found. Run anymessage doctor --json for their ids, then pass --account ID.")
        print("Choose the account whose local history you want to read:", file=sys.stderr)
        for index, (account, _) in enumerate(accounts, 1):
            print(f"  {index}. {account}", file=sys.stderr)
        answer = input("Account number: ").strip()
        if not answer.isdigit() or not 1 <= int(answer) <= len(accounts):
            raise ValueError("No valid account selected. Settings were not changed.")
        settings = replace(settings, account=accounts[int(answer) - 1][0])
    report = doctor(settings)
    if not report.ready and not args.non_interactive and sys.stdin.isatty() and not args.acknowledge_debug_copy:
        existing = input("Existing key manifest path, or Enter for first-time capture: ").strip()
        if existing:
            settings = replace(settings, keys_file=Path(existing).expanduser().absolute())
            report = doctor(settings)
    if not report.ready:
        print(f"WeChat readiness: {report.source['state']}", file=sys.stderr)
        acknowledged = args.acknowledge_debug_copy
        if not acknowledged and not args.non_interactive and sys.stdin.isatty():
            print("First-time capture copies and re-signs WeChat temporarily, uses your account data, and may require phone login. Save drafts and quit WeChat normally first. The original application is not re-signed.", file=sys.stderr)
            acknowledged = input("Start temporary-copy key capture? [y/N] ").strip().lower() in ("y", "yes")
        if not acknowledged:
            raise ValueError("Supply valid --keys-file PATH, or read docs/wechat-setup.md and rerun with --acknowledge-debug-copy after saving drafts and quitting WeChat. Settings were not saved.")
        settings = capture(settings, report, args.state_dir)
        report = doctor(settings)
        if not report.ready:
            raise ValueError(f"Capture finished but reader readiness is {report.source['state']}. Settings were not saved. Run doctor with the captured manifest using ANYMESSAGE_KEYS_FILE.")
    config = Settings.config_path()
    previous = json.loads(config.read_text()) if config.exists() else None
    payload = {"version": 1, **{key: str(value) if isinstance(value, Path) else value for key, value in asdict(settings).items()}}
    if previous is not None and previous != payload and not args.replace:
        raise ValueError("A different AnyMessage configuration already exists. Inspect it, then pass --replace to replace its references. Keys are never overwritten.")
    if previous != payload:
        settings.save()
    print(f"WeChat is ready. Configuration references saved in {config}.")
    for client in ([] if args.no_register else args.client or ["codex"]):
        result = register(client, replace=args.replace, command=args.command)
        print(f"{client}: {result.status} in {result.path}.")
        if result.backup:
            print(f"Private backup: {result.backup}")
    print("Restart or reload the client's MCP connection, then call status.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only local macOS messages through MCP")
    parser.add_argument("--version", action="version", version="%(prog)s " + version("anymessage"))
    parser.add_argument("--status", action="store_true", help="Print source readiness (legacy compatibility)")
    commands = parser.add_subparsers(dest="command_name")
    onboarding = commands.add_parser("setup", help="Configure WeChat and register Codex by default")
    onboarding.add_argument("--keys-file", type=Path)
    onboarding.add_argument("--data-dir", type=Path)
    onboarding.add_argument("--account")
    onboarding.add_argument("--sqlcipher")
    onboarding.add_argument("--client", action="append", choices=CLIENTS, help="Repeat for each local client; default codex")
    onboarding.add_argument("--no-register", action="store_true")
    onboarding.add_argument("--non-interactive", action="store_true")
    onboarding.add_argument("--acknowledge-debug-copy", action="store_true", help="Explicit consent to first-time temporary-copy capture")
    onboarding.add_argument("--state-dir", type=Path, help="New private directory for capture; must not exist")
    onboarding.add_argument("--replace", action="store_true", help="Replace conflicting saved references or AnyMessage client entries")
    onboarding.add_argument("--command", help="Absolute AnyMessage executable path for client registration")
    diagnostic = commands.add_parser("doctor", help="Inspect readiness and prerequisites without reading messages")
    diagnostic.add_argument("--json", action="store_true")
    demo = commands.add_parser("demo", help="Try fictional project messages and attachments without WeChat or account keys")
    demo.add_argument("--mcp", action="store_true", help="Serve the fictional dataset over stdio MCP for your AI client")
    mcp = commands.add_parser("mcp", help="Run the MCP server or manage local clients")
    mcp.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    mcp.add_argument("--port", type=int, default=8765, help="Loopback HTTP port; default 8765")
    mcp.add_argument("--telegram-export", type=Path, help="Read one Telegram Desktop JSON export and local attachments instead of WeChat; no keys")
    actions = mcp.add_subparsers(dest="mcp_action")
    add = actions.add_parser("add", help="Register a local client")
    add.add_argument("client", choices=[*CLIENTS, "grokbot"])
    add.add_argument("--replace", action="store_true")
    add.add_argument("--command")
    config = actions.add_parser("config", help="Print a manual stdio MCP configuration")
    config.add_argument("--format", choices=("json", "toml"), default="json")
    config.add_argument("--client", choices=[*CLIENTS, "grokbot"])
    config.add_argument("--server-only", action="store_true", help="Print the server object without an mcpServers wrapper")
    config.add_argument("--command")
    args = parser.parse_args(argv)
    if args.status and args.command_name:
        parser.error("--status cannot be combined with a subcommand")
    if args.command_name == "mcp" and args.telegram_export is not None and args.mcp_action is not None:
        parser.error("--telegram-export selects a running server, not mcp add/config. Use the manual Telegram MCP configuration in docs/telegram-export.md.")
    try:
        if args.status:
            status = WeChat().status()
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 0
        if args.command_name == "setup":
            return setup(args)
        if args.command_name == "doctor":
            report = doctor(Settings.load())
            if args.json:
                print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
            else:
                print(f"WeChat: {report.source['state']}")
                for name, passed in report.checks.items():
                    print(f"{name}: {'ok' if passed else 'missing'}")
                for step in report.next_steps:
                    print(step)
            return 0 if report.ready else 1
        if args.command_name == "demo":
            from anymessage.demo import demo_settings, print_demo_evidence

            if args.mcp:
                from anymessage.server import create_server

                with demo_settings() as settings:
                    create_server(demo_settings=settings).run(transport="stdio")
            else:
                import asyncio

                asyncio.run(print_demo_evidence())
            return 0
        if args.command_name == "mcp" and args.mcp_action == "config":
            print(configuration(args.format, client=args.client, command=args.command, server_only=args.server_only), end="")
            return 0
        if args.command_name == "mcp" and args.mcp_action == "add":
            if args.client == "grokbot":
                raise ValueError(GROKBOT_GUIDANCE)
            result = register(args.client, replace=args.replace, command=args.command)
            print(f"{result.client}: {result.status} in {result.path}.")
            if result.backup:
                print(f"Private backup: {result.backup}")
            print("Restart or reload the client's MCP connection, then call status.")
            return 0
        from anymessage.server import create_server
        transport = args.transport if args.command_name == "mcp" else "stdio"
        port = args.port if args.command_name == "mcp" else 8765
        if not 1 <= port <= 65535:
            parser.error("--port must be between 1 and 65535")
        export = args.telegram_export if args.command_name == "mcp" else None
        create_server(port=port, telegram_export=export).run(transport=transport)
        return 0
    except (OSError, ValueError, SourceError, subprocess.SubprocessError) as error:
        if isinstance(error, json.JSONDecodeError):
            message = "A configuration file contains invalid JSON. Inspect it before retrying."
        elif isinstance(error, OSError):
            message = "A local file or executable could not be accessed. Check paths and permissions."
        else:
            message = str(error)
        print("anymessage: " + message, file=sys.stderr)
        return 2
