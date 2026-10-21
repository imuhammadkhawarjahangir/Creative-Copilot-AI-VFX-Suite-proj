export type Verdict = "accepted" | "rejected";

// What became of an applied suggestion: the host's answer, or "failed" if the service couldn't reach one.
export type ActionOutcome = {
  status: "applied" | "marked" | "duplicate" | "rejected" | "failed";
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
