import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";

export interface PollState<T> {
  data: T | null;
  /** Last error from the most recent poll (null when the last poll succeeded). */
  error: ApiError | null;
  /** True when the last poll failed (503 / network) but older data is still shown. */
  stale: boolean;
  loading: boolean;
  fetchedAt: number | null;
  refresh: () => void;
}

function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err;
  return new ApiError(0, err instanceof Error ? err.message : "unexpected error");
}

/**
 * Poll `fetcher` every `intervalMs` (next poll is scheduled only after the
 * previous one settles, so requests never pile up). On failure the last good
 * data is kept and flagged stale.
 */
export function usePolling<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  intervalMs: number,
  key: string,
): PollState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchedAt, setFetchedAt] = useState<number | null>(null);
  const [tick, setTick] = useState(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    setData(null);
    setError(null);
    setLoading(true);
    setFetchedAt(null);
  }, [key]);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let active = true;

    const run = async (): Promise<void> => {
      try {
        const out = await fetcherRef.current(controller.signal);
        if (!active) return;
        setData(out);
        setError(null);
        setFetchedAt(Date.now());
      } catch (err) {
        if (!active || (err instanceof DOMException && err.name === "AbortError")) return;
        const apiErr = toApiError(err);
        setError(apiErr);
        if (apiErr.status === 401) return; // session handler redirects to login
      } finally {
        if (active) setLoading(false);
      }
      if (active) timer = setTimeout(() => void run(), intervalMs);
    };

    void run();
    return () => {
      active = false;
      controller.abort();
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [intervalMs, key, tick]);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  return { data, error, stale: error !== null && data !== null, loading, fetchedAt, refresh };
}
