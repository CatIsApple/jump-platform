import { nowUnixSeconds, randomToken, sha256Hex } from "./crypto";
import { badRequest, forbidden, json, methodNotAllowed, notFound, parseJson, unauthorized } from "./http";

export interface Env {
  DB: D1Database;
  ENV?: string;
  LICENSE_PEPPER: string;
  ADMIN_TOKEN?: string;
}

type LicenseStatus = "active" | "suspended" | "revoked";

function isValidStatus(v: unknown): v is LicenseStatus {
  return v === "active" || v === "suspended" || v === "revoked";
}

function normalizeDomain(v: string): string {
  let s = (v || "").trim();
  s = s.replace(/^https?:\/\//i, "");
  s = s.replace(/\/+$/g, "");
  return s;
}

function tokenPrefix(token: string): string {
  return (token || "").slice(0, 8);
}

function parseBearer(req: Request): string | null {
  const h = req.headers.get("Authorization") || "";
  const m = /^Bearer\s+(.+)$/i.exec(h.trim());
  return m?.[1]?.trim() || null;
}

async function requireAdmin(req: Request, env: Env): Promise<void> {
  // 1) Optional extra guard: X-Admin-Token
  const adminToken = (env.ADMIN_TOKEN || "").trim();
  if (adminToken) {
    const provided = (req.headers.get("X-Admin-Token") || "").trim();
    if (provided && provided === adminToken) return;
  }

  // 2) Cloudflare Access assertion header (when Access is configured on this route)
  const accessJwt =
    req.headers.get("Cf-Access-Jwt-Assertion") ||
    req.headers.get("CF-Access-Jwt-Assertion") ||
    req.headers.get("cf-access-jwt-assertion");
  if (accessJwt && accessJwt.trim()) return;

  throw new Error("admin_unauthorized");
}

async function requireSession(req: Request, env: Env): Promise<{
  session_id: number;
  license_id: number;
  company_name: string;
  expires_at: number;
  status: LicenseStatus;
}> {
  const token = parseBearer(req);
  if (!token) throw new Error("unauthorized");

  const pepper = (env.LICENSE_PEPPER || "").trim();
  if (!pepper) throw new Error("server_misconfigured");

  const tokenHash = await sha256Hex(`${pepper}:${token}`);
  const now = nowUnixSeconds();

  const row = await env.DB
    .prepare(
      `
      SELECT
        s.id AS session_id,
        s.license_id AS license_id,
        l.company_name AS company_name,
        l.expires_at AS expires_at,
        l.status AS status
      FROM sessions s
      JOIN licenses l ON l.id = s.license_id
      WHERE s.token_hash = ?
        AND s.revoked_at IS NULL
      LIMIT 1
      `
    )
    .bind(tokenHash)
    .first<{
      session_id: number;
      license_id: number;
      company_name: string;
      expires_at: number;
      status: LicenseStatus;
    }>();

  if (!row) throw new Error("unauthorized");
  if (row.status !== "active") throw new Error("forbidden");
  if (Number(row.expires_at) <= now) throw new Error("expired");

  // best-effort last_seen 갱신
  env.DB.prepare("UPDATE sessions SET last_seen_at = ? WHERE id = ?").bind(now, row.session_id).run().catch(() => {});

  return row;
}

async function handleAuthLogin(req: Request, env: Env): Promise<Response> {
  if (req.method !== "POST") return methodNotAllowed();

  const body = await parseJson<{ license_key?: unknown; device_id?: unknown }>(req);
  if (!body) return badRequest("JSON 형식의 요청 본문이 필요합니다.");

  const licenseKey = String(body.license_key || "").trim();
  const deviceId = String(body.device_id || "").trim();
  if (!licenseKey) return badRequest("license_key가 비어 있습니다.");

  const pepper = (env.LICENSE_PEPPER || "").trim();
  if (!pepper) return json({ ok: false, error: "server_misconfigured", message: "서버 설정 오류: LICENSE_PEPPER 누락" }, { status: 500 });

  const keyHash = await sha256Hex(`${pepper}:${licenseKey}`);
  const now = nowUnixSeconds();

  const lic = await env.DB
    .prepare("SELECT id, company_name, expires_at, status, key_prefix FROM licenses WHERE key_hash = ? LIMIT 1")
    .bind(keyHash)
    .first<{ id: number; company_name: string; expires_at: number; status: LicenseStatus; key_prefix: string }>();

  if (!lic) return unauthorized("라이센스 키를 확인해주세요.");
  if (lic.status !== "active") return forbidden("라이센스 상태가 활성(active)이 아닙니다.");
  if (Number(lic.expires_at) <= now) return forbidden("라이센스가 만료되었습니다.");

  const token = randomToken(32);
  const tokenHash = await sha256Hex(`${pepper}:${token}`);
  const prefix = tokenPrefix(token);

  await env.DB
    .prepare(
      `
      INSERT INTO sessions(token_hash, token_prefix, license_id, created_at, last_seen_at, device_id)
      VALUES(?, ?, ?, ?, ?, ?)
      `
    )
    .bind(tokenHash, prefix, lic.id, now, now, deviceId)
    .run();

  return json({
    ok: true,
    token,
    license: {
      id: lic.id,
      company_name: lic.company_name,
      expires_at: Number(lic.expires_at),
      status: lic.status,
      key_prefix: lic.key_prefix,
    },
    server_time: now,
  });
}

async function handleAuthLogout(req: Request, env: Env): Promise<Response> {
  if (req.method !== "POST") return methodNotAllowed();

  const token = parseBearer(req);
  if (!token) return unauthorized();

  const pepper = (env.LICENSE_PEPPER || "").trim();
  if (!pepper) return json({ ok: false, error: "server_misconfigured", message: "서버 설정 오류: LICENSE_PEPPER 누락" }, { status: 500 });

  const tokenHash = await sha256Hex(`${pepper}:${token}`);
  const now = nowUnixSeconds();

  await env.DB
    .prepare("UPDATE sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL")
    .bind(now, tokenHash)
    .run();

  return json({ ok: true });
}

async function handleAuthHeartbeat(req: Request, env: Env): Promise<Response> {
  if (req.method !== "GET") return methodNotAllowed();

  try {
    const session = await requireSession(req, env);
    return json({
      ok: true,
      status: session.status,
      expires_at: Number(session.expires_at),
      server_time: nowUnixSeconds(),
    });
  } catch (e) {
    const code = String(e instanceof Error ? e.message : e);
    if (code === "unauthorized") return unauthorized("세션이 만료되었거나 폐기되었습니다.");
    if (code === "expired") return forbidden("라이센스가 만료되었습니다.");
    if (code === "forbidden") return forbidden("라이센스가 정지되었습니다.");
    return unauthorized();
  }
}

async function handleAdminLicenseSessions(req: Request, env: Env, licenseId: number): Promise<Response> {
  try {
    await requireAdmin(req, env);
  } catch {
    return unauthorized("관리자 인증이 필요합니다.");
  }

  if (req.method !== "GET") return methodNotAllowed();

  const rows = await env.DB
    .prepare(
      `SELECT id, token_prefix, created_at, last_seen_at, revoked_at, device_id
       FROM sessions
       WHERE license_id = ?
       ORDER BY id DESC
       LIMIT 50`
    )
    .bind(licenseId)
    .all<{
      id: number;
      token_prefix: string;
      created_at: number;
      last_seen_at: number;
      revoked_at: number | null;
      device_id: string;
    }>();

  return json({ ok: true, sessions: rows.results || [] });
}

async function handleAdminSessionRevoke(req: Request, env: Env, sessionId: number): Promise<Response> {
  try {
    await requireAdmin(req, env);
  } catch {
    return unauthorized("관리자 인증이 필요합니다.");
  }

  if (req.method !== "POST") return methodNotAllowed();

  const now = nowUnixSeconds();
  const result = await env.DB
    .prepare("UPDATE sessions SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL")
    .bind(now, sessionId)
    .run();

  const changed = result.meta?.changes || 0;
  return json({ ok: true, revoked: changed > 0 });
}

async function handlePlatformDomains(req: Request, env: Env): Promise<Response> {
  if (req.method !== "GET") return methodNotAllowed();

  try {
    await requireSession(req, env);
  } catch (e) {
    const code = String(e instanceof Error ? e.message : e);
    if (code === "unauthorized") return unauthorized();
    if (code === "expired") return forbidden("라이센스가 만료되었습니다.");
    if (code === "forbidden") return forbidden("라이센스 상태가 활성(active)이 아닙니다.");
    if (code === "server_misconfigured") return json({ ok: false, error: "server_misconfigured" }, { status: 500 });
    return unauthorized();
  }

  const rows = await env.DB.prepare("SELECT site_key, domain, updated_at FROM platform_domains ORDER BY site_key").all<{
    site_key: string;
    domain: string;
    updated_at: number;
  }>();

  const domains: Record<string, string> = {};
  let maxUpdated = 0;
  for (const r of rows.results || []) {
    domains[String(r.site_key)] = String(r.domain);
    maxUpdated = Math.max(maxUpdated, Number(r.updated_at) || 0);
  }

  return json({ ok: true, domains, updated_at: maxUpdated || null });
}

async function handleAdminLicenses(req: Request, env: Env): Promise<Response> {
  try {
    await requireAdmin(req, env);
  } catch {
    return unauthorized("관리자 인증이 필요합니다.");
  }

  if (req.method === "GET") {
    const rows = await env.DB
      .prepare(
        `
        SELECT id, key_prefix, company_name, created_at, expires_at, status, note
        FROM licenses
        ORDER BY id DESC
        `
      )
      .all<{
        id: number;
        key_prefix: string;
        company_name: string;
        created_at: number;
        expires_at: number;
        status: LicenseStatus;
        note: string;
      }>();
    return json({ ok: true, licenses: rows.results || [] });
  }

  if (req.method === "POST") {
    const body = await parseJson<{ company_name?: unknown; days?: unknown; note?: unknown }>(req);
    if (!body) return badRequest("JSON 형식의 요청 본문이 필요합니다.");

    const companyName = String(body.company_name || "").trim();
    const note = String(body.note || "").trim();
    const daysRaw = body.days;

    if (!companyName) return badRequest("company_name이 비어 있습니다.");

    const days = Number(daysRaw);
    if (!Number.isFinite(days) || days <= 0 || days > 3650) return badRequest("days는 1~3650 범위의 숫자여야 합니다.");

    const pepper = (env.LICENSE_PEPPER || "").trim();
    if (!pepper) return json({ ok: false, error: "server_misconfigured", message: "서버 설정 오류: LICENSE_PEPPER 누락" }, { status: 500 });

    const now = nowUnixSeconds();
    const expiresAt = now + Math.floor(days * 86400);

    // Generate license key (one-time reveal)
    const licenseKey = `JUMP-${randomToken(24)}`;
    const keyHash = await sha256Hex(`${pepper}:${licenseKey}`);
    const keyPrefix = tokenPrefix(licenseKey);

    const res = await env.DB
      .prepare(
        `
        INSERT INTO licenses(key_hash, key_prefix, company_name, created_at, expires_at, status, note)
        VALUES(?, ?, ?, ?, ?, ?, ?)
        `
      )
      .bind(keyHash, keyPrefix, companyName, now, expiresAt, "active", note)
      .run();

    const id = Number(res.meta?.last_row_id || 0);
    return json({
      ok: true,
      license_key: licenseKey,
      license: { id, key_prefix: keyPrefix, company_name: companyName, created_at: now, expires_at: expiresAt, status: "active", note },
    });
  }

  return methodNotAllowed();
}

async function handleAdminLicenseAction(req: Request, env: Env, licenseId: number, action: string): Promise<Response> {
  try {
    await requireAdmin(req, env);
  } catch {
    return unauthorized("관리자 인증이 필요합니다.");
  }

  if (req.method !== "POST") return methodNotAllowed();

  const now = nowUnixSeconds();

  if (action === "extend") {
    const body = await parseJson<{ days?: unknown }>(req);
    if (!body) return badRequest("JSON 형식의 요청 본문이 필요합니다.");
    const days = Number(body.days);
    if (!Number.isFinite(days) || days <= 0 || days > 3650) return badRequest("days는 1~3650 범위의 숫자여야 합니다.");

    // extend from max(now, expires_at)
    const current = await env.DB.prepare("SELECT expires_at FROM licenses WHERE id = ?").bind(licenseId).first<{ expires_at: number }>();
    if (!current) return notFound();

    const base = Math.max(now, Number(current.expires_at) || 0);
    const newExpires = base + Math.floor(days * 86400);
    await env.DB.prepare("UPDATE licenses SET expires_at = ? WHERE id = ?").bind(newExpires, licenseId).run();
    return json({ ok: true, expires_at: newExpires });
  }

  if (action === "suspend" || action === "resume" || action === "revoke") {
    const nextStatus: LicenseStatus =
      action === "suspend" ? "suspended" : action === "resume" ? "active" : "revoked";

    await env.DB.prepare("UPDATE licenses SET status = ? WHERE id = ?").bind(nextStatus, licenseId).run();

    if (nextStatus !== "active") {
      // revoke all active sessions
      await env.DB
        .prepare("UPDATE sessions SET revoked_at = ? WHERE license_id = ? AND revoked_at IS NULL")
        .bind(now, licenseId)
        .run();
    }

    return json({ ok: true, status: nextStatus });
  }

  return badRequest("지원하지 않는 액션입니다.", { action });
}

async function handleAdminPlatformDomains(req: Request, env: Env): Promise<Response> {
  try {
    await requireAdmin(req, env);
  } catch {
    return unauthorized("관리자 인증이 필요합니다.");
  }

  if (req.method === "GET") {
    const rows = await env.DB.prepare("SELECT site_key, domain, updated_at FROM platform_domains ORDER BY site_key").all<{
      site_key: string;
      domain: string;
      updated_at: number;
    }>();
    return json({ ok: true, domains: rows.results || [] });
  }

  if (req.method === "PUT") {
    const body = await parseJson<{ site_key?: unknown; domain?: unknown }>(req);
    if (!body) return badRequest("JSON 형식의 요청 본문이 필요합니다.");
    const siteKey = String(body.site_key || "").trim();
    const domain = normalizeDomain(String(body.domain || ""));
    if (!siteKey) return badRequest("site_key가 비어 있습니다.");
    if (!domain) return badRequest("domain이 비어 있습니다.");

    const now = nowUnixSeconds();
    await env.DB
      .prepare(
        `
        INSERT INTO platform_domains(site_key, domain, updated_at)
        VALUES(?, ?, ?)
        ON CONFLICT(site_key) DO UPDATE SET
          domain = excluded.domain,
          updated_at = excluded.updated_at
        `
      )
      .bind(siteKey, domain, now)
      .run();

    return json({ ok: true, site_key: siteKey, domain, updated_at: now });
  }

  if (req.method === "DELETE") {
    const url = new URL(req.url);
    const siteKey = (url.searchParams.get("site_key") || "").trim();
    if (!siteKey) return badRequest("query param site_key가 필요합니다.");
    await env.DB.prepare("DELETE FROM platform_domains WHERE site_key = ?").bind(siteKey).run();
    return json({ ok: true });
  }

  return methodNotAllowed();
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    const path = url.pathname;

    // Health
    if (path === "/v1/health") {
      if (req.method !== "GET") return methodNotAllowed();
      return json({ ok: true, service: "jump-backend", env: env.ENV || "unknown" });
    }

    // Auth
    if (path === "/v1/auth/login") return handleAuthLogin(req, env);
    if (path === "/v1/auth/logout") return handleAuthLogout(req, env);

    // Heartbeat (session validation)
    if (path === "/v1/auth/heartbeat") return handleAuthHeartbeat(req, env);

    // User API
    if (path === "/v1/platform-domains") return handlePlatformDomains(req, env);

    // Admin health
    if (path === "/v1/admin/health") {
      try {
        await requireAdmin(req, env);
      } catch {
        return unauthorized("관리자 인증이 필요합니다.");
      }
      if (req.method !== "GET") return methodNotAllowed();
      return json({ ok: true, admin: true });
    }

    // Admin: licenses
    if (path === "/v1/admin/licenses") return handleAdminLicenses(req, env);
    {
      const m = /^\/v1\/admin\/licenses\/(\d+)\/(extend|suspend|resume|revoke)$/.exec(path);
      if (m) {
        const licenseId = Number(m[1]);
        const action = m[2];
        return handleAdminLicenseAction(req, env, licenseId, action);
      }
    }
    // Admin: sessions per license
    {
      const m = /^\/v1\/admin\/licenses\/(\d+)\/sessions$/.exec(path);
      if (m) return handleAdminLicenseSessions(req, env, Number(m[1]));
    }
    // Admin: revoke individual session
    {
      const m = /^\/v1\/admin\/sessions\/(\d+)\/revoke$/.exec(path);
      if (m) return handleAdminSessionRevoke(req, env, Number(m[1]));
    }

    // Admin: platform domains
    if (path === "/v1/admin/platform-domains") return handleAdminPlatformDomains(req, env);

    // Default
    if (path.startsWith("/v1/")) return notFound();
    return notFound();
  },
};

