from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests

DEFAULT_ADMIN_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


class ApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


@dataclass(frozen=True)
class AdminApiConfig:
    base_url: str
    access_client_id: str
    access_client_secret: str
    admin_token: str = ""


class JumpAdminApi:
    def __init__(self, cfg: AdminApiConfig, *, timeout_s: float = 20.0) -> None:
        self.cfg = cfg
        self.timeout_s = float(timeout_s)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": DEFAULT_ADMIN_UA,
                # Cloudflare Access Service Token headers
                "CF-Access-Client-Id": cfg.access_client_id,
                "CF-Access-Client-Secret": cfg.access_client_secret,
            }
        )
        if cfg.admin_token:
            self.session.headers["X-Admin-Token"] = cfg.admin_token

    def _url(self, path: str) -> str:
        return self.cfg.base_url.rstrip("/") + path

    def _request(self, method: str, path: str, *, params: dict[str, str] | None = None, json_body: Any = None) -> Any:
        url = self._url(path)
        try:
            resp = self.session.request(
                method.upper(),
                url,
                params=params,
                data=None if json_body is None else json.dumps(json_body, ensure_ascii=False),
                timeout=self.timeout_s,
            )
        except requests.RequestException as exc:
            raise ApiError(f"요청 실패: {exc}") from exc

        payload: Any = None
        try:
            payload = resp.json()
        except Exception:
            payload = resp.text

        if resp.status_code >= 400:
            msg = "요청 실패"
            if isinstance(payload, dict) and payload.get("message"):
                msg = str(payload.get("message"))
            raise ApiError(msg, status_code=resp.status_code, payload=payload)

        return payload

    # ---- Health ----
    def health(self) -> Any:
        return self._request("GET", "/v1/admin/health")

    # ---- Licenses ----
    def list_licenses(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/v1/admin/licenses")
        return list((data or {}).get("licenses") or [])

    def create_license(self, company_name: str, days: int, note: str = "") -> dict[str, Any]:
        return self._request("POST", "/v1/admin/licenses", json_body={"company_name": company_name, "days": int(days), "note": note})

    def extend_license(self, license_id: int, days: int) -> dict[str, Any]:
        return self._request("POST", f"/v1/admin/licenses/{int(license_id)}/extend", json_body={"days": int(days)})

    def suspend_license(self, license_id: int) -> dict[str, Any]:
        return self._request("POST", f"/v1/admin/licenses/{int(license_id)}/suspend", json_body={})

    def resume_license(self, license_id: int) -> dict[str, Any]:
        return self._request("POST", f"/v1/admin/licenses/{int(license_id)}/resume", json_body={})

    def revoke_license(self, license_id: int) -> dict[str, Any]:
        return self._request("POST", f"/v1/admin/licenses/{int(license_id)}/revoke", json_body={})

    # ---- Sessions ----
    def list_sessions(self, license_id: int) -> list[dict[str, Any]]:
        data = self._request("GET", f"/v1/admin/licenses/{int(license_id)}/sessions")
        return list((data or {}).get("sessions") or [])

    def revoke_session(self, session_id: int) -> dict[str, Any]:
        return self._request("POST", f"/v1/admin/sessions/{int(session_id)}/revoke", json_body={})

    # ---- Platform domains ----
    def list_domains(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/v1/admin/platform-domains")
        return list((data or {}).get("domains") or [])

    def set_domain(self, site_key: str, domain: str) -> dict[str, Any]:
        return self._request("PUT", "/v1/admin/platform-domains", json_body={"site_key": site_key, "domain": domain})

    def delete_domain(self, site_key: str) -> dict[str, Any]:
        return self._request("DELETE", "/v1/admin/platform-domains", params={"site_key": site_key})
