import React, { useEffect, useRef, useState } from "react";
import SuggestionCard from "./components/SuggestionCard";
import type { ActionOutcome, Suggestion, Verdict } from "./types";

type ProjectStream = { host: string; items: Suggestion[] };

// used when the page runs outside Electron (no preload); Electron passes SENSEI_SERVICE_URL instead
const DEFAULT_SERVICE_URL = "ws://127.0.0.1:8000";

const MAX_OUTCOMES = 4; // host answers kept per card, so other UIs' repeated Applies can't grow it forever

const LOST_ANSWER = "The connection dropped before the service answered, so this may or may not have gone through";

function without<T>(record: Record<string, T>, key: string): Record<string, T> {
  const next = { ...record };
  delete next[key];
  return next;
}

export default function App() {
  const [projects, setProjects] = useState<Record<string, ProjectStream>>({});
  // keyed by "<project>/<suggestion id>" so a verdict survives the card being remounted
  const [verdicts, setVerdicts] = useState<Record<string, Verdict>>({});
  // the hosts' answers since this UI last applied it (one per host when a project has several), by the same key
  const [outcomes, setOutcomes] = useState<Record<string, ActionOutcome[]>>({});
  // why the latest decision didn't go through, by the same key
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  // decisions sent on the current connection and not answered yet (ack_apply / ack_feedback), by key
  const unansweredRef = useRef(new Set<string>());

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
            // one host's answer, shown as it comes; whether the Apply went through is ack_apply's to say
            const key = `${msg.project_id}/${msg.suggestion_id}`;
            const outcome: ActionOutcome = { status: msg.status, detail: msg.detail };
            setOutcomes((prev) => ({ ...prev, [key]: [...(prev[key] ?? []), outcome].slice(-MAX_OUTCOMES) }));
          } else if (msg.type === "ack_apply" || msg.type === "ack_feedback") {
            // The service's answer to this UI's decision. If it didn't go through (no host carried the Apply
            // out, or the Dismiss wasn't recorded), undo the verdict and say why.
            const key = `${msg.project_id}/${msg.suggestion_id}`;
            unansweredRef.current.delete(key);
            if (!msg.ok) {
              const what = msg.type === "ack_apply" ? "Couldn't apply" : "Dismiss wasn't recorded";
              setVerdicts((prev) => without(prev, key));
              setNotes((prev) => ({ ...prev, [key]: `${what}: ${msg.detail}` }));
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
        // Answers are sent to the connection that made the decision, so those still owed are lost with it:
        // the outcome is unknown. Undo those verdicts. Deciding again is safe: an action a host already
        // carried out comes back as a duplicate, and a later vote replaces the earlier one.
        const lost = [...unansweredRef.current];
        unansweredRef.current.clear();
        if (lost.length > 0) {
          setVerdicts((prev) => lost.reduce((rest, key) => without(rest, key), prev));
          setNotes((prev) => ({ ...prev, ...Object.fromEntries(lost.map((key) => [key, LOST_ANSWER])) }));
        }
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
  // action_result); Dismiss is plain feedback. Each is answered once (ack_apply, ack_feedback): the
  // verdict shows at once, the buttons stay disabled until then, and it is undone if it didn't go through.
  const decide = (projectId: string, suggestionId: string, verdict: Verdict) => {
    const ws = wsRef.current;
    // Only show a verdict the service actually received; the buttons are disabled while disconnected.
    if (ws?.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify(verdict === "accepted"
      ? { type: "apply", project_id: projectId, suggestion_id: suggestionId }
      : { type: "feedback", suggestion_id: suggestionId, verdict, project_id: projectId }));
    const key = `${projectId}/${suggestionId}`;
    unansweredRef.current.add(key);
    setVerdicts((prev) => ({ ...prev, [key]: verdict }));
    // what the card said about an earlier attempt no longer applies
    setNotes((prev) => without(prev, key));
    if (verdict === "accepted") setOutcomes((prev) => without(prev, key));
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
                outcomes={outcomes[`${projectId}/${s.id}`] ?? []}
                note={notes[`${projectId}/${s.id}`]}
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
