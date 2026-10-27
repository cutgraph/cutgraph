// Revisions this client is itself about to author. The server publishes its SSE
// broadcast BEFORE the PUT/PATCH response returns, so our own accepted write can
// echo back over the event stream and look like a remote edit. Every gated write
// marks its expected revision (baseRevision + 1) here for the duration of the
// request; the SSE handler skips any event whose revision is still self-pending.
const pending = new Map<number, number>();

export function markSelfPending(revision: number): void {
  pending.set(revision, (pending.get(revision) ?? 0) + 1);
}

export function clearSelfPending(revision: number): void {
  const count = pending.get(revision) ?? 0;
  if (count <= 1) pending.delete(revision);
  else pending.set(revision, count - 1);
}

export function isSelfPending(revision: number): boolean {
  return (pending.get(revision) ?? 0) > 0;
}
