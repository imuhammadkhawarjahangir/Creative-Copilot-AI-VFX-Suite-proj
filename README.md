# SENSEI — AI Copilot for Editing + Motion Graphics

SENSEI connects to professional editing and motion-graphics software to provide real-time creative suggestions,
automation, and intelligent enhancements:
- Emotion-aware color grade suggestions
- Context-aware transitions
- Rhythm-synced keyframe snapping to soundtrack beats
- Shot-aware VFX preset recommendations

## Monorepo Layout
- `service/` — FastAPI realtime suggestion engine (WebSocket)
- `apps/copilot-desktop/` — Electron + React desktop UI
- `adapters/` — Host integrations (mock host included)

## Quickstart (Demo)
Requirements: Docker, Node 22.12+ (Python 3.11+ only to run the service without Docker)

### 1) Start AI service + mock host
```bash
docker compose up --build
```
The service listens on `127.0.0.1:8000` (loopback only); the mock host streams a synthetic timeline into it.
Browser pages may only connect from the origins in `SENSEI_ALLOWED_ORIGINS` (default: the desktop UI's dev server) — see `docs/PROTOCOL.md`.

### 2) Start the desktop UI
```bash
cd apps/copilot-desktop
npm install
npm run dev
```
Suggestions stream into the window as the mock host's playhead moves. Apply / Dismiss send feedback to the service (visible in its logs).

### Without Docker
```bash
# service
cd service
pip install -e .
uvicorn sensei_service.main:app --port 8000

# mock host (second terminal)
cd adapters/mock-host
npm install
npm start
```

### Optional: require a token
Give the service, the hosts and the desktop UI the same `SENSEI_TOKEN` (letters, digits, `.`, `_`, `~`, `-`); clients without it are rejected.
```bash
export SENSEI_TOKEN=$(openssl rand -hex 16)          # PowerShell: $env:SENSEI_TOKEN = [guid]::NewGuid().ToString("N")
docker compose up --build                            # passes it to the service and the mock host
cd apps/copilot-desktop && npm run dev               # Electron hands it to the UI
```
