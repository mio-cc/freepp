export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function api<T = any>(
  path: string,
  method: string = "GET",
  body?: any
): Promise<T> {
  const opts: RequestInit = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(path, opts);
  const text = await r.text();
  if (!r.ok) {
    // 优先透出后端 JSON 错误信息, 否则给可读的状态码错误
    let detail = "";
    try {
      const j = JSON.parse(text);
      detail = String(j.error || j.detail || j.message || "").slice(0, 200);
    } catch {
      /* 非 JSON 响应 */
    }
    throw new ApiError(
      r.status,
      detail || `HTTP ${r.status} ${r.statusText || ""}`.trim()
    );
  }
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError(r.status, `响应不是有效 JSON: ${text.slice(0, 120)}`);
  }
}
