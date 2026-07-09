import json

from csm.config import Config
from csm.discovery.files import FilesAdapter, find_project_root, parse_transcript


def test_parse_transcript_full(transcript, tmp_path):
    cfg = Config(scan_paths=[str(tmp_path / "scan")])
    s = parse_transcript(transcript, cfg)
    assert s.id == "abc123"
    assert s.model == "claude-sonnet-5"
    assert s.git_branch == "feature/x"
    assert s.title == "Do the thing"
    assert s.cwd == str(tmp_path / "proj")
    assert s.project_root == str(tmp_path / "proj")  # .git marker
    # usage summed across assistant messages incl. cache_creation
    assert s.usage.input_tokens == 10 + 90 + 5
    assert s.usage.output_tokens == 60
    assert s.usage.total_tokens == 165
    assert s.usage.confidence == "high"
    assert s.started_at is not None and s.updated_at > s.started_at


def test_usage_unknown_when_absent(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text(json.dumps({"type": "user", "cwd": str(tmp_path),
                             "timestamp": "2026-07-08T10:00:00Z", "message": {}}))
    s = parse_transcript(f, Config())
    assert s.usage.total_tokens is None
    assert s.model is None  # never invented


def test_cost_only_with_configured_prices(transcript, tmp_path):
    cfg = Config(scan_paths=[str(tmp_path / "scan")])
    assert parse_transcript(transcript, cfg).usage.estimated_cost is None
    cfg.model_prices = {"claude-sonnet-5": {"input": 3.0, "output": 15.0}}
    s = parse_transcript(transcript, cfg)
    assert s.usage.estimated_cost is not None and s.usage.estimated_cost > 0


def test_oversize_file_skipped(tmp_path):
    f = tmp_path / "big.jsonl"
    f.write_text('{"cwd": "/x"}\n')
    cfg = Config(max_file_size_mb=0.0000001)
    s = parse_transcript(f, cfg)
    assert s.cwd is None
    assert any(e.kind == "skipped" for e in s.evidence)


def test_malformed_lines_tolerated(tmp_path):
    f = tmp_path / "bad.jsonl"
    f.write_text('not json "model"\n{"cwd": "/tmp"}\n')
    s = parse_transcript(f, Config())
    assert s.cwd == "/tmp"


def test_find_project_root(tmp_path):
    (tmp_path / "repo" / "src").mkdir(parents=True)
    (tmp_path / "repo" / "pyproject.toml").touch()
    assert find_project_root(str(tmp_path / "repo" / "src")) == str(tmp_path / "repo")
    assert find_project_root(str(tmp_path / "nonexistent-dir")) is None
    assert find_project_root(None) is None


def test_adapter_discovers_and_links_markdown(transcript, tmp_path):
    md = tmp_path / "scan" / "plan-abc123.md"
    md.write_text("# Mission plan for abc123\n")
    cfg = Config(scan_paths=[str(tmp_path / "scan")])
    sessions = FilesAdapter().discover(cfg)
    assert len(sessions) == 1
    assert sessions[0].markdown_file == str(md)
