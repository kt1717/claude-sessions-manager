"""Local web dashboard: FastAPI backend + single-file HTML frontend.

Safety: binds 127.0.0.1 by default; file reads only through discovered session
paths (no arbitrary path parameter -> no traversal); actions gated by
enable_gui_actions and the launcher whitelist; all text redacted.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response

from .config import Config, load_config
from .discovery import build_snapshot
from .labels import LabelError, set_mission_label, set_session_label
from .launcher import (LaunchError, build_open_command, build_resume_command,
                       open_terminal, read_file_preview, resume_session)
from .redact import redact

WEB_DIR = Path(__file__).parent / "web"


def create_app(config: Config | None = None) -> FastAPI:
    config = config or load_config()
    app = FastAPI(title="claude-session-monitor", docs_url=None, redoc_url=None)

    def snapshot():
        return build_snapshot(config)

    def get_session(session_id: str):
        snap = snapshot()
        sess = snap.find_session(session_id)
        if not sess:
            raise HTTPException(status_code=404, detail="session not found")
        return snap, sess

    def redacted(payload) -> JSONResponse:
        text = redact(json.dumps(payload, default=str))
        return JSONResponse(content=json.loads(text))

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (WEB_DIR / "index.html").read_text()

    @app.get("/vendor/{name}")
    def vendor(name: str):
        # Serve only whitelisted, locally-vendored static assets (no path traversal).
        allowed = {"cytoscape.min.js": "application/javascript"}
        if name not in allowed:
            raise HTTPException(status_code=404, detail="not found")
        path = WEB_DIR / "vendor" / name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return Response(content=path.read_bytes(), media_type=allowed[name])

    @app.get("/api/sessions")
    def api_sessions():
        snap = snapshot()
        return redacted({
            "generated_at": snap.generated_at.isoformat(),
            "sessions": [s.model_dump(mode="json") for s in snap.sessions],
        })

    @app.get("/api/sessions/{session_id}")
    def api_session(session_id: str):
        _, sess = get_session(session_id)
        return redacted(sess.model_dump(mode="json"))

    @app.get("/api/tree")
    def api_tree():
        snap = snapshot()
        return redacted({
            "generated_at": snap.generated_at.isoformat(),
            "missions": [m.model_dump(mode="json") for m in snap.missions],
            "orphan_processes": [p.model_dump(mode="json") for p in snap.orphan_processes],
        })

    @app.post("/api/sessions/{session_id}/open")
    def api_open(session_id: str):
        if not config.enable_gui_actions:
            raise HTTPException(status_code=403, detail="GUI actions disabled in config")
        _, sess = get_session(session_id)
        target = sess.project_root or sess.cwd
        if not target or not Path(target).is_dir():
            raise HTTPException(status_code=400, detail="no existing project directory")
        try:
            argv = open_terminal(target, config)
        except LaunchError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"launched": True, "command": argv, "cwd": target}

    @app.get("/api/sessions/{session_id}/read")
    def api_read(session_id: str, which: str = "auto"):
        # Path comes ONLY from discovered session metadata — never from the client.
        _, sess = get_session(session_id)
        candidates = {
            "markdown": sess.markdown_file, "log": sess.log_file,
            "session": sess.session_file,
            "auto": sess.markdown_file or sess.log_file or sess.session_file,
        }
        if which not in candidates:
            raise HTTPException(status_code=400, detail="which must be auto|markdown|log|session")
        target = candidates[which]
        if not target:
            raise HTTPException(status_code=404, detail="no such file recorded for this session")
        try:
            content = read_file_preview(target)
        except LaunchError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return {"path": target, "content": redact(content)}

    @app.get("/api/sessions/{session_id}/open-command")
    def api_open_command(session_id: str):
        _, sess = get_session(session_id)
        target = sess.project_root or sess.cwd
        if not target:
            return {"command": None, "reason": "no project directory detected"}
        try:
            return {"command": build_open_command(target, config), "cwd": target}
        except LaunchError as exc:
            return {"command": None, "reason": str(exc)}

    @app.post("/api/sessions/{session_id}/rename")
    def api_rename_session(session_id: str, body: dict = Body(...)):
        _, sess = get_session(session_id)  # 404 if unknown
        try:
            name = set_session_label(config, sess.id, (body or {}).get("name", ""))
        except LabelError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "id": sess.id, "name": name}

    @app.post("/api/missions/{mission_key:path}/rename")
    def api_rename_mission(mission_key: str, body: dict = Body(...)):
        # key is the project_root (url-encoded, may contain slashes)
        key = unquote(mission_key)
        try:
            name = set_mission_label(config, key, (body or {}).get("name", ""))
        except LabelError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "key": key, "name": name}

    @app.post("/api/sessions/{session_id}/resume")
    def api_resume(session_id: str):
        if not config.enable_gui_actions:
            raise HTTPException(status_code=403, detail="GUI actions disabled in config")
        _, sess = get_session(session_id)
        target = sess.project_root or sess.cwd
        if not target or not Path(target).is_dir():
            raise HTTPException(status_code=400, detail="no existing project directory")
        try:
            argv = resume_session(sess.id, target, config)
        except LaunchError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"launched": True, "command": argv, "cwd": target}

    @app.get("/api/sessions/{session_id}/resume-command")
    def api_resume_command(session_id: str):
        _, sess = get_session(session_id)
        target = sess.project_root or sess.cwd
        if not target:
            return {"command": None, "reason": "no project directory detected"}
        try:
            return {"command": build_resume_command(sess.id, target, config),
                    "cwd": target}
        except LaunchError as exc:
            return {"command": None, "reason": str(exc)}

    @app.get("/api/doctor")
    def api_doctor():
        from .launcher import pick_terminal
        import shutil
        checks = {}
        for p in config.expanded_scan_paths():
            checks[f"scan_path:{p}"] = p.is_dir()
        try:
            checks["terminal_launcher"] = pick_terminal(config) or False
        except LaunchError as exc:
            checks["terminal_launcher"] = f"error: {exc}"
        checks["safe_reader"] = bool(shutil.which(config.safe_read_command))
        checks["gui_actions_enabled"] = config.enable_gui_actions
        snap = snapshot()
        checks["sessions_found"] = len(snap.sessions)
        checks["orphan_processes"] = len(snap.orphan_processes)
        return redacted(checks)

    @app.get("/api/export")
    def api_export():
        snap = snapshot()
        return redacted(snap.model_dump(mode="json"))

    return app


def run_server(host: str = "127.0.0.1", port: int = 8765, config: Config | None = None):
    import uvicorn
    uvicorn.run(create_app(config), host=host, port=port, log_level="warning")
