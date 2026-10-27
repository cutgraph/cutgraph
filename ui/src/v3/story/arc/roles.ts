import type { Scene } from "../../../types/project";

// Scene-role accents use the shared shell colour tokens.
const ROLE_COLOR: Record<Scene["role"], string> = {
  Hook: "var(--yellow)",
  Problem: "var(--coral)",
  Approach: "var(--cyan)",
  Build: "var(--blue)",
  Example: "var(--blue)",
  Check: "var(--violet)",
  Payoff: "var(--green)",
  Wrap: "var(--green)",
  Beat: "#9aa7b1",
};

export function roleColor(role: Scene["role"]): string {
  return ROLE_COLOR[role] ?? ROLE_COLOR.Beat;
}
