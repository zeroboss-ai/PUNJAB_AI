/** Thin fetch wrapper. Every request carries X-Terminal (the server rejects state-changing calls without it). */
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

let onUnauth: () => void = () => {};
export function setUnauthHandler(fn: () => void) {
  onUnauth = fn;
}

interface Opts {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
}

export async function api<T = any>(path: string, opts: Opts = {}): Promise<T> {
  const { method = "GET", body, signal } = opts;
  let res: Response;
  try {
    res = await fetch("/api" + path, {
      method,
      signal,
      credentials: "same-origin",
      headers: { "X-Terminal": "1", ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError("Cannot reach the terminal server. Is it still running?", 0);
  }
  if (res.status === 401 && !path.startsWith("/auth/login")) onUnauth();
  let data: any = null;
  try {
    data = await res.json();
  } catch {
    /* non-JSON body */
  }
  if (!res.ok) {
    const d = data?.error ?? data?.detail;
    const msg = typeof d === "string" ? d : Array.isArray(d) ? "Some field is invalid - check the form" : `Request failed (${res.status})`;
    throw new ApiError(msg, res.status);
  }
  if (data && data.ok === false && typeof data.error === "string") throw new ApiError(data.error, res.status);
  return data as T;
}
