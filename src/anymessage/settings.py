"""Local configuration references shared by onboarding and the reader."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shutil
import tempfile


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    account: str | None
    keys_file: Path | None
    sqlcipher: str | None

    @staticmethod
    def config_path() -> Path:
        return Path(os.environ.get("ANYMESSAGE_CONFIG_FILE", str(Path.home() / "Library/Application Support/AnyMessage/config.json"))).expanduser().absolute()

    @classmethod
    def load(cls, *, data_dir: Path | None = None, account: str | None = None, keys_file: Path | None = None, sqlcipher: str | None = None) -> Settings:
        saved = {}
        config = cls.config_path()
        if config.is_file():
            saved = json.loads(config.read_text())
            if not isinstance(saved, dict) or saved.get("version") != 1:
                raise ValueError("Invalid AnyMessage configuration. Expected a version 1 object.")
            for field in ("data_dir", "account", "keys_file", "sqlcipher"):
                if saved.get(field) is not None and not isinstance(saved[field], str):
                    raise ValueError(f"Invalid AnyMessage configuration field: {field}.")
        root = data_dir or os.environ.get("ANYMESSAGE_DATA_DIR") or saved.get("data_dir") or Path.home() / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
        key = keys_file or os.environ.get("ANYMESSAGE_KEYS_FILE") or saved.get("keys_file")
        selected = sqlcipher or os.environ.get("ANYMESSAGE_SQLCIPHER") or saved.get("sqlcipher")
        if selected:
            executable = shutil.which(os.path.expanduser(selected))
        else:
            executable = shutil.which("sqlcipher") or next((found for path in (
                "/opt/homebrew/opt/sqlcipher/bin/sqlcipher", "/usr/local/opt/sqlcipher/bin/sqlcipher",
            ) if (found := shutil.which(path))), None)
        if executable:
            executable = os.path.abspath(executable)
        return cls(Path(root).expanduser().resolve(), account or os.environ.get("ANYMESSAGE_ACCOUNT") or saved.get("account"), Path(key).expanduser().absolute() if key else None, executable)

    def save(self) -> None:
        payload = {"version": 1, **{key: str(value) if isinstance(value, Path) else value for key, value in asdict(self).items()}}
        private_write(self.config_path(), json.dumps(payload, indent=2) + "\n")


def private_write(path: Path, text: str) -> None:
    if path.is_symlink():
        raise ValueError(f"Refusing to replace symlink configuration {path}. Edit its managed target manually.")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
