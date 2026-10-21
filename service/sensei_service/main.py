import hmac
import json
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from .config import settings
from .protocol import HelloMsg, ContextFrameMsg, FeedbackMsg, ProjectClosedMsg
from .engine.suggest import build_suggestions

log = logging.getLogger("uvicorn.error")

app = FastAPI(title="SENSEI Service", version="0.1.0")


class Hub:
    # Fans suggestions out to connected UIs, one stream per project.
    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.latest: dict[str, str] = {}  # project id -> last suggestions message
        self.hosts: dict[str, int] = {}   # project id -> connected host count

    async def send_all(self, message: str):
        for ws in list(self.clients):
            try:
                await ws.send_text(message)
            except Exception:
                self.clients.discard(ws)

    async def publish(self, project_id: str, message: str):
        self.latest[project_id] = message
        await self.send_all(message)

    def open(self, project_id: str):
        self.hosts[project_id] = self.hosts.get(project_id, 0) + 1

    async def close(self, project_id: str):
        self.hosts[project_id] -= 1
        if self.hosts[project_id] == 0:
            del self.hosts[project_id]
            self.latest.pop(project_id, None)
            await self.send_all(ProjectClosedMsg(project_id=project_id).model_dump_json())


hub = Hub()


TOKEN_PROTOCOL = "sensei.token."


async def accept(ws: WebSocket) -> bool:
    # Browsers always send Origin, so this blocks other web pages (closing before accept -> HTTP 403).
    origin = ws.headers.get("origin")
    if origin is not None and origin not in settings.allowed_origins:
        log.warning("rejected websocket from origin %s", origin)
        await ws.close(code=1008)
        return False

    offered = [p for p in ws.scope.get("subprotocols", []) if p.startswith(TOKEN_PROTOCOL)]
    if settings.token is not None:
        presented = offered[0][len(TOKEN_PROTOCOL):] if offered else ""
        if not hmac.compare_digest(presented.encode(), settings.token.encode()):
            log.warning("rejected websocket without a valid token")
            await ws.close(code=1008)
            return False

    # a browser drops the connection unless the subprotocol it offered is echoed back
    await ws.accept(subprotocol=offered[0] if offered else None)
    return True


async def send_error(ws: WebSocket, message: str):
    await ws.send_text(json.dumps({"type": "error", "message": message}))


def describe(err: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in err.errors())


async def serve(ws: WebSocket, on_hello=None, on_frame=None):
    # Bad input gets an error reply; the socket stays open.
    while True:
        raw = await ws.receive_text()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            await send_error(ws, "invalid json")
            continue
        if not isinstance(data, dict):
            await send_error(ws, "message must be a JSON object")
            continue

        msg_type = data.get("type")
        try:
            if msg_type == "hello":
                hello = HelloMsg.model_validate(data)
                if on_hello is not None:
                    await on_hello(hello)
                await ws.send_text(json.dumps({"type": "ack", "ok": True}))
            elif msg_type == "context_frame":
                if on_frame is None:
                    await send_error(ws, "context_frame is only accepted on /ws/host")
                else:
                    await on_frame(ContextFrameMsg.model_validate(data))
            elif msg_type == "feedback":
                fb = FeedbackMsg.model_validate(data)
                # accept feedback (store later)
                log.info("feedback %s %s (project %s)", fb.suggestion_id, fb.verdict, fb.project_id)
                await ws.send_text(json.dumps({"type": "ack_feedback", "ok": True}))
            else:
                await send_error(ws, "unknown msg type")
        except ValidationError as err:
            await send_error(ws, f"invalid {msg_type}: {describe(err)}")


@app.get("/health")
def health():
    return {"ok": True}


@app.websocket("/ws/host")
async def ws_host(ws: WebSocket):
    if not await accept(ws):
        return

    hello: HelloMsg | None = None

    async def on_hello(msg: HelloMsg):
        nonlocal hello
        if hello is None or hello.project.id != msg.project.id:
            if hello is not None:
                await hub.close(hello.project.id)
            hub.open(msg.project.id)
        hello = msg

    async def on_frame(ctx: ContextFrameMsg):
        if hello is None:
            await send_error(ws, "send hello before context_frame")
            return
        msg = build_suggestions(ctx)
        msg.project_id, msg.host = hello.project.id, hello.host.name
        suggestions = msg.model_dump_json()
        await ws.send_text(suggestions)
        await hub.publish(hello.project.id, suggestions)

    try:
        await serve(ws, on_hello, on_frame)
    except WebSocketDisconnect:
        return
    finally:
        if hello is not None:
            await hub.close(hello.project.id)


@app.websocket("/ws/ui")
async def ws_ui(ws: WebSocket):
    if not await accept(ws):
        return
    hub.clients.add(ws)
    try:
        for message in list(hub.latest.values()):
            await ws.send_text(message)
        await serve(ws)
    except WebSocketDisconnect:
        return
    finally:
        hub.clients.discard(ws)
