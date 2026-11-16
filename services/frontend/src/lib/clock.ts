// Tracks the offset between the server clock and the browser clock so that
// "last updated N s ago" is computed against server time, not a skewed laptop.
let offsetMs = 0;

export function noteServerTime(serverMs: number, clientMs: number = Date.now()): void {
  if (Number.isFinite(serverMs)) offsetMs = serverMs - clientMs;
}

export function serverNow(clientMs: number = Date.now()): number {
  return clientMs + offsetMs;
}
