import pytest
from fastapi.testclient import TestClient

from sensei_service import main
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
    for _ in range(4):
        store.record("sug_1234abcd", "rejected", VFX_ARM)
    vfx = suggestions(store)["vfx"]
    assert vfx.confidence == round((0.74 * PRIOR_STRENGTH + 0) / (PRIOR_STRENGTH + 4), 2)  # 0.41
    assert "0 applied, 4 dismissed" in vfx.rationale

    ranked = [i.kind for i in build_suggestions(ContextFrameMsg.model_validate(FRAME), store.adjust).items]
    assert ranked[-1] == "vfx"  # was second, now below everything else


def test_applies_raise_a_suggestion_type():
    store = FeedbackStore(":memory:")
    arm = "keyframe/snap_keyframe_to_time"
    for _ in range(5):
        store.record("sug_1234abcd", "accepted", arm)
    assert suggestions(store)["keyframe"].confidence > 0.70


def test_learning_survives_a_restart(tmp_path):
    path = str(tmp_path / "feedback.sqlite3")
    first = FeedbackStore(path)
    first.record("sug_1234abcd", "rejected", VFX_ARM)
    first.record("sug_1234abcd", "accepted", VFX_ARM, project_id="p", client="desktop")
    first.record("sug_00000000", "rejected", None)  # not current: kept, not learned from
    first.db.close()
    assert FeedbackStore(path).counts == {VFX_ARM: (1, 1)}


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


def test_feedback_over_the_wire_changes_later_suggestions(client):
    hello = {"type": "hello", "host": {"name": "test", "version": "0"}, "project": {"id": "p", "fps": 24}}
    with client.websocket_connect("/ws/ui") as ui, client.websocket_connect("/ws/host") as host:
        host.send_json(hello)
        host.receive_json()
        host.send_json(FRAME)
        first = host.receive_json()
        ui.receive_json()
        vfx = next(i for i in first["items"] if i["kind"] == "vfx")
        for _ in range(3):
            ui.send_json({"type": "feedback", "suggestion_id": vfx["id"], "verdict": "rejected", "project_id": "p"})
            assert ui.receive_json()["type"] == "ack_feedback"

        host.send_json(FRAME)
        later = next(i for i in host.receive_json()["items"] if i["kind"] == "vfx")
        assert later["id"] == vfx["id"]  # same suggestion...
        assert later["confidence"] < vfx["confidence"] and "3 dismissed" in later["rationale"]  # ...less confident
