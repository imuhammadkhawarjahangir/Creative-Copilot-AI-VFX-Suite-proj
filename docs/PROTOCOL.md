# SENSEI Protocol (v0.1)

Transport: WebSocket JSON

## Endpoints
- `/ws/host` — editing hosts. Send `hello` first (it registers the host's project), then `context_frame`, `action_result` and `feedback`. Each `context_frame` is answered with `suggestions` on the same socket; a frame sent before `hello` gets an `error`. Hosts also receive `apply_action` (see *Applying a suggestion*).
- `/ws/ui` — copilot UIs. No handshake: UIs don't register a project (a `hello` here is acknowledged and ignored). Send `apply` and `feedback`. On connect, receive the latest `suggestions` of every open project; then every `suggestions` message produced from any host's frames, and `project_closed` when the last host of a project disconnects, in that order. A UI that falls 256 messages behind is disconnected (close code 1013) rather than slowing everyone else down.

Suggestions are scoped per project: each `suggestions` message carries the `project_id` and `host` name from that host's `hello`, so a UI can keep one stream per project.
- `GET /health` — `{"ok": true}`

WebSocket handshakes that carry a browser `Origin` not listed in `SENSEI_ALLOWED_ORIGINS` (comma-separated, default `http://127.0.0.1:5180,http://localhost:5180,sensei://app`: the desktop UI's dev server and its packaged origin) are rejected with HTTP 403. Native clients such as host adapters send no `Origin` and are accepted.

### Optional tokens
When auth is on, every WebSocket client must offer the subprotocol `sensei.token.<its token>`; a missing or wrong token is rejected with HTTP 403. The service echoes the offered subprotocol back. The token travels as a subprotocol, not a header or query string, because browsers cannot set WebSocket headers and query strings end up in logs.

The service runs in one of two modes:
- `SENSEI_TOKEN` (letters, digits, `.`, `_`, `~`, `-`): one shared token, valid on both endpoints.
- `SENSEI_CLIENTS_FILE`: per-client tokens (managed with `python -m sensei_service.clients`). Each client has roles: `host` may use `/ws/host`, `ui` may use `/ws/ui`; a token used on an endpoint its client has no role for is rejected with 403.

Setting both is refused at startup; setting neither leaves auth off.

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
      "id": "sug_7bec91a2",
      "kind": "color_grade",
      "title": "Moody teal shadows + warm highlights",
      "confidence": 0.78,
      "rationale": "Tense emotion and low-key lighting benefit from cooler shadows and controlled warmth.",
      "action": {
        "op": "apply_color_grade",
        "params": { "lut": "teal_orange_07", "contrast": 1.1, "sat": 0.92, "shadow_tint": "teal", "highlight_tint": "warm" }
      }
    }
  ]
}

(For the `context_frame` above the service also returns a `vfx` and a `keyframe` item; they are omitted here.)

Items are ordered by confidence, highest first; `confidence` is the rule's own, adjusted by the feedback stored for that kind of suggestion (the `rationale` then says so). An item's `id` is derived from its content, so an unchanged suggestion keeps its id across frames. Times in action params (`time`, `at`) are seconds, rounded to milliseconds.

### Actions
An action describes an edit. These are the only ops a host is ever asked to perform; the service checks every action against these bounds (and rejects unknown keys) before sending it.

| `op` | `params` (bounds) |
|------|----------|
| `apply_color_grade` | `lut` (`[a-z0-9_]{1,64}`), `contrast` (0.5–2), `sat` (0–2), optional `shadow_tint`, `highlight_tint` (`teal` or `warm`) |
| `apply_transition_at_cut` | `type` (`dip_to_black` or `cross_dissolve`), `frames` (1–48), `at` (cut time, ≥ 0) |
| `snap_keyframe_to_time` | `time` (the next beat, ≥ 0), `target` (`next_keyframe`) |
| `apply_vfx_preset` | `preset` (`[a-z0-9_]{1,64}`), `threshold`, `intensity`, `radius` (0–1) |

## Applying a suggestion

1. The UI names a suggestion; it never sends an action itself:
   `{ "type": "apply", "project_id": "p1", "suggestion_id": "sug_7bec91a2" }`
2. The service looks the suggestion up in that project's latest suggestions, re-checks its action against the bounds above, and sends it to every host of the project:
   `{ "type": "apply_action", "action_id": "act_3f1c09a2b4d7", "suggestion_id": "sug_7bec91a2", "op": "apply_color_grade", "params": { ... } }`
   The `action_id` is derived from the project and suggestion, so applying the same suggestion again yields the same id; a host must apply an id at most once.
3. The host answers on its socket:
   `{ "type": "action_result", "action_id": "act_3f1c09a2b4d7", "status": "applied", "detail": null }`
   `status` is `applied`, `marked` (the host can't do it directly and left a marker for the editor), `duplicate` (already applied) or `rejected` (with a `detail`). A host can only report actions of its own project.
4. The service forwards the result to every UI, with `project_id` and `suggestion_id` added. If the service couldn't send the action (no host connected, the suggestion is no longer current, or it fails the bounds), the UI gets the same message with `"status": "failed"` and a `detail`.

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
