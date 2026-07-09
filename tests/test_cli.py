import json

import pytest

from csm.cli import main, _parse_since
from tests.conftest import MOCK_FILE


@pytest.fixture
def cli_config(tmp_path, monkeypatch):
    """Point the CLI at an isolated config using mock data only."""
    monkeypatch.setenv("COLUMNS", "300")  # keep rich tables from folding cells
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"scan_paths: ['{tmp_path}/scan']\nmock_data_file: '{MOCK_FILE}'\n"
    )
    return str(cfg)


def run(capsys, *argv):
    code = main(list(argv))
    return code, capsys.readouterr().out


def test_list(cli_config, capsys):
    code, out = run(capsys, "--config", cli_config, "list")
    assert code == 0
    assert "mock-aaa" in out


def test_list_filters(cli_config, capsys):
    code, out = run(capsys, "--config", cli_config, "list", "--model", "sonnet")
    assert "mock-aaa" in out
    assert "mock-ccc" not in out  # opus session filtered out


def test_tree(cli_config, capsys):
    code, out = run(capsys, "--config", cli_config, "tree")
    assert code == 0
    assert "dhcp-relay" in out


def test_detail_prefix_match(cli_config, capsys):
    code, out = run(capsys, "--config", cli_config, "detail", "mock-aaa")
    assert code == 0
    assert "claude-sonnet-5" in out
    assert "confidence" in out


def test_detail_unknown_exits(cli_config, capsys):
    with pytest.raises(SystemExit):
        main(["--config", cli_config, "detail", "nope"])


def test_export(cli_config, capsys, tmp_path):
    out_file = tmp_path / "export.json"
    code, _ = run(capsys, "--config", cli_config, "export", "-o", str(out_file))
    assert code == 0
    data = json.loads(out_file.read_text())
    assert len(data["missions"]) >= 4


def test_guide(cli_config, capsys):
    code, out = run(capsys, "--config", cli_config, "guide")
    assert code == 0
    assert "csm doctor" in out


def test_doctor_runs(cli_config, capsys):
    code, out = run(capsys, "--config", cli_config, "doctor")
    assert "doctor" in out  # exit code may be non-zero if scan path missing — that's correct


def test_config_init(tmp_path, monkeypatch, capsys):
    target = tmp_path / "conf" / "config.yaml"
    monkeypatch.setattr("csm.cli.write_default_config",
                        lambda force=False: __import__("csm.config", fromlist=["x"])
                        .write_default_config(target, force=force))
    code, out = run(capsys, "config", "init")
    assert code == 0
    assert target.is_file()


def test_open_refuses_missing_dir(cli_config, capsys):
    code, out = run(capsys, "--config", cli_config, "open", "mock-aaa", "--yes")
    assert code == 1  # /tmp/mock/dhcp-relay does not exist


def test_read_preview(cli_config, capsys, tmp_path):
    # create the markdown file referenced by mock-ccc
    from pathlib import Path
    p = Path("/tmp/mock/reports")
    p.mkdir(parents=True, exist_ok=True)
    (p / "closeout.md").write_text("# Closeout\nAll done. password=Secret99\n")
    code, out = run(capsys, "--config", cli_config, "read", "mock-ccc", "--preview")
    assert code == 0
    assert "Closeout" in out
    assert "Secret99" not in out


def test_parse_since():
    from datetime import datetime, timezone, timedelta
    assert _parse_since("2h") <= datetime.now(timezone.utc) - timedelta(hours=1, minutes=59)
    assert _parse_since("2026-01-01").year == 2026
