import json
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from .protocol import HelloMsg, ContextFrameMsg, FeedbackMsg
from .engine.suggest import build_suggestions

log = logging.getLogger("uvicorn.error")

app = FastAPI(title="SENSEI Service", version="0.1.0")


class UiHub:
    # Fans every suggestions message produced from host frames out to connected UIs.
    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.latest: str | None = None

    async def broadcast(self, message: str):
        self.latest = message
        for ws in list(self.clients):
            try:
                await ws.send_text(message)
            except Exception:
                self.clients.discard(ws)


hub = UiHub()


async def send_error(ws: WebSocket, message: str):
    await ws.send_text(json.dumps({"type": "error", "message": message}))


def describe(err: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in err.errors())


async def serve(ws: WebSocket, on_frame=None):
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
                HelloMsg.model_validate(data)  # validates
                await ws.send_text(json.dumps({"type": "ack", "ok": True}))
            elif msg_type == "context_frame":
                if on_frame is None:
                    await send_error(ws, "context_frame is only accepted on /ws/host")
                else:
                    await on_frame(ContextFrameMsg.model_validate(data))
            elif msg_type == "feedback":
                fb = FeedbackMsg.model_validate(data)
                # accept feedback (store later)
                log.info("feedback %s %s", fb.suggestion_id, fb.verdict)
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
    await ws.accept()

    async def on_frame(ctx: ContextFrameMsg):
        suggestions = build_suggestions(ctx).model_dump_json()
        await ws.send_text(suggestions)
        await hub.broadcast(suggestions)

    try:
        await serve(ws, on_frame)
    except WebSocketDisconnect:
        return


@app.websocket("/ws/ui")
async def ws_ui(ws: WebSocket):
    await ws.accept()
    hub.clients.add(ws)
    try:
        if hub.latest:
            await ws.send_text(hub.latest)
        await serve(ws)
    except WebSocketDisconnect:
        return
    finally:
        hub.clients.discard(ws)
