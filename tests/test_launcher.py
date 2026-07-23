import pytest

from csm.config import Config
from csm.launcher import (LaunchError, SAFE_READERS, TERMINAL_WHITELIST,
                          build_open_command, build_read_command,
                          build_resume_command, open_terminal, pick_terminal,
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


# --- native Windows (wt / powershell) --------------------------------------

def test_native_windows_prefers_wt(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("shutil.which", lambda name: "C:\\wt.exe" if name == "wt" else None)
    cfg = Config()  # auto
    assert pick_terminal(cfg) == "wt"
    argv = build_open_command(str(tmp_path), cfg)
    assert argv == ["wt.exe", "-d", str(tmp_path.resolve())]


def test_native_windows_falls_back_to_powershell(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("shutil.which", lambda name: "C:\\powershell.exe" if name == "powershell" else None)
    cfg = Config()
    assert pick_terminal(cfg) == "powershell"
    argv = build_resume_command("abc-123", str(tmp_path), cfg)
    assert argv[0] == "powershell.exe"
    assert "claude --resume 'abc-123'" in argv[-1]


def test_native_windows_falls_back_to_cmd_when_no_wt_or_powershell(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("shutil.which", lambda name: "C:\\Windows\\cmd.exe" if name == "cmd" else None)
    cfg = Config()
    assert pick_terminal(cfg) == "cmd"
    argv = build_resume_command("abc-123", str(tmp_path), cfg)
    assert argv == ["cmd.exe", "/K", "claude --resume abc-123"]
    assert all(";" not in part and "&&" not in part for part in argv)


def test_native_windows_prefers_wt_over_powershell_and_cmd(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("shutil.which", lambda name: "C:\\x\\" + name if name in ("wt", "powershell", "cmd") else None)
    cfg = Config()
    assert pick_terminal(cfg) == "wt"


def test_native_windows_no_terminal_found(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("shutil.which", lambda name: None)
    cfg = Config()
    assert pick_terminal(cfg) is None
    with pytest.raises(LaunchError):
        build_open_command(str(tmp_path), cfg)


# --- WSL (bounce to a real Windows Terminal window) ------------------------

def test_wsl_bounces_to_windows_terminal(tmp_path, monkeypatch):
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("shutil.which", lambda name: "/mnt/c/x/" + name if name in ("wt.exe", "wsl.exe") else None)
    cfg = Config()
    assert pick_terminal(cfg) == "wt-wsl"
    argv = build_resume_command("abc-123", str(tmp_path), cfg)
    assert argv == ["wt.exe", "wsl.exe", "-d", "Ubuntu", "--cd", str(tmp_path.resolve()),
                    "--", "claude", "--resume", "abc-123"]


def test_wsl_without_distro_name_falls_back_to_linux_terminal(tmp_path, monkeypatch):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.setattr("csm.launcher._in_wsl", lambda: True)
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/xterm" if name == "xterm" else None)
    cfg = Config()
    assert pick_terminal(cfg) == "xterm"


def test_wsl_without_windows_terminal_installed_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setattr("sys.platform", "linux")
    # wt.exe/wsl.exe not on PATH (interop disabled or plain WSL1 without it) —
    # but a real Linux GUI terminal is available (e.g. WSLg + xterm installed).
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/xterm" if name == "xterm" else None)
    cfg = Config()
    assert pick_terminal(cfg) == "xterm"


def test_wt_wsl_explicit_without_distro_name_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "/mnt/c/x/" + name)
    cfg = Config(terminal_launcher="wt-wsl")
    with pytest.raises(LaunchError):
        build_open_command(str(tmp_path), cfg)


def test_resume_and_open_argvs_are_argv_not_shell_for_wt(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("shutil.which", lambda name: "wt.exe" if name == "wt" else None)
    cfg = Config()
    argv = build_resume_command("abc-123", str(tmp_path), cfg)
    assert all(";" not in part and "&&" not in part for part in argv)
