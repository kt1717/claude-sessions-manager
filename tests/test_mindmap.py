"""Tests for mindmap backend: todos, subagents, labels, rename & resume."""
import json

import pytest
from fastapi.testclient import TestClient

from csm.config import Config
from csm.discovery import build_snapshot
from csm.discovery.files import FilesAdapter, parse_transcript
from csm.labels import (LabelError, load_labels, sanitize_name, set_archived,
                        set_mission_label, set_session_label)
from csm.launcher import LaunchError, build_resume_command, resume_session
from csm.server import create_app


def _write_transcript_with_subagents(tmp_path):
    """A transcript with two TodoWrite calls + two externalized subagents."""
    scan = tmp_path / "scan" / "-home-user-proj"
    scan.mkdir(parents=True)
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    sid = "sess-001"
    lines = [
        {"type": "user", "cwd": str(proj), "gitBranch": "main",
         "sessionId": sid, "timestamp": "2026-07-08T10:00:00Z",
         "message": {"role": "user", "content": "go"}},
        # first TodoWrite (should be superseded by the later one)
        {"type": "assistant", "timestamp": "2026-07-08T10:01:00Z",
         "message": {"role": "assistant", "model": "claude-sonnet-5",
                     "usage": {"input_tokens": 10, "output_tokens": 5},
                     "content": [{"type": "tool_use", "name": "TodoWrite", "id": "t1",
                                  "input": {"todos": [
                                      {"content": "old", "activeForm": "old",
                                       "status": "pending"}]}}]}},
        # spawn a subagent
        {"type": "assistant", "timestamp": "2026-07-08T10:02:00Z",
         "message": {"role": "assistant", "model": "claude-sonnet-5",
                     "content": [{"type": "tool_use", "name": "Agent",
                                  "id": "toolu_ABC",
                                  "input": {"description": "find files"}}]}},
        # latest TodoWrite wins
        {"type": "assistant", "timestamp": "2026-07-08T10:03:00Z",
         "message": {"role": "assistant", "model": "claude-sonnet-5",
                     "usage": {"input_tokens": 20, "output_tokens": 8},
                     "content": [{"type": "tool_use", "name": "TodoWrite", "id": "t2",
                                  "input": {"todos": [
                                      {"content": "a", "activeForm": "doing a",
                                       "status": "completed"},
                                      {"content": "b", "activeForm": "doing b",
                                       "status": "in_progress"},
                                      {"content": "c", "activeForm": "doing c",
                                       "status": "pending"}]}}]}},
    ]
    f = scan / f"{sid}.jsonl"
    f.write_text("\n".join(json.dumps(x) for x in lines))

    subdir = scan / sid / "subagents"
    subdir.mkdir(parents=True)
    # subagent 1 (authoritative edge -> toolu_ABC)
    (subdir / "agent-aaa111.meta.json").write_text(json.dumps({
        "agentType": "Explore", "description": "find files",
        "toolUseId": "toolu_ABC", "spawnDepth": 1}))
    (subdir / "agent-aaa111.jsonl").write_text("\n".join(json.dumps(x) for x in [
        {"isSidechain": True, "agentId": "aaa111", "sessionId": sid,
         "message": {"role": "assistant", "model": "claude-opus-4-8",
                     "usage": {"input_tokens": 100,
                               "cache_creation_input_tokens": 50,
                               "cache_read_input_tokens": 25, "output_tokens": 30}}},
    ]))
    # subagent 2, plus a bogus meta to prove malformed json is skipped
    (subdir / "agent-bbb222.meta.json").write_text(json.dumps({
        "agentType": "general-purpose", "description": "do work",
        "toolUseId": "toolu_XYZ", "spawnDepth": 2}))
    (subdir / "agent-bbb222.jsonl").write_text(json.dumps(
        {"message": {"role": "assistant", "model": "claude-sonnet-5",
                     "usage": {"input_tokens": 1, "output_tokens": 1}}}))
    (subdir / "agent-broken.meta.json").write_text("{ not json")
    return f, proj, sid


def test_parse_todos_and_progress(tmp_path):
    f, _, _ = _write_transcript_with_subagents(tmp_path)
    s = parse_transcript(f, Config(scan_paths=[str(tmp_path / "scan")]))
    # latest TodoWrite replaced the earlier one
    assert [t.content for t in s.todos] == ["a", "b", "c"]
    assert s.todos[0].activeForm == "doing a"
    assert s.progress.completed == 1
    assert s.progress.in_progress == 1
    assert s.progress.pending == 1
    assert s.progress.total == 3


