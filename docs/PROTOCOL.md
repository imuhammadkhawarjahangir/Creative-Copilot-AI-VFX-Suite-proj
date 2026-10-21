# SENSEI Protocol (v0.1)

Transport: WebSocket JSON

## Endpoints
- `/ws/host` — editing hosts. Send `hello`, `context_frame`, `feedback`. Each `context_frame` is answered with `suggestions` on the same socket.
- `/ws/ui` — copilot UIs. Send `hello`, `feedback`. Receive the latest `suggestions` on connect, then every `suggestions` message produced from any host's frames.
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

An item's `id` is derived from its content, so an unchanged suggestion keeps its id across frames.

## UI/Host -> Service (feedback)
{
  "type": "feedback",
  "suggestion_id": "sug_001",
  "verdict": "accepted"
}

`verdict` is one of `accepted`, `rejected`, `modified`; optional `notes` string.

## Service replies
### ack (to hello)
{ "type": "ack", "ok": true }

### ack_feedback (to feedback)
{ "type": "ack_feedback", "ok": true }

### error
Sent for invalid JSON, a failed validation, or an unknown `type`. The connection stays open.
{ "type": "error", "message": "invalid context_frame: scene: Field required" }
