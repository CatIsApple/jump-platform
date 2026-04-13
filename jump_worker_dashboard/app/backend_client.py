from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests

DEFAULT_BACKEND_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


class BackendError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def normalize_base_url(value: str) -> str:
    return (value or "").strip().rstrip("/")


@dataclass(frozen=True)
class BackendConfig:
    base_url: str
    timeout_s: float = 20.0


class WorkerBackendClient:
    def __init__(self, cfg: BackendConfig) -> None:
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": DEFAULT_BACKEND_UA,
            }
        )

    def _url(self, path: str) -> str:
        return normalize_base_url(self.cfg.base_url) + path

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str = "",
        json_body: Any = None,
    ) -> Any:
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            resp = self.session.request(
                method.upper(),
                self._url(path),
                data=None if json_body is None else json.dumps(json_body, ensure_ascii=False),
                headers=headers,
                timeout=float(self.cfg.timeout_s),
            )
        except requests.RequestException as exc:
            raise BackendError(f"요청 실패: {exc}") from exc

        payload: Any = None
        try:
            payload = resp.json()
        except Exception:
            payload = resp.text

        if resp.status_code >= 400:
            msg = "요청 실패"
            if isinstance(payload, dict):
                if payload.get("message"):
                    msg = str(payload.get("message"))
                elif payload.get("error"):
                    msg = str(payload.get("error"))
            raise BackendError(msg, status_code=resp.status_code, payload=payload)

        return payload

    def login(self, license_key: str, device_id: str) -> dict[str, Any]:
        data = self._request(
            "POST",
            "/v1/auth/login",
            json_body={
                "license_key": license_key,
                "device_id": device_id,
            },
        )
        return data if isinstance(data, dict) else {}

    def logout(self, token: str) -> dict[str, Any]:
        data = self._request("POST", "/v1/auth/logout", token=token)
        return data if isinstance(data, dict) else {}

    def heartbeat(self, token: str) -> dict[str, Any]:
        data = self._request("GET", "/v1/auth/heartbeat", token=token)
        return data if isinstance(data, dict) else {}

    def platform_domains(self, token: str) -> dict[str, Any]:
        data = self._request("GET", "/v1/platform-domains", token=token)
        return data if isinstance(data, dict) else {}