def test_parse_subagents(tmp_path):
    f, _, _ = _write_transcript_with_subagents(tmp_path)
    s = parse_transcript(f, Config(scan_paths=[str(tmp_path / "scan")]))
    by_id = {a.agent_id: a for a in s.subagents}
    # the broken meta.json is skipped, not fatal
    assert set(by_id) == {"aaa111", "bbb222"}
    a = by_id["aaa111"]
    assert a.agent_type == "Explore"
    assert a.tool_use_id == "toolu_ABC"
    assert a.spawn_depth == 1
    assert a.model == "claude-opus-4-8"
    assert a.total_tokens == 100 + 50 + 25 + 30  # all four usage components
    assert by_id["bbb222"].spawn_depth == 2


def test_missing_subagents_dir_is_empty(transcript, tmp_path):
    s = parse_transcript(transcript, Config(scan_paths=[str(tmp_path / "scan")]))
    assert s.subagents == []
    assert s.todos == []
    assert s.progress is None


def test_subagent_transcripts_not_double_counted_as_sessions(tmp_path):
    # Regression: externalized subagent transcripts under <sid>/subagents/ (and
    # nested .../subagents/workflows/wf_.../) must NOT surface as independent
    # top-level "agent-<hash>" sessions — they're already represented via the
    # parent session's `subagents` list. Previously `rglob("*.jsonl")` swept
    # them up as bogus sessions, often mis-grouped under whatever cwd a tool
    # call happened to run in (e.g. dumped into the user's home-dir mission).
    f, _, sid = _write_transcript_with_subagents(tmp_path)
    cfg = Config(scan_paths=[str(tmp_path / "scan")])
    sessions = FilesAdapter().discover(cfg)
    ids = {s.id for s in sessions}
    assert ids == {sid}  # only the real session, none of its subagent files
    assert not any(i.startswith("agent-") for i in ids)


def test_sanitize_name_rules():
    assert sanitize_name("  hello world  ") == "hello world"
    assert sanitize_name("line1\nline2\t!") == "line1line2!"  # control chars stripped
    with pytest.raises(LabelError):
        sanitize_name("   ")
    with pytest.raises(LabelError):
        sanitize_name("x" * 201)


def test_labels_roundtrip(tmp_path):
    cfg = Config(labels_file=str(tmp_path / "labels.json"))
    assert load_labels(cfg) == {"sessions": {}, "missions": {}, "archived": []}
    set_session_label(cfg, "sid-1", " My Task ")
    set_mission_label(cfg, "/home/x/proj", "Cool Mission")
    data = load_labels(cfg)
    assert data["sessions"]["sid-1"] == "My Task"
    assert data["missions"]["/home/x/proj"] == "Cool Mission"


def test_archive_roundtrip(tmp_path):
    cfg = Config(labels_file=str(tmp_path / "labels.json"))
    assert load_labels(cfg)["archived"] == []
    set_archived(cfg, "sid-1", True)
    assert load_labels(cfg)["archived"] == ["sid-1"]
    # archiving twice is idempotent
    set_archived(cfg, "sid-1", True)
    assert load_labels(cfg)["archived"] == ["sid-1"]
    set_archived(cfg, "sid-1", False)
    assert load_labels(cfg)["archived"] == []
    # unarchiving something never archived is a no-op, not an error
    set_archived(cfg, "never-archived", False)
    assert load_labels(cfg)["archived"] == []


def test_archived_flag_in_snapshot(tmp_path):
    f, proj, sid = _write_transcript_with_subagents(tmp_path)
    cfg = Config(scan_paths=[str(tmp_path / "scan")],
                 labels_file=str(tmp_path / "labels.json"), mock_data_file="")
    snap = build_snapshot(cfg, include_processes=False)
    assert snap.find_session(sid).archived is False
    set_archived(cfg, sid, True)
    snap2 = build_snapshot(cfg, include_processes=False)
    assert snap2.find_session(sid).archived is True


def test_label_override_in_snapshot(tmp_path):
    f, proj, sid = _write_transcript_with_subagents(tmp_path)
    cfg = Config(scan_paths=[str(tmp_path / "scan")],
                 labels_file=str(tmp_path / "labels.json"),
                 mock_data_file="")
    set_session_label(cfg, sid, "Renamed Session")
    set_mission_label(cfg, str(proj), "Renamed Mission")
    snap = build_snapshot(cfg, include_processes=False)
    sess = snap.find_session(sid)
    assert sess.label == "Renamed Session"
    assert sess.title == "Renamed Session"  # override wins over auto title
    mission = next(m for m in snap.missions if m.project_root == str(proj))
    assert mission.name == "Renamed Mission"


