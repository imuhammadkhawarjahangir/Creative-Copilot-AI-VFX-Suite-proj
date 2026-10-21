import re

import pytest
from fastapi.testclient import TestClient

from sensei_service.clients import Client, digest
from sensei_service.config import settings
from sensei_service.engine.suggest import build_suggestions
from sensei_service import main
from sensei_service.feedback import FeedbackStore
from sensei_service.main import app, hub
from sensei_service.protocol import ContextFrameMsg, checked_params

ACK = {"type": "ack", "ok": True}
FRAME = {
    "type": "context_frame",
    "ts": 1,
    "playhead": 14.0,
    "scene": {"shot_id": "A012_C003", "emotion": "tense", "lighting": "low-key", "notes": "neon spill"},
    "timeline": {"clips": [], "cuts": [14.2], "audio": {"beats": [13.9, 14.55, 15.2]}},
}


def hello(project_id="p"):
    return {"type": "hello", "host": {"name": "test", "version": "0"}, "project": {"id": project_id, "fps": 24}}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "store", FeedbackStore(":memory:"))  # fresh learning state per test
    hub.clients.clear()
    hub.latest.clear()
    hub.hosts.clear()
    hub.owners.clear()
    hub.actions.clear()
    # As a context manager, every WebSocket session shares one event loop, as under uvicorn;
    # otherwise each session gets its own loop and cross-session wake-ups can be lost.
    with TestClient(app) as test_client:
        yield test_client


def start(host, project_id="p"):
    host.send_json(hello(project_id))
    assert host.receive_json() == ACK
    host.send_json(FRAME)
    return host.receive_json()


def item_of(suggestions, kind):
    return next(i for i in suggestions["items"] if i["kind"] == kind)


def test_apply_reaches_the_host_and_the_result_reaches_the_ui(client):
    with client.websocket_connect("/ws/ui") as ui, client.websocket_connect("/ws/host") as host:
        suggestions = start(host)
        assert ui.receive_json() == suggestions
        item = item_of(suggestions, "color_grade")

        ui.send_json({"type": "apply", "project_id": "p", "suggestion_id": item["id"]})
        action = host.receive_json()
        assert action["type"] == "apply_action" and re.fullmatch(r"act_[0-9a-f]{12}", action["action_id"])
        assert action["project_id"] == "p"  # so the host can refuse it if it has switched since
        assert (action["suggestion_id"], action["op"], action["params"]) == (item["id"], item["action"]["op"], item["action"]["params"])

        host.send_json({"type": "action_result", "action_id": action["action_id"], "status": "applied"})
        assert ui.receive_json() == {"type": "action_result", "project_id": "p", "suggestion_id": item["id"],
                                     "action_id": action["action_id"], "status": "applied", "detail": None}


def test_applying_twice_reuses_the_action_id(client):
    # so a host can recognise the repeat and not apply it twice
    with client.websocket_connect("/ws/ui") as ui, client.websocket_connect("/ws/host") as host:
        suggestions = start(host)
        ui.receive_json()
        request = {"type": "apply", "project_id": "p", "suggestion_id": item_of(suggestions, "vfx")["id"]}
        ui.send_json(request)
        first = host.receive_json()
        ui.send_json(request)
        assert host.receive_json()["action_id"] == first["action_id"]


def test_the_ui_cannot_supply_its_own_action(client):
    with client.websocket_connect("/ws/ui") as ui, client.websocket_connect("/ws/host") as host:
        suggestions = start(host)
        ui.receive_json()
        item = item_of(suggestions, "vfx")
        ui.send_json({"type": "apply", "project_id": "p", "suggestion_id": item["id"],
                      "op": "delete_timeline", "params": {"everything": True}})
        action = host.receive_json()
        assert (action["op"], action["params"]) == (item["action"]["op"], item["action"]["params"])


