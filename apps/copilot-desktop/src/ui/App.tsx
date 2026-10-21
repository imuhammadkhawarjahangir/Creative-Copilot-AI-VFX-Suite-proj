import React, { useEffect, useRef, useState } from "react";
import SuggestionCard from "./components/SuggestionCard";

type Suggestion = {
  id: string;
  kind: string;
  title: string;
  confidence: number;
  rationale: string;
  action: { op: string; params: Record<string, any> };
};

const WS_URL = "ws://localhost:8000/ws/ui";

export default function App() {
  const [items, setItems] = useState<Suggestion[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let stopped = false;
    let retry: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        ws.send(JSON.stringify({
          type: "hello",
          host: { name: "copilot-ui", version: "0.1" },
          project: { id: "ui-session", fps: 24 }
        }));
      };

      ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data);
          if (msg.type === "suggestions") {
            setItems(msg.items);
          }
        } catch {}
      };

      // The service may start after the UI, or restart: keep retrying.
      ws.onclose = () => {
        setConnected(false);
        if (!stopped) retry = setTimeout(connect, 1000);
      };
    };

    connect();
    return () => {
      stopped = true;
      clearTimeout(retry);
      wsRef.current?.close();
    };
  }, []);

  const sendFeedback = (suggestionId: string, verdict: "accepted" | "rejected") => {
    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "feedback", suggestion_id: suggestionId, verdict }));
    }
  };

  return (
    <div style={{ padding: 18, fontFamily: "system-ui" }}>
      <h1 style={{ margin: 0 }}>SENSEI</h1>
      <p style={{ marginTop: 6, opacity: 0.75 }}>
        Real-time creative copilot — suggestions stream in as your timeline context changes.
      </p>
      <p style={{ fontSize: 12, opacity: 0.6 }}>
        {connected ? "Connected to SENSEI service" : "Connecting to SENSEI service…"}
      </p>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        {items.map((s) => (
          <SuggestionCard key={s.id} s={s} onFeedback={(verdict) => sendFeedback(s.id, verdict)} />
        ))}
      </div>
    </div>
  );
}
