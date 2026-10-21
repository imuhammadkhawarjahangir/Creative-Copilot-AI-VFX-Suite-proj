# Architecture

## Components
1. Host Adapter
   - Runs next to an NLE / motion-graphics host and streams its timeline context to the service: playhead, cuts,
     beat times and scene metadata (emotion, lighting, notes).
   - Implemented: `adapters/mock-host`, which replays a synthetic timeline. Planned: Resolve and Adobe
     (`adapters/resolve`, `adapters/adobe-bridge` are placeholders).
   - Applies actions the service sends (`apply_action`), at most once per action id, and reports the result. The
     mock host records them.

2. SENSEI Service (FastAPI, `service/`)
   - `/ws/host`: a host registers its project with `hello`, then streams context frames; each frame is answered with
     suggestions and action payloads.
   - Suggestion engine (`service/sensei_service/engine/`): four rules (color grade, transition, rhythm keyframe, VFX
     preset), each a heuristic over the frame with a fixed confidence. Results are ranked by confidence, and ids are
     derived from content, so an unchanged suggestion keeps its id across frames.
   - `/ws/ui`: UIs receive the latest suggestions of every open project, then every update, scoped per project.
   - Applying: routes a suggestion a UI applied to the project's hosts (bounded, idempotent `apply_action`) and the
     hosts' results back to every UI.
   - Feedback is validated and logged; it is not stored or used to adapt suggestions yet.
   - (Future) Pluggable model backends: local LLM/VLM, remote APIs, hybrid

3. Copilot Desktop (Electron + React, `apps/copilot-desktop/`)
   - Shows one live suggestion stream per project
   - Apply sends the suggestion to the host through the service and shows the host's answer; Dismiss sends feedback
   - Sandboxed renderer; the packaged UI is served over a privileged `sensei://` scheme

## Data flow
Host -> `hello` + context frames -> Service -> suggestions (per project) -> UI -> feedback -> Service

UI apply -> Service (checks the action) -> Host applies it -> result -> Service -> UI

## Key design choices
- WebSocket JSON protocol ([`PROTOCOL.md`](PROTOCOL.md)), validated by the pydantic models in
  `service/sensei_service/protocol.py`
- Host adapters are swappable: anything that speaks the protocol on `/ws/host` is a host
- Hosts and UIs use separate endpoints; the service fans each project's suggestions out to every UI
- Local-first: loopback binding, browser-origin allowlist, optional token ([`SECURITY.md`](SECURITY.md))
