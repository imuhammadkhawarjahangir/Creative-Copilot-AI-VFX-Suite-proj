import pytest
from fastapi.testclient import TestClient

from sensei_service import feedback, main
from sensei_service.engine.suggest import build_suggestions
from sensei_service.feedback import PRIOR_STRENGTH, FeedbackStore, arm_of
from sensei_service.main import app, hub
from sensei_service.protocol import ContextFrameMsg

FRAME = {
    "type": "context_frame",
    "ts": 1,
    "playhead": 14.0,
    "scene": {"shot_id": "A012_C003", "emotion": "tense", "lighting": "low-key", "notes": "neon spill"},
    "timeline": {"clips": [], "cuts": [14.2], "audio": {"beats": [13.9, 14.55, 15.2]}},
}
HELLO = {"type": "hello", "host": {"name": "test", "version": "0"}, "project": {"id": "p", "fps": 24}}
VFX_ARM = "vfx/apply_vfx_preset/neon_bloom_02"


def suggestions(store):
    return {i.kind: i for i in build_suggestions(ContextFrameMsg.model_validate(FRAME), store.adjust).items}


def test_no_feedback_changes_nothing():
    plain = {i.kind: i for i in build_suggestions(ContextFrameMsg.model_validate(FRAME)).items}
    learned = suggestions(FeedbackStore(":memory:"))
    assert {k: (v.confidence, v.rationale) for k, v in learned.items()} == \
           {k: (v.confidence, v.rationale) for k, v in plain.items()}


def test_dismissals_lower_a_suggestion_type_and_rerank_it():
    store = FeedbackStore(":memory:")
    for n in range(4):  # neon bloom dismissed on four shots
        store.vote("", "p", f"sug_0000000{n}", VFX_ARM, "rejected")
    vfx = suggestions(store)["vfx"]
    assert vfx.confidence == round((0.74 * PRIOR_STRENGTH + 0) / (PRIOR_STRENGTH + 4), 2)  # 0.41
    assert "0 applied, 4 dismissed" in vfx.rationale

    ranked = [i.kind for i in build_suggestions(ContextFrameMsg.model_validate(FRAME), store.adjust).items]
    assert ranked[-1] == "vfx"  # was second, now below everything else


def test_applies_raise_a_suggestion_type():
    store = FeedbackStore(":memory:")
    arm = "keyframe/snap_keyframe_to_time"
    for n in range(5):
        store.vote("", "p", f"sug_0000000{n}", arm, "accepted")
    assert suggestions(store)["keyframe"].confidence > 0.70


def test_a_client_has_one_vote_per_project_and_suggestion():
    store = FeedbackStore(":memory:")
    for _ in range(3):
        store.vote("desktop", "p", "sug_1234abcd", VFX_ARM, "rejected")
    assert store.counts[VFX_ARM] == (0, 1)
    store.vote("desktop", "p", "sug_1234abcd", VFX_ARM, "accepted")  # changed its mind: the latest verdict counts
    assert store.counts[VFX_ARM] == (1, 0)

    store.vote("laptop", "p", "sug_1234abcd", VFX_ARM, "rejected")  # another client has its own vote
    store.vote("desktop", "q", "sug_1234abcd", VFX_ARM, "rejected")  # so does the same suggestion in another project
    assert store.counts[VFX_ARM] == (1, 2)


def test_only_the_latest_votes_are_kept(monkeypatch):
    monkeypatch.setattr(feedback, "MAX_VOTES", 3)
    store = FeedbackStore(":memory:")
    for n in range(2):
        store.vote("", "p", f"sug_a000000{n}", VFX_ARM, "rejected")
    for n in range(3):
        store.vote("", "p", f"sug_b000000{n}", VFX_ARM, "accepted")
    assert store.counts[VFX_ARM] == (3, 0)  # the two dismissals were the oldest
    assert store.db.execute("SELECT COUNT(*) FROM votes").fetchone() == (3,)


