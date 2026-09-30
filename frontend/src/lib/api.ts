const TOKEN_KEY = "dc-token";

export const tokenStore = {
  get: () => { try { return localStorage.getItem(TOKEN_KEY); } catch { return null; } },
  set: (t: string) => { try { localStorage.setItem(TOKEN_KEY, t); } catch { /* storage unavailable */ } },
  clear: () => { try { localStorage.removeItem(TOKEN_KEY); } catch { /* storage unavailable */ } },
};

export class ApiError extends Error {
  constructor(message: string, public status: number, public code?: string) { super(message); }
}

export const GENERIC_ERROR = "Something went wrong. Please try again.";

interface Options { method?: string; json?: unknown; form?: FormData; signal?: AbortSignal }

async function raw(path: string, opts: Options = {}): Promise<Response> {
  const headers: Record<string, string> = {};
  const token = tokenStore.get();
  if (token) headers.Authorization = `Bearer ${token}`;
  let body: BodyInit | undefined;
  if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(opts.json); }
  if (opts.form) body = opts.form;
  let res: Response;
  try {
    res = await fetch(path, { method: opts.method ?? "GET", headers, body, signal: opts.signal });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError("Cannot reach the server. Check your connection and try again.", 0, "network");
  }
  if (!res.ok) {
    let msg = GENERIC_ERROR, code: string | undefined;
    try { const b = await res.json(); msg = b?.error?.message ?? GENERIC_ERROR; code = b?.error?.code; } catch { /* non-JSON */ }
    if (res.status === 401 && tokenStore.get() && !path.startsWith("/api/auth/login")) {
      tokenStore.clear();
      window.dispatchEvent(new Event("dc-unauthorized"));
    }
    throw new ApiError(msg, res.status, code);
  }
  return res;
}

export async function api<T = unknown>(path: string, opts?: Options): Promise<T> {
  const res = await raw(path, opts);
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

/** Files require auth, so images are fetched with the bearer token and shown via blob URLs. */
export async function fetchBlobUrl(path: string): Promise<string> {
  const res = await raw(path);
  return URL.createObjectURL(await res.blob());
}

export const errorMessage = (e: unknown) => (e instanceof ApiError ? e.message : GENERIC_ERROR);

export async function downloadFile(path: string, filename: string): Promise<void> {
  const url = await fetchBlobUrl(path);
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

/** Signed, short-lived URL for native <video>/<audio> playback and large downloads (issued only after an ownership check). */
export async function streamUrl(kind: "asset" | "reference", id: string): Promise<string> {
  return (await api<{ url: string }>("/api/media/stream-url", { method: "POST", json: { kind, id } })).url;
}

/** Downloads a stored file by streaming through the signed URL, so large videos are never held in browser memory. */
export async function downloadAsset(id: string): Promise<void> {
  const url = await streamUrl("asset", id);
  const a = document.createElement("a");
  a.href = `${url}?download=1`; a.click();
}
