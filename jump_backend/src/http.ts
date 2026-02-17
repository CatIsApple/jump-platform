type JsonValue = null | boolean | number | string | JsonValue[] | { [k: string]: JsonValue };

export function json(data: JsonValue, init?: ResponseInit): Response {
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json; charset=utf-8");
  return new Response(JSON.stringify(data, null, 2), { ...init, headers });
}

export function text(body: string, init?: ResponseInit): Response {
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type")) headers.set("Content-Type", "text/plain; charset=utf-8");
  return new Response(body, { ...init, headers });
}

export function badRequest(message: string, extra?: Record<string, JsonValue>): Response {
  return json({ ok: false, error: "bad_request", message, ...(extra || {}) }, { status: 400 });
}

export function unauthorized(message = "인증이 필요합니다."): Response {
  return json({ ok: false, error: "unauthorized", message }, { status: 401 });
}

export function forbidden(message = "권한이 없습니다."): Response {
  return json({ ok: false, error: "forbidden", message }, { status: 403 });
}

export function notFound(): Response {
  return json({ ok: false, error: "not_found", message: "요청한 API가 없습니다." }, { status: 404 });
}

export function methodNotAllowed(): Response {
  return json({ ok: false, error: "method_not_allowed", message: "허용되지 않는 메서드입니다." }, { status: 405 });
}

export async function parseJson<T extends Record<string, unknown>>(req: Request): Promise<T | null> {
  const ct = req.headers.get("Content-Type") || "";
  if (!ct.toLowerCase().includes("application/json")) return null;
  try {
    return (await req.json()) as T;
  } catch {
    return null;
  }
}

