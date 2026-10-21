import asyncio
import hashlib
import json
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from .clients import authenticate
from .config import settings
from .protocol import (ActionOutcomeMsg, ActionResultMsg, ApplyActionMsg, ApplyMsg, ContextFrameMsg,
                       FeedbackMsg, HelloMsg, ProjectClosedMsg, checked_params)
from .engine.suggest import build_suggestions

log = logging.getLogger("uvicorn.error")

app = FastAPI(title="SENSEI Service", version="0.1.0")

UI_QUEUE_SIZE = 256  # messages a UI may fall behind by before it is disconnected


class UiClient:
    # Everything sent to one UI goes through its queue and a single sender task,
    # so a UI that stops reading only ever blocks itself.
    def __init__(self, ws: WebSocket, backlog: int):
        self.ws = ws
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=UI_QUEUE_SIZE + backlog)
        self.sender: asyncio.Task | None = None
        self.dropped = False  # set once; nothing is queued for this UI after that


class Hub:
    # Fans suggestions out to connected UIs, one stream per project, and routes applied
    # actions to the project's hosts. Every method is synchronous, so hub state never
    # changes halfway through another update.
    def __init__(self):
        self.clients: set[UiClient] = set()
        self.latest: dict[str, str] = {}               # project id -> last suggestions message
        self.hosts: dict[str, set[WebSocket]] = {}     # project id -> connected host sockets
        self.actions: dict[str, tuple[str, str]] = {}  # action id -> (project id, suggestion id)
        self.background: set[asyncio.Task] = set()     # sends and closes in flight, kept referenced

    def spawn(self, coro):
        task = asyncio.get_running_loop().create_task(coro)
        self.background.add(task)
        task.add_done_callback(self.finished)

    def finished(self, task: asyncio.Task):
        self.background.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.info("background send failed: %r", task.exception())

    def subscribe(self, ws: WebSocket) -> UiClient:
        # The snapshot is queued in the same step as registration, so no publish or close can
        # land in between: a UI sees each open project's latest state, then every later message.
        ui = UiClient(ws, backlog=len(self.latest))
        for message in self.latest.values():
            ui.queue.put_nowait(message)
        self.clients.add(ui)
        return ui

    def start(self, ui: UiClient):
        ui.sender = asyncio.get_running_loop().create_task(self.pump(ui))

    async def pump(self, ui: UiClient):
        try:
            while True:
                await ui.ws.send_text(await ui.queue.get())
        except Exception as exc:  # cancellation is a BaseException and still propagates
            # The socket failed under us: stop delivering. The connection's handler sees the
            # disconnect and finishes cleanup; the task ends normally, with nothing left unretrieved.
            log.info("stopped sending to a UI whose connection failed: %r", exc)
            ui.dropped = True
            self.clients.discard(ui)

    def unsubscribe(self, ui: UiClient):
        ui.dropped = True
        self.clients.discard(ui)
        if ui.sender is not None:
            ui.sender.cancel()

    def send(self, ui: UiClient, message: str):
        if ui.dropped:
            return
        if ui.queue.full():
            log.warning("disconnecting a UI that stopped reading (%d messages behind)", ui.queue.qsize())
            self.unsubscribe(ui)
            self.spawn(close_quietly(ui.ws))
            return
        ui.queue.put_nowait(message)

    def send_all(self, message: str):
        for ui in list(self.clients):
            self.send(ui, message)

    def publish(self, project_id: str, message: str):
        self.latest[project_id] = message
        self.send_all(message)

    def open(self, project_id: str, host: WebSocket):
        self.hosts.setdefault(project_id, set()).add(host)

    def close(self, project_id: str, host: WebSocket):
        hosts = self.hosts.get(project_id)
        if hosts is None:
            return
        hosts.discard(host)
        if hosts:
            return
        del self.hosts[project_id]
        self.latest.pop(project_id, None)
        self.actions = {a: owner for a, owner in self.actions.items() if owner[0] != project_id}
        self.send_all(ProjectClosedMsg(project_id=project_id).model_dump_json())

    def apply(self, project_id: str, suggestion_id: str) -> str | None:
        # Sends a current suggestion's action to the project's hosts. The UI only names the
        # suggestion: the action comes from what the service produced, re-checked against the
        # per-op bounds. Returns a "failed" outcome for the UI, or None once sent.
        def failed(detail: str) -> str:
            return ActionOutcomeMsg(project_id=project_id, suggestion_id=suggestion_id,
                                    status="failed", detail=detail).model_dump_json()

        hosts = self.hosts.get(project_id)
        if not hosts:
            return failed("no host is connected for this project")
        items = json.loads(self.latest[project_id])["items"] if project_id in self.latest else []
        item = next((i for i in items if i["id"] == suggestion_id), None)
        if item is None:
            return failed("this suggestion is no longer current")
        op = item["action"]["op"]
        try:
            params = checked_params(op, item["action"]["params"])
        except ValueError:
            return failed("the action is outside the allowed bounds")
        # the same suggestion in the same project always gets the same id, so hosts can deduplicate
        action_id = "act_" + hashlib.sha256(f"{project_id}/{suggestion_id}".encode()).hexdigest()[:12]
        self.actions[action_id] = (project_id, suggestion_id)
        message = ApplyActionMsg(action_id=action_id, suggestion_id=suggestion_id, op=op, params=params).model_dump_json()
        for host in hosts:
            self.spawn(host.send_text(message))
        return None

    def report(self, project_id: str, result: ActionResultMsg) -> str | None:
        # Forwards a host's result to every UI. Returns an error for the host, or None.
        owner = self.actions.get(result.action_id)
        if owner is None or owner[0] != project_id:
            return "unknown action_id for this host's project"
        self.send_all(ActionOutcomeMsg(project_id=project_id, suggestion_id=owner[1], action_id=result.action_id,
                                       status=result.status, detail=result.detail).model_dump_json())
        return None


