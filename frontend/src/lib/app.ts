/** App-wide state: login, session info (mode, market, feed), theme, toasts, order ticket, tiny router. */
import { useEffect, useSyncExternalStore } from "react";
import { api, setUnauthHandler } from "./api";
import { startQuotes, stopQuotes } from "./quotes";
import { createStore, useStore } from "./store";
import type { OrderRow, SessionInfo } from "./types";

// ----------------------------------------------------------------------------------- auth + session
export const auth = createStore<{ state: "loading" | "out" | "in"; mode: string | null }>({ state: "loading", mode: null });
export const session = createStore<SessionInfo | null>(null);
let pollTimer: number | undefined;

async function refreshSession() {
  try {
    session.set(await api<SessionInfo>("/session"));
  } catch {
    /* keep the last known state; 401s are handled globally */
  }
}

function loop() {
  if (pollTimer) window.clearTimeout(pollTimer);
  const go = async () => {
    if (auth.get().state !== "in") return;
    if (!document.hidden) await refreshSession();
    pollTimer = window.setTimeout(go, 2500);
  };
  go();
}

export async function boot() {
  setUnauthHandler(() => signedOut());
  try {
    const s = await api<{ logged_in: boolean; mode: string }>("/auth/status");
    auth.set({ state: s.logged_in ? "in" : "out", mode: s.mode });
    if (s.logged_in) enter();
  } catch {
    auth.set({ state: "out", mode: null });
  }
}

function enter() {
  startQuotes();
  loop();
}

function signedOut() {
  if (auth.get().state === "out") return;
  stopQuotes();
  if (pollTimer) window.clearTimeout(pollTimer);
  session.set(null);
  auth.set((a) => ({ ...a, state: "out" }));
}

export async function login(password: string) {
  await api("/auth/login", { method: "POST", body: { password } });
  auth.set((a) => ({ ...a, state: "in" }));
  await refreshSession();
  enter();
}

export async function logout() {
  try {
    await api("/auth/logout", { method: "POST" });
  } finally {
    signedOut();
  }
}

export async function connectBroker(totp: string, accountId = "", confirmLive = false) {
  await api("/connect", { method: "POST", body: { totp, account_id: accountId, confirm_live: confirmLive } });
  await refreshSession();
}

export async function disconnectBroker() {
  await api("/disconnect", { method: "POST", body: {} });
  await refreshSession();
}

export const useSession = () => useStore(session);
export const useAuth = () => useStore(auth);
export { refreshSession };

// ---------------------------------------------------------------------------------------- theme
type Theme = "dark" | "light";
function initialTheme(): Theme {
  try {
    const t = localStorage.getItem("nt-theme");
    if (t === "dark" || t === "light") return t;
  } catch {
    /* storage can be unavailable */
  }
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}
export const theme = createStore<Theme>(initialTheme());
export function applyTheme(t: Theme) {
  document.documentElement.dataset.theme = t;
  theme.set(t);
  try {
    localStorage.setItem("nt-theme", t);
  } catch {
    /* ignore */
  }
}
export const useTheme = () => useStore(theme);

// --------------------------------------------------------------------------------------- toasts
export interface Toast {
  id: number;
  kind: "ok" | "err" | "info";
  text: string;
}
export const toasts = createStore<Toast[]>([]);
let toastId = 1;
export function toast(text: string, kind: Toast["kind"] = "ok", ms = 4500) {
  const id = toastId++;
  toasts.set((l) => [...l.slice(-3), { id, kind, text }]);
  window.setTimeout(() => toasts.set((l) => l.filter((t) => t.id !== id)), kind === "err" ? Math.max(ms, 7000) : ms);
}
export const useToasts = () => useStore(toasts);

// --------------------------------------------------------------------------------- order ticket
export interface TicketReq {
  key: string;
  label: string;
  side: "B" | "S";
  lot: number;
  tick: number;
  product?: string;
  modify?: OrderRow;
}
export const ticket = createStore<TicketReq | null>(null);
export const openTicket = (r: TicketReq) => ticket.set(r);
export const closeTicket = () => ticket.set(null);
export const useTicket = () => useStore(ticket);

// ---------------------------------------------------------------------------------------- router
function subscribePath(cb: () => void) {
  window.addEventListener("popstate", cb);
  return () => window.removeEventListener("popstate", cb);
}
export function usePath(): string {
  return useSyncExternalStore(subscribePath, () => location.pathname);
}
export function navigate(to: string) {
  if (location.pathname === to) return;
  history.pushState(null, "", to);
  window.dispatchEvent(new PopStateEvent("popstate"));
  window.scrollTo(0, 0);
}

export function useTitle(t: string) {
  useEffect(() => {
    document.title = `${t} · PUNJAB AI`;
  }, [t]);
}
