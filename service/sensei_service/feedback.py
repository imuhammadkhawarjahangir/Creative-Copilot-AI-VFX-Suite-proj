"""Stores feedback and learns from it which kinds of suggestion this user takes.

Each suggestion *type* (an "arm": kind, op and its main param, e.g. the neon bloom preset) has a
Beta-Bernoulli preference whose prior mean is the rule's own confidence, worth PRIOR_STRENGTH
decisions. Applying a suggestion counts as a success, dismissing it as a failure; the posterior
mean replaces the rule's confidence before suggestions are ranked.
"""
import sqlite3
import threading
import time
from pathlib import Path

PRIOR_STRENGTH = 5  # how many decisions the rule's confidence is worth
SIGNATURE_PARAM = {  # the param that distinguishes suggestions of the same kind
    "apply_color_grade": "lut",
    "apply_transition_at_cut": "type",
    "apply_vfx_preset": "preset",
}


def arm_of(kind: str, op: str, params: dict) -> str:
    key = SIGNATURE_PARAM.get(op)
    return f"{kind}/{op}" + (f"/{params.get(key)}" if key else "")


class FeedbackStore:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # The store is created at import but used from whichever thread runs the event loop (several,
        # under the test client), so the connection isn't bound to one thread; a lock serialises access.
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS feedback (
            ts REAL NOT NULL, client TEXT, project_id TEXT, suggestion_id TEXT NOT NULL,
            arm TEXT, verdict TEXT NOT NULL)""")
        self.db.commit()
        # per-arm (accepted, rejected) counts, kept in memory and updated as feedback arrives
        self.counts: dict[str, tuple[int, int]] = {}
        for arm, verdict, n in self.db.execute(
                "SELECT arm, verdict, COUNT(*) FROM feedback WHERE arm IS NOT NULL GROUP BY arm, verdict"):
            self._count(arm, verdict, n)

    def _count(self, arm: str, verdict: str, n: int = 1):
        accepted, rejected = self.counts.get(arm, (0, 0))
        if verdict == "rejected":
            rejected += n
        else:  # accepted, modified (applied with tweaks)
            accepted += n
        self.counts[arm] = (accepted, rejected)

    def record(self, suggestion_id: str, verdict: str, arm: str | None = None,
               project_id: str | None = None, client: str | None = None):
        # arm is None when the suggestion isn't current (unknown content): stored, but not learned from
        with self.lock:
            self.db.execute("INSERT INTO feedback VALUES (?, ?, ?, ?, ?, ?)",
                            (time.time(), client, project_id, suggestion_id, arm, verdict))
            self.db.commit()
            if arm is not None:
                self._count(arm, verdict)

    def adjust(self, item):
        # Replaces a suggestion's rule confidence with the posterior mean for its arm.
        accepted, rejected = self.counts.get(arm_of(item.kind, item.action.op, item.action.params), (0, 0))
        if accepted + rejected == 0:
            return item
        prior = item.confidence
        item.confidence = round((prior * PRIOR_STRENGTH + accepted) / (PRIOR_STRENGTH + accepted + rejected), 2)
        item.rationale += (f" (Adjusted from {prior:.0%} by your feedback on this kind of suggestion: "
                           f"{accepted} applied, {rejected} dismissed.)")
        return item