hub = Hub()


async def close_quietly(ws: WebSocket):
    # No timeout: the close frame has to wait until the socket drains. A slow client that resumes
    # reading then gets it (1013: try again later) and reconnects with a fresh snapshot; a client
    # that never reads again is cut off by uvicorn's keepalive ping. The hub has dropped it already.
    try:
        await ws.close(code=1013)
    except Exception:
        pass


TOKEN_PROTOCOL = "sensei.token."


async def admit(ws: WebSocket, role: str) -> tuple[str, str] | None:
    # Checks the handshake before accepting it. Returns (subprotocol to echo or "", client name or
    # "" when auth is off), or None after rejecting the connection (closing before accept -> HTTP 403).
    # Browsers always send Origin, so this blocks other web pages.
    origin = ws.headers.get("origin")
    if origin is not None and origin not in settings.allowed_origins:
        log.warning("rejected websocket from origin %r", origin)
        await ws.close(code=1008)
        return None

    offered = [p for p in ws.scope.get("subprotocols", []) if p.startswith(TOKEN_PROTOCOL)]
    name = ""
    if settings.clients is not None:
        client = authenticate(settings.clients, offered[0][len(TOKEN_PROTOCOL):]) if offered else None
        if client is None or role not in client.roles:
            reason = "no valid token" if client is None else f"client {client.name!r} lacks the {role!r} role"
            log.warning("rejected websocket on %s: %s", ws.url.path, reason)
            await ws.close(code=1008)
            return None
        name = client.name

    # a browser drops the connection unless the subprotocol it offered is echoed back
    return (offered[0] if offered else ""), name


def error(message: str) -> str:
    return json.dumps({"type": "error", "message": message})


def describe(err: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in err.errors())


ONLY_ON = {"context_frame": "/ws/host", "action_result": "/ws/host", "apply": "/ws/ui"}


