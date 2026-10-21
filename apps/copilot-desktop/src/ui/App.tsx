import React, { useEffect, useRef, useState } from "react";
import SuggestionCard from "./components/SuggestionCard";
import type { ActionOutcome, Suggestion, Verdict } from "./types";

type ProjectStream = { host: string; items: Suggestion[] };

// used when the page runs outside Electron (no preload); Electron passes SENSEI_SERVICE_URL instead
const DEFAULT_SERVICE_URL = "ws://127.0.0.1:8000";

// Outcomes that changed nothing and recorded nothing: "failed" (the service couldn't send it; only the UI
// that applied gets it) and "rejected" (the host refused it). The suggestion can be decided again.
const UNDONE: ReadonlySet<ActionOutcome["status"]> = new Set(["failed", "rejected"]);

function without<T>(record: Record<string, T>, key: string): Record<string, T> {
  const next = { ...record };
  delete next[key];
  return next;
}

export default function App() {
  const [projects, setProjects] = useState<Record<string, ProjectStream>>({});
  // keyed by "<project>/<suggestion id>" so a verdict survives the card being remounted
  const [verdicts, setVerdicts] = useState<Record<string, Verdict>>({});
  const [outcomes, setOutcomes] = useState<Record<string, ActionOutcome>>({});
  // why the service didn't record a Dismiss, by the same key
  const [refusals, setRefusals] = useState<Record<string, string>>({});
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let stopped = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    // optional shared secret, presented as a WebSocket subprotocol (see docs/PROTOCOL.md)
    let protocols: string[] = [];
    let url = `${DEFAULT_SERVICE_URL}/ws/ui`;

    const connect = () => {
      const ws = new WebSocket(url, protocols);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        // the service replays the latest suggestions of every open project right after connect
        setProjects({});
      };

      ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data);
          if (msg.type === "suggestions") {
            setProjects((prev) => ({ ...prev, [msg.project_id]: { host: msg.host, items: msg.items } }));
          } else if (msg.type === "action_result") {
            const key = `${msg.project_id}/${msg.suggestion_id}`;
            const outcome: ActionOutcome = { status: msg.status, detail: msg.detail };
            setOutcomes((prev) => ({ ...prev, [key]: outcome }));
            if (UNDONE.has(msg.status)) setVerdicts((prev) => without(prev, key));
          } else if (msg.type === "ack_feedback") {
            if (!msg.ok) {
              // not recorded (the suggestion changed meanwhile): undo the verdict and say why
              const key = `${msg.project_id}/${msg.suggestion_id}`;
              setVerdicts((prev) => without(prev, key));
              setRefusals((prev) => ({ ...prev, [key]: msg.detail }));
            }
          } else if (msg.type === "error") {
            console.warn("[sensei] the service refused a message:", msg.message);
          } else if (msg.type === "project_closed") {
            setProjects((prev) => without(prev, msg.project_id));
          }
        } catch {}
      };

      // The service may start after the UI, or restart: keep retrying.
      ws.onclose = () => {
        setConnected(false);
        if (!stopped) retry = setTimeout(connect, 1000);
      };
    };

    (window.sensei?.config() ?? Promise.resolve({ token: null, serviceUrl: DEFAULT_SERVICE_URL })).then(({ token, serviceUrl }) => {
      url = `${serviceUrl}/ws/ui`;
      if (token) protocols = [`sensei.token.${token}`];
      if (!stopped) connect();
    });
    return () => {
      stopped = true;
      clearTimeout(retry);
      wsRef.current?.close();
    };
  }, []);

  // Apply asks the service to send the suggestion's action to the host (the outcome arrives as
  // action_result); Dismiss is plain feedback. The verdict shows at once and is undone if the
  // service refuses it.
  const decide = (projectId: string, suggestionId: string, verdict: Verdict) => {
    const ws = wsRef.current;
    // Only show a verdict the service actually received; the buttons are disabled while disconnected.
    if (ws?.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify(verdict === "accepted"
      ? { type: "apply", project_id: projectId, suggestion_id: suggestionId }
      : { type: "feedback", suggestion_id: suggestionId, verdict, project_id: projectId }));
    const key = `${projectId}/${suggestionId}`;
    setVerdicts((prev) => ({ ...prev, [key]: verdict }));
    // what the card said about an earlier attempt that didn't go through no longer applies
    setRefusals((prev) => without(prev, key));
    setOutcomes((prev) => (prev[key] && UNDONE.has(prev[key].status) ? without(prev, key) : prev));
  };

  const projectIds = Object.keys(projects).sort();

  return (
    <div style={{ padding: 18, fontFamily: "system-ui" }}>
      <h1 style={{ margin: 0 }}>SENSEI</h1>
      <p style={{ marginTop: 6, opacity: 0.75 }}>
        Real-time creative copilot — suggestions stream in as your timeline context changes.
      </p>
      <p style={{ fontSize: 12, opacity: 0.6 }}>
        {connected ? "Connected to SENSEI service" : "Connecting to SENSEI service…"}
      </p>
      {connected && projectIds.length === 0 && (
        <p style={{ opacity: 0.6 }}>Waiting for an editing host to send its timeline…</p>
      )}

      {projectIds.map((projectId) => (
        <section key={projectId} style={{ marginTop: 16 }}>
          <h2 style={{ fontSize: 14, margin: "0 0 8px" }}>
            Project {projectId} <span style={{ fontWeight: 400, opacity: 0.6 }}>· {projects[projectId].host}</span>
          </h2>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            {projects[projectId].items.map((s) => (
              <SuggestionCard
                key={s.id}
                s={s}
                status={verdicts[`${projectId}/${s.id}`] ?? "idle"}
                outcome={outcomes[`${projectId}/${s.id}`]}
                refused={refusals[`${projectId}/${s.id}`]}
                disabled={!connected}
                onFeedback={(verdict) => decide(projectId, s.id, verdict)}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
