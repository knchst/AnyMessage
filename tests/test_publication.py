from __future__ import annotations

import json
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile

import pytest


SCRIPT = Path(__file__).resolve().parent.parent / "scripts/check_publication.py"


@pytest.fixture
def publication_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "public.txt").write_text("Synthetic public source\n")
    return tmp_path


def audit(root, *arguments):
    return subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), *arguments], capture_output=True, text=True)


def test_clean_publication_tree(publication_repo):
    result = audit(publication_repo)
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize(("name", "content", "reason"), [
    ("config.txt", b"/" + b"Users/private-person/project/", "personal absolute home path"),
    ("account.txt", b"wxid_" + b"a1b2c3d4e5f6", "non-fixture WeChat account identifier"),
    ("history.db", b"fixture", "local database"),
    ("material.json", json.dumps({"keys": {"message/message_0.db": {"enc_key": "a" * 64}}}).encode(), "SQLCipher key manifest"),
    ("material.txt", b"-----BEGIN " + b"PRIVATE KEY-----", "private key material"),
])
def test_publication_negative_controls(publication_repo, name, content, reason):
    (publication_repo / name).write_bytes(content)
    result = audit(publication_repo)
    assert result.returncode == 1
    assert reason in result.stdout


@pytest.mark.parametrize("worktree", ["missing", "clean"])
def test_staged_private_material_is_not_hidden_by_worktree(publication_repo, worktree):
    pending = publication_repo / "pending.txt"
    pending.write_bytes(b"/" + b"Users/private-person/data/")
    subprocess.run(["git", "-C", str(publication_repo), "add", "pending.txt"], check=True)
    before = subprocess.run(["git", "-C", str(publication_repo), "ls-files", "--stage", "-z"], check=True, capture_output=True).stdout
    if worktree == "missing":
        pending.unlink()
    else:
        pending.write_text("Sanitized public content\n")
    result = audit(publication_repo)
    assert result.returncode == 1
    assert "index:pending.txt: personal absolute home path" in result.stdout
    after = subprocess.run(["git", "-C", str(publication_repo), "ls-files", "--stage", "-z"], check=True, capture_output=True).stdout
    assert before == after
    if worktree == "missing":
        assert not pending.exists()
    else:
        assert pending.read_text() == "Sanitized public content\n"


@pytest.mark.parametrize("format", ["wheel", "sdist"])
def test_archive_members_are_audited(publication_repo, tmp_path, format):
    archive = tmp_path / ("package.whl" if format == "wheel" else "package.tar.gz")
    name = "anymessage/private/history.db"
    if format == "wheel":
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr(name, b"synthetic")
    else:
        with tarfile.open(archive, "w:gz") as output:
            member = tarfile.TarInfo(name)
            member.size = len(b"synthetic")
            output.addfile(member, io.BytesIO(b"synthetic"))
    result = audit(publication_repo, "--archive", str(archive))
    assert result.returncode == 1
    assert "history.db" in result.stdout


def commit_fixture(root):
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_AUTHOR_NAME": "Audit Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
           "GIT_COMMITTER_NAME": "Audit Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
    subprocess.run(["git", "-C", str(root), "add", "."], env=env, check=True)
    subprocess.run(["git", "-C", str(root), "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", "commit", "-qm", "Synthetic audit fixture"], env=env, check=True)


def test_history_check_finds_removed_private_material(publication_repo):
    private = publication_repo / "config.txt"
    private.write_bytes(b"/" + b"home/private-person/data/")
    commit_fixture(publication_repo)
    private.unlink()
    commit_fixture(publication_repo)
    assert audit(publication_repo).returncode == 0
    result = audit(publication_repo, "--history")
    assert result.returncode == 1
    assert "commit " in result.stdout and "personal absolute home path" in result.stdout


def test_noreply_policy_is_explicit(publication_repo):
    commit_fixture(publication_repo)
    assert audit(publication_repo, "--history").returncode == 0
    result = audit(publication_repo, "--history", "--require-noreply")
    assert result.returncode == 1
    assert "author or committer email" in result.stdout
