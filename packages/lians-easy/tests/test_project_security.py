from __future__ import annotations

from pathlib import Path

import pytest

from lians_easy.project import detect_project


def _repo(tmp_path: Path, remote: str) -> Path:
    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "config").write_text(
        f'[remote "origin"]\n\turl = {remote}\n', encoding="utf-8"
    )
    return root


def test_origin_drops_https_credentials_query_and_fragment(tmp_path, monkeypatch):
    root = _repo(
        tmp_path,
        "https://build-user:top-secret@example.com/Team/Project.git?token=hidden#fragment",
    )

    monkeypatch.chdir(root)
    project = detect_project(root)

    assert project.origin == "example.com/team/project"
    assert "secret" not in project.id
    assert "token" not in project.id


def test_origin_drops_ssh_user_information(tmp_path, monkeypatch):
    root = _repo(tmp_path, "ssh://deploy-key@example.com:2222/Team/Project.git")

    monkeypatch.chdir(root)
    project = detect_project(root)

    assert project.origin == "example.com:2222/team/project"


def test_local_remote_path_is_not_exposed_as_public_origin(tmp_path, monkeypatch):
    root = _repo(tmp_path, str(tmp_path / "private" / "upstream.git"))

    monkeypatch.chdir(root)
    project = detect_project(root)

    assert project.origin is None
    assert project.name == "project"


def test_unverified_gitdir_pointer_is_not_followed(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "private-metadata"
    outside.mkdir()
    (outside / "config").write_text(
        '[remote "origin"]\n\turl = https://user:secret@example.com/private/repo.git\n',
        encoding="utf-8",
    )
    (root / ".git").write_text(f"gitdir: {outside}\n", encoding="utf-8")

    monkeypatch.chdir(root)
    project = detect_project(root)

    assert project.origin is None
    assert project.root == str(root)


def test_verified_linked_worktree_uses_common_git_config(tmp_path, monkeypatch):
    common = tmp_path / "source" / ".git"
    git_dir = common / "worktrees" / "preview"
    git_dir.mkdir(parents=True)
    (git_dir / "commondir").write_text("../..\n", encoding="utf-8")
    (common / "config").write_text(
        '[remote "origin"]\n\turl = git@github.com:Lians-ai/Lians.git\n',
        encoding="utf-8",
    )
    root = tmp_path / "preview"
    root.mkdir()
    (root / ".git").write_text(f"gitdir: {git_dir}\n", encoding="utf-8")

    monkeypatch.chdir(root)
    project = detect_project(root)

    assert project.origin == "github.com/lians-ai/lians"


@pytest.mark.parametrize("value", ["../outside", "bad\x00path"])
def test_project_hints_outside_the_launched_workspace_fail_closed(tmp_path, monkeypatch, value):
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.chdir(root)
    candidate = root / value if "\x00" not in value else value

    with pytest.raises(ValueError, match="project path must"):
        detect_project(candidate)


def test_project_hint_inside_workspace_never_changes_the_trusted_root(tmp_path, monkeypatch):
    root = tmp_path / "project"
    nested = root / "src" / "api"
    nested.mkdir(parents=True)
    monkeypatch.chdir(root)

    project = detect_project(nested)

    assert project.root == str(root)
