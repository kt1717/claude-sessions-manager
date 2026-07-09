import pytest
import yaml

from csm.config import Config, load_config, write_default_config, DEFAULT_CONFIG_YAML


def test_defaults():
    cfg = Config()
    assert cfg.require_confirmation_for_open is True
    assert cfg.safe_read_command == "less"
    assert any("projects" in p for p in cfg.scan_paths)


def test_load_missing_file(tmp_path):
    cfg = load_config(tmp_path / "nope.yaml")
    assert isinstance(cfg, Config)


def test_load_overrides(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("refresh_interval_seconds: 9\nscan_paths: ['/x']\nunknown_key: 1\n")
    cfg = load_config(p)
    assert cfg.refresh_interval_seconds == 9
    assert cfg.scan_paths == ["/x"]


def test_load_rejects_non_mapping(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("- just\n- a list\n")
    with pytest.raises(ValueError):
        load_config(p)


def test_write_default_and_roundtrip(tmp_path):
    p = tmp_path / "cfg" / "config.yaml"
    write_default_config(p)
    assert p.is_file()
    cfg = load_config(p)
    assert cfg.terminal_launcher == "auto"
    with pytest.raises(FileExistsError):
        write_default_config(p)
    write_default_config(p, force=True)


def test_default_yaml_parses():
    assert isinstance(yaml.safe_load(DEFAULT_CONFIG_YAML), dict)


def test_ignored_paths(tmp_path):
    cfg = Config(ignored_paths=[str(tmp_path / "secret")])
    assert cfg.is_ignored(tmp_path / "secret" / "x.jsonl")
    assert not cfg.is_ignored(tmp_path / "open" / "x.jsonl")
