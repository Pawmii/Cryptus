import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";

export interface Resource<T> {
  data: T | undefined;
  error: ApiError | null;
  loading: boolean;
  reload: () => Promise<void>;
  setData: (v: T) => void;
}

/** Fetch a JSON resource; optionally poll. Keeps previous data while refetching. */
export function useApi<T>(path: string | null, pollMs?: number): Resource<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(!!path);
  const alive = useRef(true);

  const reload = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    try {
      const d = await api.get<T>(path);
      if (alive.current) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      if (alive.current) setError(e as ApiError);
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    alive.current = true;
    reload();
    if (!pollMs) return () => void (alive.current = false);
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") reload();
    }, pollMs);
    return () => {
      alive.current = false;
      window.clearInterval(id);
    };
  }, [reload, pollMs]);

  return { data, error, loading, reload, setData };
}

/** Wrap an async action with pending + error state. */
export function useAction<A extends unknown[], R>(fn: (...a: A) => Promise<R>) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(
    async (...a: A): Promise<R | undefined> => {
      setPending(true);
      setError(null);
      try {
        return await fn(...a);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        return undefined;
      } finally {
        setPending(false);
      }
    },
    [fn],
  );
  return { run, pending, error, setError };
}

export function useNow(intervalMs = 1000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}
