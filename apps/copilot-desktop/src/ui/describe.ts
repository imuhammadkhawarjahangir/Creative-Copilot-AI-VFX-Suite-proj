import type { ActionOutcome, Suggestion } from "./types";

// What the UI says about suggestions, in an editor's words rather than the protocol's.

export const KIND_LABEL: Record<string, string> = {
  color_grade: "Color grade",
  transition: "Transition",
  keyframe: "Keyframe",
  vfx: "Effect"
};

export const OUTCOME_TEXT: Record<ActionOutcome["status"], string> = {
  applied: "Applied in the host",
  marked: "Marked in the host for you to finish",
  duplicate: "Already applied in the host",
  rejected: "The host refused it"
};

const seconds = (value: unknown) => `${Number(value).toFixed(2)} s`;
const amount = (value: unknown) => Number(value).toFixed(2);

// What applying it will change, from the action's parameters, when the title doesn't already say it;
// "" otherwise, and for an action this UI doesn't know.
export function summarize(s: Suggestion): string {
  const p = s.action.params;
  switch (s.action.op) {
    case "apply_color_grade":
      return `${p.lut}, contrast ${amount(p.contrast)}, saturation ${amount(p.sat)}`;
    case "apply_transition_at_cut":
      return `${p.type === "dip_to_black" ? "Dip to black" : "Cross dissolve"}, ${p.frames} frames, at the cut at ${seconds(p.at)}`;
    case "apply_vfx_preset":
      return `${p.preset}: threshold ${amount(p.threshold)}, intensity ${amount(p.intensity)}, radius ${amount(p.radius)}`;
    default:
      return "";
  }
}

// hosts name themselves in their hello; the ones that ship with SENSEI get their product names
const HOST_NAMES: Record<string, string> = {
  "davinci-resolve": "DaVinci Resolve",
  "mock-host": "Mock host"
};

export function hostName(host: string): string {
  return HOST_NAMES[host] ?? host;
}
