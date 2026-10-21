# SENSEI Protocol (v0.1)

Transport: WebSocket JSON

## Endpoints
- `/ws/host` — editing hosts. Send `hello` first (it registers the host's project), then `context_frame` and `feedback`. Each `context_frame` is answered with `suggestions` on the same socket; a frame sent before `hello` gets an `error`.
- `/ws/ui` — copilot UIs. Send `hello`, `feedback`. On connect, receive the latest `suggestions` of every open project; then every `suggestions` message produced from any host's frames, and `project_closed` when the last host of a project disconnects.

Suggestions are scoped per project: each `suggestions` message carries the `project_id` and `host` name from that host's `hello`, so a UI can keep one stream per project.
- `GET /health` — `{"ok": true}`

WebSocket handshakes that carry a browser `Origin` not listed in `SENSEI_ALLOWED_ORIGINS` (comma-separated, default `http://127.0.0.1:5180,http://localhost:5180`) are rejected with HTTP 403. Native clients such as host adapters send no `Origin` and are accepted.

## Host -> Service

### hello
{
  "type": "hello",
  "host": { "name": "mock-host", "version": "0.1" },
  "project": { "id": "p1", "fps": 24 }
}

### context_frame
{
  "type": "context_frame",
  "ts": 1730000000,
  "playhead": 12.4,
  "scene": {
    "shot_id": "A012_C003",
    "emotion": "tense",
    "lighting": "low-key",
    "notes": "night interior, neon spill"
  },
  "timeline": {
    "clips": [
      { "id": "c1", "in": 10.0, "out": 14.2, "kind": "video", "label": "Wide" }
    ],
    "cuts": [14.2],
    "audio": { "bpm": 92, "beats": [12.0, 12.65, 13.3] }
  }
}

## Service -> Host/UI

### suggestions
{
  "type": "suggestions",
  "ts": 1730000001,
  "project_id": "p1",
  "host": "mock-host",
  "items": [
    {
      "id": "sug_001",
      "kind": "color_grade",
      "title": "Moody teal shadows + warm highlights",
      "confidence": 0.78,
      "rationale": "Matches 'tense' emotion + neon spill lighting",
      "action": {
        "op": "apply_color_grade",
        "params": { "lut": "teal_orange_07", "sat": 0.92, "contrast": 1.1 }
      }
    }
  ]
}

Items are ordered by confidence, highest first. An item's `id` is derived from its content, so an unchanged suggestion keeps its id across frames. Times in action params (`time`, `at`) are seconds, rounded to milliseconds.

## UI/Host -> Service (feedback)
{
  "type": "feedback",
  "suggestion_id": "sug_001",
  "verdict": "accepted",
  "project_id": "p1"
}

`verdict` is one of `accepted`, `rejected`, `modified`; `project_id` and `notes` are optional.

## Service replies
### ack (to hello)
{ "type": "ack", "ok": true }

### ack_feedback (to feedback)
{ "type": "ack_feedback", "ok": true }

### project_closed (to UIs)
Sent when the last host of a project disconnects; the project's latest suggestions are dropped.
{ "type": "project_closed", "project_id": "p1" }

### error
Sent for invalid JSON, a failed validation, or an unknown `type`. The connection stays open.
{ "type": "error", "message": "invalid context_frame: scene: Field required" }
