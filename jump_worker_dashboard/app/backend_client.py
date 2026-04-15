from __future__ import annotations

import json
import platform as _platform
from dataclasses import dataclass
from typing import Any

import requests


def _build_default_ua() -> str:
    """실행 플랫폼을 반영한 User-Agent 문자열 생성.

    백엔드가 세션 목록 UI에 OS/클라이언트를 구분해서 표시할 수 있도록
    실제 플랫폼 정보를 포함. Mozilla 호환 포맷 + 앱 식별자.
    """
    system = _platform.system()
    release = _platform.release()
    machine = _platform.machine() or "x64"

    try:
        from jump_worker_dashboard import __version__ as _app_ver
    except Exception:
        _app_ver = "unknown"

    if system == "Windows":
        os_token = f"Windows NT {release or '10.0'}; {machine}"
    elif system == "Darwin":
        mac_ver = (_platform.mac_ver()[0] or "14.0").replace(".", "_")
        os_token = f"Macintosh; Intel Mac OS X {mac_ver}"
    elif system == "Linux":
        os_token = f"X11; Linux {machine}"
    else:
        os_token = f"{system} {release}; {machine}"

    return (
        f"Mozilla/5.0 ({os_token}) "
        f"jump-worker-dashboard/{_app_ver} "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )


DEFAULT_BACKEND_UA = _build_default_ua()


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
