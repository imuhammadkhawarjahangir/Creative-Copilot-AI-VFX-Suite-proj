# Security

## Principles
- Treat host project data as sensitive.
- Default: run suggestions locally; optional remote inference behind explicit user opt-in (none exists today).
- Never transmit media without consent; use metadata/proxies by default (today only timeline metadata is sent).

## Controls in place
- **Network exposure:** the service binds to loopback; Docker Compose publishes it on `127.0.0.1` only.
- **Browser origins:** browsers don't apply the same-origin policy to WebSockets, so handshakes from browser
  origins outside `SENSEI_ALLOWED_ORIGINS` are rejected.
- **Optional tokens:** one shared `SENSEI_TOKEN`, or per-client tokens from `SENSEI_CLIENTS_FILE`, where each client
  has roles (`host` streams frames, `ui` follows suggestions) and can be revoked on its own. Clients present their
  token as a WebSocket subprotocol, so it never appears in URLs; only SHA-256 hashes are stored, and every entry is
  compared in constant time. Configuring both modes is refused rather than silently picking one. Connections and
  feedback are logged with the client's name.
- **Desktop renderer:** context isolation, no Node integration, sandbox; a Content-Security-Policy (no `eval`,
  WebSockets only to the service); popups and navigation away from the UI are blocked. The token reaches the page
  over IPC through a sandboxed preload, never through the bundle or a URL.
- **Applying actions:** a UI can only name a suggestion; the action sent to a host is the one the service produced,
  limited to four ops whose params are re-checked against fixed bounds (unknown keys rejected). Action ids are
  derived from project and suggestion, so hosts can refuse repeats. With per-client tokens, a project belongs to the
  client whose host opened it: another client's host is refused for it (it would otherwise receive the project's
  actions), and only the host connections an action was sent to may report its result.
- **Input handling:** every message is validated; malformed input gets an error reply rather than a crash.
- **Supply chain:** Python dependencies are hash-pinned and installed with `--require-hashes`; npm dependencies are
  locked; CI actions are pinned to commit SHAs; the images run as non-root users.

## Known gaps
- Feedback is stored locally with no automatic expiry: suggestion ids, verdicts, project ids, client names and the
  kind of suggestion (no media or timeline content). Delete `~/.sensei/feedback.sqlite3` (or the `sensei-data`
  volume) to clear it, or set `SENSEI_FEEDBACK_DB=:memory:` to keep nothing.
- Tokens identify clients (an app or adapter), not people: there is no per-user login, and revoking a client takes
  effect when the service restarts.
