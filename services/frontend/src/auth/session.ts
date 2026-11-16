// Session token storage: in-memory first, mirrored to sessionStorage so a page
// reload in the same tab keeps the session. Never localStorage (survives tab close).
import type { Role } from "../api/types";

export interface Session {
  token: string;
  role: Role;
  username: string;
  expiresAt: number; // epoch ms (client clock)
}

const KEY = "orchid.session";
let current: Session | null = null;

function isRole(v: unknown): v is Role {
  return v === "viewer" || v === "operator";
}

function parseStored(raw: string | null): Session | null {
  if (!raw) return null;
  try {
    const obj: unknown = JSON.parse(raw);
    if (typeof obj !== "object" || obj === null) return null;
    const o = obj as Record<string, unknown>;
    if (typeof o.token !== "string" || o.token.length === 0) return null;
    if (!isRole(o.role) || typeof o.username !== "string" || typeof o.expiresAt !== "number") return null;
    return { token: o.token, role: o.role, username: o.username, expiresAt: o.expiresAt };
  } catch {
    return null;
  }
}

function safeStorage(): Storage | null {
  try {
    return typeof window !== "undefined" ? window.sessionStorage : null;
  } catch {
    return null;
  }
}

export function getSession(nowMs: number = Date.now()): Session | null {
  if (!current) current = parseStored(safeStorage()?.getItem(KEY) ?? null);
  if (current && current.expiresAt <= nowMs) {
    clearSession();
    return null;
  }
  return current;
}

export function setSession(session: Session): void {
  current = session;
  try {
    safeStorage()?.setItem(KEY, JSON.stringify(session));
  } catch {
    // storage full / disabled: in-memory session still works for this page
  }
}

export function clearSession(): void {
  current = null;
  try {
    safeStorage()?.removeItem(KEY);
  } catch {
    // ignore
  }
}
