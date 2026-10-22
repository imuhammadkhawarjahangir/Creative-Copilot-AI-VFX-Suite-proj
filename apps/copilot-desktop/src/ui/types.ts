export type Verdict = "accepted" | "rejected";

// A host's answer to an applied suggestion (each host of the project answers; ack_apply gives the overall outcome).
export type ActionOutcome = {
  status: "applied" | "marked" | "duplicate" | "rejected";
  detail?: string | null;
};

export type SenseiConfig = { token: string | null; serviceUrl: string };

declare global {
  interface Window {
    // exposed by the Electron preload; absent when the UI runs in a plain browser
    sensei?: { config(): Promise<SenseiConfig> };
  }
}

export type Suggestion = {
  id: string;
  kind: string;
  title: string;
  confidence: number;
  rationale: string;
  action: { op: string; params: Record<string, unknown> };
};
