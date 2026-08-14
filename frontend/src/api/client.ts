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
  return await r.json();
}
