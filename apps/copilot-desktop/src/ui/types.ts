export type Verdict = "accepted" | "rejected";

export type Suggestion = {
  id: string;
  kind: string;
  title: string;
  confidence: number;
  rationale: string;
  action: { op: string; params: Record<string, unknown> };
};
