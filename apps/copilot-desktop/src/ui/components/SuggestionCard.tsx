import React from "react";
import type { ActionOutcome, Suggestion, Verdict } from "../types";

type Props = {
  s: Suggestion;
  status: "idle" | Verdict;
  outcomes: ActionOutcome[]; // the hosts' answers, one per host when the project has several
  note?: string; // why the latest decision didn't go through
  disabled: boolean;
  onFeedback: (verdict: Verdict) => void;
};

const OUTCOME_TEXT: Record<ActionOutcome["status"], string> = {
  applied: "Applied in the host",
  marked: "Marked in the host for a manual edit",
  duplicate: "Already applied in the host",
  rejected: "The host refused it"
};

export default function SuggestionCard({ s, status, outcomes, note, disabled, onFeedback }: Props) {
  return (
    <div style={{
      border: "1px solid rgba(0,0,0,0.12)",
      borderRadius: 12,
      padding: 12
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
        <strong>{s.title}</strong>
        <span style={{ opacity: 0.7 }}>{Math.round(s.confidence * 100)}%</span>
      </div>
      <div style={{ marginTop: 6, opacity: 0.8 }}>{s.rationale}</div>

      <div style={{ marginTop: 10, fontSize: 12, opacity: 0.75 }}>
        <div><b>Kind:</b> {s.kind}</div>
        <div><b>Op:</b> {s.action?.op}</div>
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <button disabled={disabled || status !== "idle"} onClick={() => onFeedback("accepted")}>
          Apply
        </button>
        <button disabled={disabled || status !== "idle"} onClick={() => onFeedback("rejected")}>
          Dismiss
        </button>
        {status !== "idle" && <span style={{ opacity: 0.7 }}>✓ {status}</span>}
      </div>
      {outcomes.length > 0 && (
        <div style={{ marginTop: 8, fontSize: 12, opacity: 0.8 }}>
          {outcomes.map((outcome, i) => (
            <div key={i} className="outcome">
              {OUTCOME_TEXT[outcome.status]}{outcome.detail ? `: ${outcome.detail}` : ""}
            </div>
          ))}
        </div>
      )}
      {note && (
        <div className="note" style={{ marginTop: 8, fontSize: 12, opacity: 0.8 }}>
          {note}
        </div>
      )}
    </div>
  );
}
