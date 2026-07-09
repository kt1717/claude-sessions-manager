from datetime import datetime, timedelta, timezone

from csm.config import Config
from csm.correlate import correlate
from csm.models import ProcessInfo, Session

NOW = datetime.now(timezone.utc)


def proc(pid, cwd=None, started=None):
    return ProcessInfo(pid=pid, command="claude", cwd=cwd,
                       started_at=started or NOW - timedelta(hours=1))


def test_exact_cwd_high():
    s = Session(id="s1", cwd="/tmp/a", updated_at=NOW)
    left = correlate([s], [proc(1, "/tmp/a")], Config())
    assert s.process.pid == 1 and s.confidence == "high"
    assert s.status == "active"
    assert left == []


def test_project_root_medium(tmp_path):
    root = tmp_path / "repo"
    (root / "sub").mkdir(parents=True)
    s = Session(id="s1", cwd=str(root), project_root=str(root), updated_at=NOW)
    left = correlate([s], [proc(2, str(root / "sub"))], Config())
    assert s.process.pid == 2 and s.confidence == "medium"
    assert left == []


def test_recency_low():
    s = Session(id="s1", updated_at=NOW)
    correlate([s], [proc(3, None)], Config())
    assert s.process.pid == 3 and s.confidence == "low"


def test_no_match_unknown_and_orphan():
    s = Session(id="s1", cwd="/tmp/a", updated_at=NOW - timedelta(hours=200))
    p = proc(4, "/tmp/other", started=NOW)
    left = correlate([s], [p], Config())
    assert s.process is None and s.confidence == "unknown"
    assert left == [p]
    assert s.status == "completed"  # older than completed_after_hours


def test_status_idle_vs_completed():
    cfg = Config(idle_after_minutes=60, completed_after_hours=24)
    fresh = Session(id="a", updated_at=NOW - timedelta(minutes=5))
    stale = Session(id="b", updated_at=NOW - timedelta(hours=48))
    middle = Session(id="c", updated_at=NOW - timedelta(hours=3))
    nodate = Session(id="d")
    correlate([fresh, stale, middle, nodate], [], cfg)
    assert fresh.status == "idle"
    assert stale.status == "completed"
    assert middle.status == "idle"
    assert nodate.status == "unknown"


def test_one_process_not_shared():
    s1 = Session(id="s1", cwd="/tmp/a", updated_at=NOW)
    s2 = Session(id="s2", cwd="/tmp/a", updated_at=NOW)
    correlate([s1, s2], [proc(5, "/tmp/a")], Config())
    attached = [s for s in (s1, s2) if s.process]
    assert len(attached) == 1