@pytest.mark.parametrize("with_host, suggestion_id, detail", [
    (False, "sug_1234abcd", "no host is connected"),
    (True, "sug_1234abcd", "no longer current"),
])
def test_apply_failures_are_reported_to_the_ui(client, with_host, suggestion_id, detail):
    with client.websocket_connect("/ws/ui") as ui:
        if with_host:
            with client.websocket_connect("/ws/host") as host:
                start(host)
                ui.receive_json()
                ui.send_json({"type": "apply", "project_id": "p", "suggestion_id": suggestion_id})
                outcome = ui.receive_json()
        else:
            ui.send_json({"type": "apply", "project_id": "p", "suggestion_id": suggestion_id})
            outcome = ui.receive_json()
    assert outcome["type"] == "action_result" and outcome["status"] == "failed" and detail in outcome["detail"]


def test_messages_are_only_accepted_on_their_endpoint(client):
    with client.websocket_connect("/ws/host") as host:
        host.send_json({"type": "apply", "project_id": "p", "suggestion_id": "sug_1234abcd"})
        assert "only accepted on /ws/ui" in host.receive_json()["message"]
    with client.websocket_connect("/ws/ui") as ui:
        ui.send_json({"type": "action_result", "action_id": "act_0123456789ab", "status": "applied"})
        assert "only accepted on /ws/host" in ui.receive_json()["message"]


def test_a_host_cannot_report_for_another_project(client):
    with client.websocket_connect("/ws/ui") as ui, client.websocket_connect("/ws/host") as a, \
            client.websocket_connect("/ws/host") as b:
        suggestions = start(a, "edit")
        ui.receive_json()
        start(b, "grade")
        ui.receive_json()
        ui.send_json({"type": "apply", "project_id": "edit", "suggestion_id": item_of(suggestions, "vfx")["id"]})
        action = a.receive_json()

        b.send_json({"type": "action_result", "action_id": action["action_id"], "status": "applied"})
        assert "unknown action_id" in b.receive_json()["message"]


def test_closing_a_project_forgets_its_actions(client):
    with client.websocket_connect("/ws/ui") as ui:
        with client.websocket_connect("/ws/host") as host:
            suggestions = start(host)
            ui.receive_json()
            ui.send_json({"type": "apply", "project_id": "p", "suggestion_id": item_of(suggestions, "vfx")["id"]})
            host.receive_json()
            assert len(hub.actions) == 1
        assert ui.receive_json()["type"] == "project_closed"
    assert hub.actions == {}


@pytest.mark.parametrize("playhead", [x / 4 for x in range(40, 82)])
@pytest.mark.parametrize("emotion, lighting, notes", [("tense", "low-key", "neon"), ("calm", "day", ""), (None, "noir", "neon")])
def test_engine_actions_are_within_bounds(playhead, emotion, lighting, notes):
    frame = {**FRAME, "playhead": playhead,
             "scene": {"shot_id": "X", "emotion": emotion, "lighting": lighting, "notes": notes},
             "timeline": {"cuts": [14.2], "audio": {"beats": [10 + i * 0.65 for i in range(16)]}}}
    for item in build_suggestions(ContextFrameMsg.model_validate(frame)).items:
        checked_params(item.action.op, item.action.params)


@pytest.mark.parametrize("op, params", [
    ("apply_vfx_preset", {"preset": "neon_bloom_02", "threshold": 0.78, "intensity": 5, "radius": 0.22}),
    ("apply_color_grade", {"lut": "../../etc/passwd", "contrast": 1.1, "sat": 0.9}),
    ("apply_transition_at_cut", {"type": "cross_dissolve", "frames": 8, "at": 14.2, "extra": 1}),
    ("delete_timeline", {}),
])
def test_out_of_bounds_actions_are_refused(op, params):
    with pytest.raises(ValueError):
        checked_params(op, params)


