# Mindmap GUI — API contract

Data the web GUI (`csm/web/index.html`) consumes. All responses pass through `redact()`.
Verified 2026-07-09 against real transcripts + mock data.

## GET /api/tree  (dashboard's primary feed, polled)

```jsonc
{
  "generated_at": "<iso8601>",
  "missions": [ Mission, ... ],      // grouping tier: one per project_root
  "orphan_processes": [ ... ]
}
```

### Mission (the "task" / path-group tier — renamable)
```jsonc
{
  "id": "<string>",
  "name": "<string>",            // display name; OVERRIDDEN by labels.json missions[project_root]
  "description": "<string|null>",
  "project_root": "<abs path>",  // == rename key for this mission
  "git_branch": "<string|null>",
  "created_at": "<iso8601|null>",
  "updated_at": "<iso8601|null>",
  "sessions": [ Session, ... ]
}
```

### Session (leaf tier — detail + renamable + resumable)
Top-level keys:
`id, title, status, model, usage, process, project_root, cwd, session_file,
markdown_file, log_file, started_at, updated_at, confidence, evidence, git_branch,
source, todos, progress, subagents, label`

New fields added for the mindmap:
```jsonc
{
  "id": "<session-uuid>",           // == rename/resume key
  "title": "<string|null>",         // auto-derived; label overrides display
  "label": "<string|null>",         // user override from labels.json sessions[id]; null if unset
  "model": "claude-sonnet-5",       // dominant/last model
  "usage": { "input_tokens": N, "output_tokens": N, "total_tokens": N,
             "estimated_cost": F, "source": "...", "confidence": "..." },
  "status": "active|idle|completed|unknown",
  "cwd": "<abs path>",              // used as resume working dir

  "todos": [ { "content": "...", "activeForm": "...",
               "status": "pending|in_progress|completed" }, ... ],   // latest TodoWrite; [] if none
  "progress": { "completed": N, "in_progress": N, "pending": N, "total": N } | null,

  "subagents": [ SubAgent, ... ]    // [] if none
}
```

### SubAgent (agent→subagent edge, for the relation chart)
```jsonc
{
  "agent_id": "a88352641e1503eb3",   // node id (filename stem of agent-<id>.jsonl)
  "agent_type": "Explore",           // e.g. Explore, general-purpose; may be null
  "description": "Find show_*.xml files",
  "tool_use_id": "toolu_018c86Ya...",// == parent Agent tool_use .id (authoritative edge)
  "spawn_depth": 1,                  // 1 = spawned by main agent; deeper = nested
  "model": "claude-opus-4-8",        // null if unknown
  "total_tokens": 172681,
  "status": null
}
```
Edge for the chart: parent **Session.id** → each **SubAgent.agent_id**. `spawn_depth`
supports nesting (child subagents would carry depth > 1).

## GET /api/sessions/{id}
Single Session object (same shape as above).

## POST /api/sessions/{id}/rename
Body `{"name": "<string>"}` → `{"ok": true, "id": "<id>", "name": "<saved name>"}`.
Persists to `labels.json` `sessions[id]`. **404** if `id` not in the current snapshot.
Names are trimmed, newline-stripped, length-capped.

## POST /api/missions/{project_root}/rename
`project_root` is the path (route uses `:path` so slashes are allowed).
Body `{"name": "..."}` → `{"ok": true, "key": "<project_root>", "name": "..."}`.
Persists to `labels.json` `missions[project_root]`.

## POST /api/sessions/{id}/resume
Launches a whitelisted terminal running `claude --resume <id>` in the session cwd.
→ `{"launched": bool, "command": [argv...], "cwd": "..."}`. Errors (no terminal, bad
dir) return 4xx with `detail`.

## GET /api/sessions/{id}/resume-command
Preview only, no spawn → `{"command": [argv...], "cwd": "..."}`.
NOTE: `command` is the full terminal argv (e.g. `["gnome-terminal","--working-directory=…","--","claude","--resume","<id>"]`).
For a clipboard "copy" button, the GUI should copy the bare `claude --resume <id>`.

## Persistence
`labels.json` lives beside the config file: `~/.config/claude-session-monitor/labels.json`
(overridable via `$CSM_CONFIG` dir). Shape:
```json
{ "sessions": { "<session-id>": "name" }, "missions": { "<project_root>": "name" } }
```
Loaded in `build_snapshot`; applied as overrides over auto-derived title/name.
