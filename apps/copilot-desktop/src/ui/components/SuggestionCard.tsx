import React, { useState } from "react";

type Verdict = "accepted" | "rejected";

export default function SuggestionCard({ s, onFeedback }: { s: any; onFeedback: (verdict: Verdict) => void }) {
  const [status, setStatus] = useState<"idle" | Verdict>("idle");

  const sendFeedback = (verdict: Verdict) => {
    setStatus(verdict);
    onFeedback(verdict);
  };

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
        <button disabled={status !== "idle"} onClick={() => sendFeedback("accepted")}>
          Apply
        </button>
        <button disabled={status !== "idle"} onClick={() => sendFeedback("rejected")}>
          Dismiss
        </button>
        {status !== "idle" && <span style={{ opacity: 0.7 }}>✓ {status}</span>}
      </div>
    </div>
  );
}