async def serve(ws: WebSocket, reply, handlers=None, on_hello=None, active=lambda: True, client=""):
    # Bad input gets an error reply; the socket stays open. Stops as soon as active() turns false.
    # `handlers` maps the endpoint's own message types to async handlers that validate their input.
    handlers = handlers or {}
    while active():
        raw = await ws.receive_text()
        if not active():
            return  # dropped while waiting: the message is ignored
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            await reply(error("invalid json"))
            continue
        if not isinstance(data, dict):
            await reply(error("message must be a JSON object"))
            continue

        msg_type = data.get("type")
        try:
            if msg_type == "hello":
                hello = HelloMsg.model_validate(data)
                if on_hello is not None:
                    on_hello(hello)
                await reply(json.dumps({"type": "ack", "ok": True}))
            elif msg_type == "feedback":
                fb = FeedbackMsg.model_validate(data)
                # accept feedback (store later)
                log.info("feedback %r %s (project %r, client %r)", fb.suggestion_id, fb.verdict, fb.project_id, client or None)
                await reply(json.dumps({"type": "ack_feedback", "ok": True}))
            elif msg_type in handlers:
                await handlers[msg_type](data)
            elif msg_type in ONLY_ON:
                await reply(error(f"{msg_type} is only accepted on {ONLY_ON[msg_type]}"))
            else:
                await reply(error("unknown msg type"))
        except ValidationError as err:
            await reply(error(f"invalid {msg_type}: {describe(err)}"))


@app.get("/health")
def health():
    return {"ok": True}


@app.websocket("/ws/host")
async def ws_host(ws: WebSocket):
    admitted = await admit(ws, "host")
    if admitted is None:
        return
    subprotocol, client = admitted
    await ws.accept(subprotocol=subprotocol or None)

    hello: HelloMsg | None = None

    def on_hello(msg: HelloMsg):
        nonlocal hello
        previous, hello = hello, msg
        log.info("project %r registered by host %r (client %r)", msg.project.id, msg.host.name, client or None)
        # register the new project before releasing the old one; re-sending the same project changes nothing
        if previous is None or previous.project.id != msg.project.id:
            hub.open(msg.project.id, ws)
            if previous is not None:
                hub.close(previous.project.id, ws)

    async def on_frame(data: dict):
        ctx = ContextFrameMsg.model_validate(data)
        if hello is None:
            await ws.send_text(error("send hello before context_frame"))
            return
        msg = build_suggestions(ctx)
        msg.project_id, msg.host = hello.project.id, hello.host.name
        suggestions = msg.model_dump_json()
        hub.publish(hello.project.id, suggestions)  # never waits on a UI
        await ws.send_text(suggestions)

    async def on_result(data: dict):
        result = ActionResultMsg.model_validate(data)
        if hello is None:
            await ws.send_text(error("send hello before action_result"))
            return
        problem = hub.report(hello.project.id, result)
        if problem is not None:
            await ws.send_text(error(problem))
            return
        log.info("action %s %s by host %r (client %r)", result.action_id, result.status, hello.host.name, client or None)

    try:
        await serve(ws, ws.send_text, {"context_frame": on_frame, "action_result": on_result}, on_hello, client=client)
    except WebSocketDisconnect:
        return
    finally:
        if hello is not None:
            hub.close(hello.project.id, ws)


@app.websocket("/ws/ui")
async def ws_ui(ws: WebSocket):
    admitted = await admit(ws, "ui")
    if admitted is None:
        return
    subprotocol, client = admitted
    # subscribed before the handshake completes, so nothing published meanwhile is missed
    ui = hub.subscribe(ws)

    async def reply(message: str):
        hub.send(ui, message)

    async def on_apply(data: dict):
        request = ApplyMsg.model_validate(data)
        log.info("apply %r requested for project %r (client %r)", request.suggestion_id, request.project_id, client or None)
        outcome = hub.apply(request.project_id, request.suggestion_id)
        if outcome is not None:
            await reply(outcome)

    try:
        await ws.accept(subprotocol=subprotocol or None)
        hub.start(ui)
        await serve(ws, reply, {"apply": on_apply}, active=lambda: not ui.dropped, client=client)
        # Dropped (fell behind, or its socket failed). Our close may still be waiting for the socket
        # to drain, and Starlette refuses receive_text() once a close has begun, so read raw messages
        # (ignored) until the connection ends; uvicorn's keepalive still cuts a client that never reads.
        while (await ws.receive())["type"] != "websocket.disconnect":
            pass
    except WebSocketDisconnect:
        return
    finally:
        hub.unsubscribe(ui)