def test_resume_builds_correct_argv(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    cfg = Config(terminal_launcher="gnome-terminal")
    argv = build_resume_command("abc-123", str(tmp_path), cfg)
    assert argv[0] == "gnome-terminal"
    assert argv[-3:] == ["claude", "--resume", "abc-123"]
    assert all(";" not in p and "&&" not in p for p in argv)


def test_resume_rejects_unsafe_id(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    cfg = Config(terminal_launcher="xterm")
    with pytest.raises(LaunchError):
        build_resume_command("id; rm -rf /", str(tmp_path), cfg)


def test_resume_dry_run_does_not_spawn(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    spawned = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: spawned.append(a))
    cfg = Config(terminal_launcher="xterm")
    resume_session("abc-123", str(tmp_path), cfg, dry_run=True)
    assert spawned == []


# --- endpoint tests -------------------------------------------------------

@pytest.fixture
def real_client(tmp_path):
    """Client backed by a real on-disk transcript + isolated labels file."""
    f, proj, sid = _write_transcript_with_subagents(tmp_path)
    cfg = Config(scan_paths=[str(tmp_path / "scan")],
                 labels_file=str(tmp_path / "labels.json"), mock_data_file="")
    return TestClient(create_app(cfg)), cfg, sid, proj


def test_session_endpoint_has_new_fields(real_client):
    client, _, sid, _ = real_client
    s = client.get(f"/api/sessions/{sid}").json()
    assert len(s["todos"]) == 3
    assert s["progress"]["total"] == 3
    assert len(s["subagents"]) == 2
    assert s["label"] is None


def test_classic_dashboard_route_serves_alongside_mindmap(real_client):
    client, _, _, _ = real_client
    mindmap = client.get("/")
    classic = client.get("/classic")
    assert mindmap.status_code == 200 and classic.status_code == 200
    assert "cytoscape" in mindmap.text.lower()
    assert "cytoscape" not in classic.text.lower()  # classic has no graph lib
    assert 'href="/classic"' in mindmap.text  # cross-links between the two
    assert 'href="/"' in classic.text


def test_rename_session_persists(real_client):
    client, cfg, sid, _ = real_client
    r = client.post(f"/api/sessions/{sid}/rename", json={"name": "Ported feature"})
    assert r.status_code == 200 and r.json()["name"] == "Ported feature"
    # persisted to labels.json and re-appears on next snapshot
    assert load_labels(cfg)["sessions"][sid] == "Ported feature"
    s2 = client.get(f"/api/sessions/{sid}").json()
    assert s2["label"] == "Ported feature" and s2["title"] == "Ported feature"


def test_rename_rejects_empty_and_oversized(real_client):
    client, _, sid, _ = real_client
    assert client.post(f"/api/sessions/{sid}/rename",
                       json={"name": "   "}).status_code == 400
    assert client.post(f"/api/sessions/{sid}/rename",
                       json={"name": "x" * 201}).status_code == 400


def test_rename_mission_persists(real_client):
    client, cfg, _, proj = real_client
    from urllib.parse import quote
    key = quote(str(proj), safe="")
    r = client.post(f"/api/missions/{key}/rename", json={"name": "Mission A"})
    assert r.status_code == 200
    assert load_labels(cfg)["missions"][str(proj)] == "Mission A"


def test_resume_command_endpoint(real_client, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    client, _, sid, _ = real_client
    body = client.get(f"/api/sessions/{sid}/resume-command").json()
    assert body["command"][-3:] == ["claude", "--resume", sid]


def test_resume_endpoint_launches(real_client, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    spawned = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: spawned.append(a))
    client, _, sid, _ = real_client
    r = client.post(f"/api/sessions/{sid}/resume")
    assert r.status_code == 200
    assert r.json()["launched"] is True
    assert len(spawned) == 1


def test_archive_endpoint_persists_and_hides_nothing_on_disk(real_client):
    client, cfg, sid, _ = real_client
    transcript_path = client.get(f"/api/sessions/{sid}").json()["session_file"]
    r = client.post(f"/api/sessions/{sid}/archive")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "id": sid, "archived": True}
    assert load_labels(cfg)["archived"] == [sid]
    # session is still fully readable by id — archiving is display-only
    s2 = client.get(f"/api/sessions/{sid}").json()
    assert s2["archived"] is True
    from pathlib import Path
    assert Path(transcript_path).exists()  # transcript file untouched


def test_unarchive_endpoint_persists(real_client):
    client, cfg, sid, _ = real_client
    client.post(f"/api/sessions/{sid}/archive")
    r = client.post(f"/api/sessions/{sid}/unarchive")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "id": sid, "archived": False}
    assert load_labels(cfg)["archived"] == []
    assert client.get(f"/api/sessions/{sid}").json()["archived"] is False


def test_archive_unknown_session_404s(real_client):
    client, _, _, _ = real_client
    assert client.post("/api/sessions/does-not-exist/archive").status_code == 404
