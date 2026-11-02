import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { api } from "./api";

export interface Store<T> {
  get: () => T;
  set: (v: T | ((p: T) => T)) => void;
  subscribe: (fn: () => void) => () => void;
}

export function createStore<T>(initial: T): Store<T> {
  let value = initial;
  const listeners = new Set<() => void>();
  return {
    get: () => value,
    set: (v) => {
      const next = typeof v === "function" ? (v as (p: T) => T)(value) : v;
      if (Object.is(next, value)) return;
      value = next;
      listeners.forEach((f) => f());
    },
    subscribe: (fn) => {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  };
}

export function useStore<T>(s: Store<T>): T {
  return useSyncExternalStore(s.subscribe, s.get);
}

/** Polls an API path while the tab is visible. Keeps the last good data when a refresh fails. */
export function usePoll<T>(path: string | null, ms: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const tick = useRef(0);
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (!path) return;
    let alive = true;
    let timer: number | undefined;
    const ctl = new AbortController();
    const run = async () => {
      const mine = ++tick.current;
      if (!document.hidden) {
        try {
          const d = await api<T>(path, { signal: ctl.signal });
          if (alive && mine === tick.current) {
            setData(d);
            setError(null);
          }
        } catch (e) {
          if (alive && (e as Error).name !== "AbortError") setError((e as Error).message);
        } finally {
          if (alive) setLoading(false);
        }
      }
      if (alive) timer = window.setTimeout(run, ms);
    };
    run();
    return () => {
      alive = false;
      ctl.abort();
      if (timer) window.clearTimeout(timer);
    };
  }, [path, ms, nonce]);

  return { data, error, loading, reload };
}
