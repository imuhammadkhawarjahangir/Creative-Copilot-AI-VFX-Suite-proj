import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from sensei_service.engine.suggest import build_suggestions
from sensei_service.main import app, hub
from sensei_service.protocol import ContextFrameMsg

HELLO = {"type": "hello", "host": {"name": "test", "version": "0"}, "project": {"id": "p", "fps": 24}}
ACK = {"type": "ack", "ok": True}
FRAME = {
    "type": "context_frame",
    "ts": 1,
    "playhead": 14.0,
    "scene": {"shot_id": "A012_C003", "emotion": "tense", "lighting": "low-key", "notes": "neon spill"},
    "timeline": {
        "clips": [{"id": "c1", "in": 10.0, "out": 14.2, "kind": "video"}],
        "cuts": [14.2],
        "audio": {"bpm": 92, "beats": [13.9, 14.55, 15.2]},
    },
}


def hello(project_id="p", host="test"):
    return {**HELLO, "host": {"name": host, "version": "0"}, "project": {"id": project_id, "fps": 24}}


def start_host(host, project_id="p", name="test"):
    host.send_json(hello(project_id, name))
    assert host.receive_json() == ACK


@pytest.fixture
def client():
    hub.clients.clear()
    hub.latest.clear()
    hub.hosts.clear()
    return TestClient(app)


def test_health(client):
    assert client.get("/health").json() == {"ok": True}


def test_host_frame_is_broadcast_to_ui(client):
    with client.websocket_connect("/ws/ui") as ui, client.websocket_connect("/ws/host") as host:
        ui.send_json(HELLO)
        assert ui.receive_json() == ACK  # ui is registered with the hub
        start_host(host)

        host.send_json(FRAME)
        to_host = host.receive_json()
        assert to_host["type"] == "suggestions"
        assert (to_host["project_id"], to_host["host"]) == ("p", "test")
        assert [i["kind"] for i in to_host["items"]] == ["color_grade", "vfx", "transition", "keyframe"]
        assert ui.receive_json() == to_host


def test_ui_gets_latest_suggestions_on_connect(client):
    with client.websocket_connect("/ws/host") as host:
        start_host(host)
        host.send_json(FRAME)
        latest = host.receive_json()
        with client.websocket_connect("/ws/ui") as ui:
            assert ui.receive_json() == latest


def test_frame_before_hello_is_rejected(client):
    with client.websocket_connect("/ws/host") as host:
        host.send_json(FRAME)
        assert "send hello before context_frame" in host.receive_json()["message"]


def test_projects_are_separate_streams(client):
    with client.websocket_connect("/ws/host") as a, client.websocket_connect("/ws/host") as b:
        start_host(a, "edit", "premiere")
        start_host(b, "grade", "resolve")
        a.send_json(FRAME)
        a.receive_json()
        b.send_json({**FRAME, "scene": {**FRAME["scene"], "emotion": "calm", "lighting": "day"}})
        b.receive_json()

        with client.websocket_connect("/ws/ui") as ui:
            streams = {m["project_id"]: m for m in (ui.receive_json(), ui.receive_json())}
        assert streams["edit"]["host"] == "premiere"
        assert streams["grade"]["host"] == "resolve"
        assert streams["edit"]["items"] != streams["grade"]["items"]


def test_ui_is_told_when_a_project_closes(client):
    with client.websocket_connect("/ws/ui") as ui:
        ui.send_json(HELLO)
        assert ui.receive_json() == ACK
        with client.websocket_connect("/ws/host") as host:
            start_host(host)
            host.send_json(FRAME)
            host.receive_json()
            assert ui.receive_json()["type"] == "suggestions"
        assert ui.receive_json() == {"type": "project_closed", "project_id": "p"}

    with client.websocket_connect("/ws/ui") as ui:
        ui.send_json(HELLO)
        assert ui.receive_json() == ACK  # closed project is no longer replayed on connect


def test_suggestion_ids_are_stable_and_content_based():
    ctx = ContextFrameMsg.model_validate(FRAME)
    first = {i.kind: i.id for i in build_suggestions(ctx).items}
    assert first == {i.kind: i.id for i in build_suggestions(ctx).items}
    assert len(set(first.values())) == len(first)

    later = {i.kind: i.id for i in build_suggestions(ContextFrameMsg.model_validate({**FRAME, "playhead": 14.6})).items}
    assert later["color_grade"] == first["color_grade"]
    assert later["keyframe"] != first["keyframe"]  # snaps to a different beat


@pytest.mark.parametrize("payload, expected", [
    ("not json", "invalid json"),
    ("[]", "must be a JSON object"),
    ('{"type": "context_frame", "ts": 1, "playhead": 1, "timeline": {}}', "scene: Field required"),
    ('{"type": "nope"}', "unknown msg type"),
])
def test_bad_input_gets_error_and_socket_stays_open(client, payload, expected):
    with client.websocket_connect("/ws/host") as host:
        host.send_text(payload)
        reply = host.receive_json()
        assert reply["type"] == "error"
        assert expected in reply["message"]

        host.send_json(HELLO)
        assert host.receive_json() == ACK


def test_feedback_is_validated(client):
    with client.websocket_connect("/ws/ui") as ui:
        ui.send_json({"type": "feedback", "suggestion_id": "sug_1", "verdict": "accepted"})
        assert ui.receive_json() == {"type": "ack_feedback", "ok": True}

        ui.send_json({"type": "feedback", "verdict": "loved it"})
        reply = ui.receive_json()
        assert reply["type"] == "error"
        assert "invalid feedback" in reply["message"]


def test_ui_cannot_send_context_frames(client):
    with client.websocket_connect("/ws/ui") as ui:
        ui.send_json(FRAME)
        assert "only accepted on /ws/host" in ui.receive_json()["message"]


@pytest.mark.parametrize("path", ["/ws/host", "/ws/ui"])
def test_foreign_browser_origin_is_rejected(client, path):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(path, headers={"origin": "https://evil.example"}):
            pass
    assert exc.value.code == 1008


@pytest.mark.parametrize("path", ["/ws/host", "/ws/ui"])
def test_allowed_origin_is_accepted(client, path):
    with client.websocket_connect(path, headers={"origin": "http://127.0.0.1:5180"}) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json() == ACK
