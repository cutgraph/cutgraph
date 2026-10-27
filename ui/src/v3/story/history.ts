// Undo/redo persist as forward revisions through the serialised save queue, so a history
// step waits for an in-flight edit to finish.

import { persistCurrent } from "../../persist";
import { applyLocal, redo, undo, useProjectStore } from "../../store/useProjectStore";
import type { Project } from "../../types/project";
import { toast } from "../Toast";

function saveCurrent(): void {
  const name = useProjectStore.getState().project?.name;
  if (name) void persistCurrent(name);
}

export function undoStep(): void {
  undo();
  saveCurrent();
}

export function redoStep(): void {
  redo();
  saveCurrent();
}

// Every Story edit says what it did, with an Undo that is exactly one Ctrl+Z.
export function editToast(message: string): void {
  toast(message, { label: "Undo", run: undoStep });
}

// One local edit = one undo step, saved and announced.
export function commitEdit(next: Project, label: string): void {
  applyLocal(() => ({ project: next }), { origin: "human", label });
  void persistCurrent(next.name);
  editToast(label);
}
