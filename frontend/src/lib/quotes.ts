/**
 * Live prices. One WebSocket to the terminal server; components say which instruments they need (useWant)
 * and read prices with useQuote. The server keeps a single broker feed shared by every page and the algo.
 */
import { useCallback, useEffect, useSyncExternalStore } from "react";
import { api } from "./api";
import { createStore } from "./store";
import type { Quote } from "./types";

const quotes = new Map<string, Quote>();
const flash = new Map<string, number>();            // key -> direction of the last change (+1 / -1)
const subs = new Map<string, Set<() => void>>();
const wanted = new Map<string, number>();            // key -> number of components that need it
const history = new Map<string, number[]>();         // small in-session price trail for sparklines
const lastSample = new Map<string, number>();

export const wsState = createStore<{ open: boolean }>({ open: false });

let ws: WebSocket | null = null;
let running = false;
let retry = 0;
let retryTimer: number | undefined;

function emit(key: string) {
  subs.get(key)?.forEach((f) => f());
}

function put(list: Quote[]) {
  const now = Date.now();
  for (const q of list) {
    const prev = quotes.get(q.k);
    if (prev && prev.l !== q.l) flash.set(q.k, q.l > prev.l ? 1 : -1);
    quotes.set(q.k, q);
    if (now - (lastSample.get(q.k) ?? 0) > 2000) {
      lastSample.set(q.k, now);
      const h = history.get(q.k) ?? [];
      history.set(q.k, [...h.slice(-89), q.l]);
    }
    emit(q.k);
  }
}

function send(msg: object) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
}

async function seed(keys: string[]) {
  const missing = keys.filter((k) => !quotes.has(k));
  if (!missing.length) return;
  try {
    const r = await api<{ quotes: Quote[] }>("/quotes?keys=" + encodeURIComponent(missing.join(",")));
    put(r.quotes);
  } catch {
    /* the stream will fill it in */
  }
}

function open() {
  if (!running) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => {
    retry = 0;
    wsState.set({ open: true });
    const keys = [...wanted.keys()];
    if (keys.length) send({ op: "sub", keys });
  };
  ws.onmessage = (e) => {
    try {
      const m = JSON.parse(e.data);
      if (m.t === "q") put(m.d as Quote[]);
    } catch {
      /* ignore malformed frames */
    }
  };
  ws.onclose = () => {
    wsState.set({ open: false });
    ws = null;
    if (running) {
      retryTimer = window.setTimeout(open, Math.min(1000 * 2 ** retry++, 15000));
    }
  };
  ws.onerror = () => ws?.close();
}

export function startQuotes() {
  if (running) return;
  running = true;
  open();
}

export function stopQuotes() {
  running = false;
  if (retryTimer) window.clearTimeout(retryTimer);
  ws?.close();
  ws = null;
  quotes.clear();
  history.clear();
  wsState.set({ open: false });
}

export function want(keys: string[]): () => void {
  const fresh: string[] = [];
  for (const k of keys) {
    const n = wanted.get(k) ?? 0;
    if (n === 0) fresh.push(k);
    wanted.set(k, n + 1);
  }
  if (fresh.length) {
    send({ op: "sub", keys: fresh });
    seed(fresh);
  }
  return () => {
    const gone: string[] = [];
    for (const k of keys) {
      const n = (wanted.get(k) ?? 1) - 1;
      if (n <= 0) {
        wanted.delete(k);
        gone.push(k);
      } else wanted.set(k, n);
    }
    if (gone.length) send({ op: "unsub", keys: gone });
  };
}

/** Subscribe the current component to these instruments for as long as it is mounted. */
export function useWant(keys: string[]) {
  const sig = keys.join(",");
  useEffect(() => (keys.length ? want(keys) : undefined), [sig]); // eslint-disable-line react-hooks/exhaustive-deps
}

export function useQuote(key: string | undefined): Quote | undefined {
  const subscribe = useCallback(
    (cb: () => void) => {
      if (!key) return () => {};
      let s = subs.get(key);
      if (!s) subs.set(key, (s = new Set()));
      s.add(cb);
      return () => {
        s!.delete(cb);
      };
    },
    [key],
  );
  return useSyncExternalStore(subscribe, () => (key ? quotes.get(key) : undefined));
}

/** Direction of the most recent price change (+1 / -1), for a brief colour flash on a cell. */
export function lastMove(key: string): number {
  return flash.get(key) ?? 0;
}

export function useHistory(key: string | undefined): number[] {
  const subscribe = useCallback(
    (cb: () => void) => {
      if (!key) return () => {};
      let s = subs.get(key);
      if (!s) subs.set(key, (s = new Set()));
      s.add(cb);
      return () => {
        s!.delete(cb);
      };
    },
    [key],
  );
  return useSyncExternalStore(subscribe, () => (key ? history.get(key) : undefined)) ?? EMPTY;
}
const EMPTY: number[] = [];
