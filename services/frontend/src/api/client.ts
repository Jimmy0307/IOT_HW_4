// Same-origin API client. All requests go to /api/... behind the reverse proxy.
import { clearSession, getSession, setSession } from "../auth/session";
import { noteServerTime } from "../lib/clock";
import type {
  CommandAccepted,
  EventsResponse,
  GroupName,
  LoginResponse,
  Mode,
  OverviewResponse,
  SiteDetail,
  SystemResponse,
} from "./types";

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
  get unavailable(): boolean {
    return this.status === 503;
  }
}

type UnauthorizedHandler = () => void;
let onUnauthorized: UnauthorizedHandler = () => undefined;

export function setUnauthorizedHandler(handler: UnauthorizedHandler): void {
  onUnauthorized = handler;
}

const SITE_ID = /^[A-Za-z0-9_-]{1,32}$/;

function sitePath(siteId: string): string {
  if (!SITE_ID.test(siteId)) throw new ApiError(400, "invalid site id");
  return encodeURIComponent(siteId);
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const body: unknown = await res.json();
    if (body && typeof body === "object") {
      const detail = (body as { detail?: unknown }).detail;
      if (typeof detail === "string") return detail.slice(0, 200);
      if (Array.isArray(detail)) return "request rejected by server validation";
    }
  } catch {
    // non-JSON error body
  }
  return `HTTP ${res.status}`;
}

async function request<T>(path: string, init: RequestInit = {}, auth = true): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body) headers.set("Content-Type", "application/json");
  if (auth) {
    const session = getSession();
    if (!session) {
      onUnauthorized();
      throw new ApiError(401, "not logged in");
    }
    headers.set("Authorization", `Bearer ${session.token}`);
  }
  let res: Response;
  try {
    res = await fetch(path, { ...init, headers, credentials: "same-origin", cache: "no-store" });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, "network error: API unreachable");
  }
  const date = res.headers.get("date");
  if (date) noteServerTime(Date.parse(date));
  if (res.status === 401 && auth) {
    clearSession();
    onUnauthorized();
    throw new ApiError(401, "session expired");
  }
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  return (await res.json()) as T;
}

export async function login(username: string, password: string): Promise<LoginResponse> {
  const out = await request<LoginResponse>(
    "/api/auth/login",
    { method: "POST", body: JSON.stringify({ username, password }) },
    false,
  );
  if (typeof out.access_token !== "string" || (out.role !== "viewer" && out.role !== "operator")) {
    throw new ApiError(500, "unexpected login response");
  }
  setSession({
    token: out.access_token,
    role: out.role,
    username: String(out.username),
    expiresAt: Date.now() + Math.max(0, Number(out.expires_in) || 0) * 1000,
  });
  return out;
}

export const api = {
  overview: (signal?: AbortSignal) => request<OverviewResponse>("/api/overview", { signal }),
  site: (siteId: string, signal?: AbortSignal) => request<SiteDetail>(`/api/sites/${sitePath(siteId)}`, { signal }),
  system: (signal?: AbortSignal) => request<SystemResponse>("/api/system", { signal }),
  events: (limit: number, signal?: AbortSignal) =>
    request<EventsResponse>(`/api/events?limit=${Math.max(1, Math.min(200, Math.trunc(limit)))}`, { signal }),
  sendGroupCommand: (siteId: string, group: GroupName, desiredState: "ON" | "OFF", reason: string) =>
    request<CommandAccepted>(`/api/sites/${sitePath(siteId)}/commands`, {
      method: "POST",
      body: JSON.stringify({ group, desired_state: desiredState, reason }),
    }),
  setMode: (siteId: string, mode: Mode, reason: string, ttlSeconds?: number) =>
    request<CommandAccepted>(`/api/sites/${sitePath(siteId)}/mode`, {
      method: "POST",
      body: JSON.stringify(ttlSeconds ? { mode, reason, ttl_seconds: ttlSeconds } : { mode, reason }),
    }),
};