def test_the_hub_rechecks_bounds_before_sending():
    # even an action the service itself produced is re-checked before it reaches a host
    from sensei_service.main import Hub

    class Host:
        sent = []

        async def send_text(self, message):
            self.sent.append(message)

    local = Hub()
    host = Host()
    local.open("p", host, "")
    local.latest["p"] = ('{"type": "suggestions", "ts": 1, "project_id": "p", "host": "t", "items": [{"id": "sug_1234abcd", '
                         '"kind": "vfx", "title": "t", "confidence": 0.5, "rationale": "r", "action": {"op": "apply_vfx_preset", '
                         '"params": {"preset": "x", "threshold": 9, "intensity": 0.1, "radius": 0.1}}}]}')
    outcome = local.apply("p", "sug_1234abcd")
    assert outcome is not None and '"status":"failed"' in outcome and "bounds" in outcome
    assert host.sent == [] and local.actions == {}


@pytest.fixture
def host_clients(monkeypatch):
    # per-client mode with two different host clients and a UI client
    tokens = {"resolve": "resolve-token", "intruder": "intruder-token", "desktop": "ui-token"}
    monkeypatch.setattr(settings, "clients", [
        Client("resolve", ("host",), digest(tokens["resolve"])),
        Client("intruder", ("host",), digest(tokens["intruder"])),
        Client("desktop", ("ui",), digest(tokens["desktop"])),
    ])
    return {name: [f"sensei.token.{token}"] for name, token in tokens.items()}


def test_another_client_cannot_take_over_a_project(client, host_clients):
    with client.websocket_connect("/ws/ui", subprotocols=host_clients["desktop"]) as ui, \
            client.websocket_connect("/ws/host", subprotocols=host_clients["resolve"]) as owner, \
            client.websocket_connect("/ws/host", subprotocols=host_clients["intruder"]) as intruder:
        suggestions = start(owner)
        ui.receive_json()
        intruder.send_json(hello("p"))
        assert "held by another client" in intruder.receive_json()["message"]

        ui.send_json({"type": "apply", "project_id": "p", "suggestion_id": item_of(suggestions, "vfx")["id"]})
        action = owner.receive_json()
        assert action["type"] == "apply_action"
        # the intruder received no action (its next message is the error below) and can't report on it
        intruder.send_json({"type": "action_result", "action_id": action["action_id"], "status": "applied"})
        assert "send hello" in intruder.receive_json()["message"]


def test_a_host_refused_another_project_no_longer_hosts_its_old_one(client, host_clients):
    # its editor has left the old project, so that project's actions must not reach it
    with client.websocket_connect("/ws/ui", subprotocols=host_clients["desktop"]) as ui, \
            client.websocket_connect("/ws/host", subprotocols=host_clients["resolve"]) as owner, \
            client.websocket_connect("/ws/host", subprotocols=host_clients["intruder"]) as switcher:
        own = start(switcher, "q")
        ui.receive_json()
        start(owner, "p")
        ui.receive_json()

        switcher.send_json(hello("p"))
        assert "held by another client" in switcher.receive_json()["message"]
        assert ui.receive_json() == {"type": "project_closed", "project_id": "q"}
        assert "q" not in hub.hosts and "q" not in hub.owners

        ui.send_json({"type": "apply", "project_id": "q", "suggestion_id": item_of(own, "vfx")["id"]})
        assert "no host is connected" in ui.receive_json()["detail"]


def test_another_host_of_the_same_client_may_join(client, host_clients):
    with client.websocket_connect("/ws/host", subprotocols=host_clients["resolve"]) as first, \
            client.websocket_connect("/ws/host", subprotocols=host_clients["resolve"]) as second:
        start(first)
        second.send_json(hello("p"))
        assert second.receive_json() == ACK
        assert len(hub.hosts["p"]) == 2


def test_only_the_hosts_an_action_was_sent_to_may_report_it(client):
    with client.websocket_connect("/ws/ui") as ui, client.websocket_connect("/ws/host") as host:
        suggestions = start(host)
        ui.receive_json()
        ui.send_json({"type": "apply", "project_id": "p", "suggestion_id": item_of(suggestions, "vfx")["id"]})
        action = host.receive_json()
        with client.websocket_connect("/ws/host") as late:  # joins the project after the action was sent
            late.send_json(hello("p"))
            assert late.receive_json() == ACK
            late.send_json({"type": "action_result", "action_id": action["action_id"], "status": "applied"})
            assert "unknown action_id" in late.receive_json()["message"]
