import pytest

from csm.config import Config
from csm.launcher import (LaunchError, SAFE_READERS, TERMINAL_WHITELIST,
                          build_open_command, build_read_command, open_terminal,
                          read_file_preview)


def test_open_requires_existing_dir(tmp_path):
    cfg = Config(terminal_launcher="xterm")
    with pytest.raises(LaunchError):
        build_open_command(str(tmp_path / "missing"), cfg)


def test_non_whitelisted_launcher_rejected(tmp_path):
    cfg = Config(terminal_launcher="rm -rf")
    with pytest.raises(LaunchError):
        build_open_command(str(tmp_path), cfg)


def test_open_command_is_argv_not_shell(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    cfg = Config(terminal_launcher="gnome-terminal")
    argv = build_open_command(str(tmp_path), cfg)
    assert argv[0] == "gnome-terminal"
    assert all(";" not in part and "&&" not in part for part in argv)


def test_dry_run_does_not_spawn(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    spawned = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: spawned.append(a))
    cfg = Config(terminal_launcher="xterm")
    open_terminal(str(tmp_path), cfg, dry_run=True)
    assert spawned == []


def test_read_command_whitelist(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("hi")
    with pytest.raises(LaunchError):
        build_read_command(str(f), Config(safe_read_command="bash"))
    with pytest.raises(LaunchError):
        build_read_command(str(tmp_path / "missing.md"), Config())


def test_read_command_ok(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("hi")
    argv = build_read_command(str(f), Config(safe_read_command="cat"))
    assert argv == ["cat", str(f)]


def test_preview_truncates(tmp_path):
    f = tmp_path / "big.md"
    f.write_text("A" * 500)
    out = read_file_preview(str(f), max_bytes=100)
    assert out.startswith("A" * 100)
    assert "truncated" in out


def test_whitelists_contain_no_shells():
    assert "bash" not in SAFE_READERS and "sh" not in SAFE_READERS
    for argv in TERMINAL_WHITELIST.values():
        assert argv[0] != "sh" and argv[0] != "bash"