def test_learning_survives_a_restart(tmp_path):
    path = str(tmp_path / "feedback.sqlite3")
    first = FeedbackStore(path)
    first.vote("", "p", "sug_1234abcd", VFX_ARM, "rejected")
    first.vote("desktop", "p", "sug_1234abcd", VFX_ARM, "accepted")
    first.db.close()

    again = FeedbackStore(path)
    assert again.counts == {VFX_ARM: (1, 1)}
    again.vote("desktop", "p", "sug_1234abcd", VFX_ARM, "rejected")  # still one vote per client after a restart
    assert again.counts == {VFX_ARM: (0, 2)}


def test_arms_distinguish_presets_not_parameters():
    a = arm_of("vfx", "apply_vfx_preset", {"preset": "neon_bloom_02", "intensity": 0.35})
    b = arm_of("vfx", "apply_vfx_preset", {"preset": "neon_bloom_02", "intensity": 0.9})
    c = arm_of("vfx", "apply_vfx_preset", {"preset": "film_halation", "intensity": 0.35})
    assert a == b != c


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "store", FeedbackStore(":memory:"))
    hub.clients.clear()
    hub.latest.clear()
    hub.hosts.clear()
    hub.owners.clear()
    hub.actions.clear()
    with TestClient(app) as test_client:
        yield test_client


def open_project(host, ui) -> dict:
    # The host opens project "p" and sends a frame; returns its vfx suggestion.
    host.send_json(HELLO)
    host.receive_json()
    host.send_json(FRAME)
    items = host.receive_json()["items"]
    ui.receive_json()
    return next(i for i in items if i["kind"] == "vfx")


def test_feedback_over_the_wire_changes_later_suggestions(client):
    with client.websocket_connect("/ws/ui") as ui, client.websocket_connect("/ws/host") as host:
        vfx = open_project(host, ui)
        for _ in range(3):
            ui.send_json({"type": "feedback", "suggestion_id": vfx["id"], "verdict": "rejected", "project_id": "p"})
            assert ui.receive_json() == {"type": "ack_feedback", "ok": True}

        host.send_json(FRAME)
        later = next(i for i in host.receive_json()["items"] if i["kind"] == "vfx")
        assert later["id"] == vfx["id"]  # same suggestion...
        assert later["confidence"] < vfx["confidence"]  # ...less confident, by one vote however often it's sent
        assert "0 applied, 1 dismissed" in later["rationale"]


def test_hosts_cannot_send_feedback(client):
    with client.websocket_connect("/ws/ui") as ui, client.websocket_connect("/ws/host") as host:
        vfx = open_project(host, ui)
        host.send_json({"type": "feedback", "suggestion_id": vfx["id"], "verdict": "rejected", "project_id": "p"})
        assert host.receive_json() == {"type": "error", "message": "feedback is only accepted on /ws/ui"}
    assert main.store.counts == {}


def test_feedback_on_a_suggestion_that_isnt_current_is_not_stored(client):
    with client.websocket_connect("/ws/ui") as ui, client.websocket_connect("/ws/host") as host:
        vfx = open_project(host, ui)
        for project_id, suggestion_id in [("p", "sug_00000000"), ("not-open", vfx["id"])]:
            ui.send_json({"type": "feedback", "suggestion_id": suggestion_id, "verdict": "rejected",
                          "project_id": project_id})
            assert ui.receive_json() == {"type": "ack_feedback", "ok": False,
                                         "detail": "this suggestion is no longer current"}
    assert main.store.db.execute("SELECT COUNT(*) FROM votes").fetchone() == (0,)


def test_an_apply_counts_once_and_only_when_sent(client, monkeypatch):
    with client.websocket_connect("/ws/ui") as ui, client.websocket_connect("/ws/host") as host:
        vfx = open_project(host, ui)
        apply = {"type": "apply", "project_id": "p", "suggestion_id": vfx["id"]}

        def out_of_bounds(op, params):
            raise ValueError(op)

        with monkeypatch.context() as patch:
            patch.setattr(main, "checked_params", out_of_bounds)
            ui.send_json(apply)
            assert ui.receive_json()["status"] == "failed"
        assert main.store.counts == {}  # nothing was sent, so nothing is learned

        for _ in range(2):
            ui.send_json(apply)
            assert host.receive_json()["type"] == "apply_action"
    assert main.store.counts == {VFX_ARM: (1, 0)}
