from __future__ import annotations

import random
import string
import time
from typing import Callable
from urllib.parse import parse_qs, urlparse

from ..file_manager import load_json, save_json

# 일부 사이트의 자체 서명 SSL 인증서 경고 억제
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_COOLDOWN = "cooldown"
STATUS_INSUFFICIENT = "insufficient"
STATUS_LOGIN_REQUIRED = "login_required"


def _try_import_requests(emit: Callable[[str, str], None], user: dict):
    try:
        import requests  # type: ignore

        return requests
    except Exception as exc:  # noqa: BLE001
        emit(f"[{user['name']}] requests 모듈이 필요합니다: {exc}", "ERROR")
        return None


def _try_import_bs4(emit: Callable[[str, str], None], user: dict):
    try:
        from bs4 import BeautifulSoup  # type: ignore

        return BeautifulSoup
    except Exception as exc:  # noqa: BLE001
        emit(f"[{user['name']}] beautifulsoup4 모듈이 필요합니다: {exc}", "ERROR")
        return None


def _uuid26() -> str:
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choices(chars, k=26))


def _join(domain: str, path: str) -> str:
    return domain.rstrip("/") + path


def _ua() -> str:
    # 원본 jump.exe는 Windows + Chrome 141/142 계열 UA를 사용한다.
    return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"


def _headers(
    *,
    host: str | None = None,
    origin: str | None = None,
    referer: str | None = None,
    cookie: str | None = None,
    accept_json: bool = False,
) -> dict[str, str]:
    # 원본 jump.exe의 요청 헤더를 최대한 따라간다(일부 사이트는 헤더/리퍼러를 민감하게 본다).
    h: dict[str, str] = {
        "Connection": "keep-alive",
        "User-Agent": _ua(),
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Sec-CH-UA": '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": '"Windows"',
    }
    if host:
        h["Host"] = host
    if origin:
        h["Origin"] = origin
    if referer:
        h["Referer"] = referer
    if cookie:
        h["Cookie"] = cookie

    if accept_json:
        h.update(
            {
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
            }
        )
    else:
        h.update(
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-User": "?1",
                "Sec-Fetch-Dest": "document",
            }
        )
    return h


# -------------------- 밤의제국 --------------------


def _bamje_login(user: dict, emit: Callable[[str, str], None]) -> str | None:
    req = _try_import_requests(emit, user)
    if req is None:
        return None

    host = urlparse(user["domain"]).netloc or user["domain"].replace("https://", "").strip("/")
    uuid = _uuid26()
    url = _join(user["domain"], f"/bbs/login_check.php?_={int(time.time() * 1000)}")
    payload = {"url": "%252F", "mb_id": user["id"], "mb_password": user["pw"]}

    try:
        res = req.post(
            url,
            data=payload,
            headers=_headers(
                host=host,
                origin=user["domain"],
                referer=user["domain"],
                cookie=f"PHPSESSID={uuid}",
            ),
            allow_redirects=False,
            timeout=15,
            verify=False,
        )
    except Exception as exc:  # noqa: BLE001
        emit(f"[{user['name']}] 로그인 요청 실패: {exc}", "ERROR")
        return None

    if res.status_code == 302:
        save_json(user["domain"], user["id"], {"token": uuid})
        return uuid
    if res.status_code == 403:
        emit(f"[{user['name']}] 로그인 실패(403) - 도메인/차단 확인 필요", "ERROR")
        return None

    emit(f"[{user['name']}] 로그인 실패(HTTP {res.status_code})", "ERROR")
    return None


def _bamje_check(user: dict, emit: Callable[[str, str], None]) -> bool:
    req = _try_import_requests(emit, user)
    if req is None:
        return False

    data = load_json(user["domain"], user["id"])
    if not data or not data.get("token"):
        return False

    host = urlparse(user["domain"]).netloc or user["domain"].replace("https://", "").strip("/")
    url = _join(user["domain"], "/bbs/userinfo.php")
    try:
        res = req.get(
            url,
            headers=_headers(
                host=host,
                referer=url,
                cookie=f"PHPSESSID={data['token']}",
            ),
            timeout=15,
            verify=False,
        )
    except Exception as exc:  # noqa: BLE001
        emit(f"[{user['name']}] 로그인 상태 확인 실패: {exc}", "WARNING")
        return False

    if res.status_code == 403:
        return False
    if 'alert(\"회원만 조회하실 수 있습니다.\")' in res.text:
        return False
    return True


def bamje(user: dict, emit: Callable[[str, str], None]) -> tuple[str, str]:
    req = _try_import_requests(emit, user)
    if req is None:
        return STATUS_FAILED, "requests 모듈 필요"

    if not _bamje_check(user, emit):
        if not _bamje_login(user, emit):
            return STATUS_LOGIN_REQUIRED, "로그인 실패"
        if not _bamje_check(user, emit):
            return STATUS_LOGIN_REQUIRED, "로그인 실패(확인 필요)"

    data = load_json(user["domain"], user["id"]) or {}
    token = data.get("token")
    if not token:
        return STATUS_FAILED, "토큰 없음"

    host = urlparse(user["domain"]).netloc or user["domain"].replace("https://", "").strip("/")
    url = _join(user["domain"], "/bbs/jump.php")
    try:
        res = req.get(
            url,
            headers=_headers(
                host=host,
                referer=_join(user["domain"], "/bbs/userinfo.php"),
                cookie=f"PHPSESSID={token}",
            ),
            timeout=15,
            verify=False,
        )
    except Exception as exc:  # noqa: BLE001
        emit(f"[{user['name']}] 점프 요청 실패: {exc}", "ERROR")
        return STATUS_FAILED, "요청 실패"

    if 'alert(\"점프가 완료되었습니다.\")' in res.text:
        emit(f"[성공] {user['name']} - {user['id']}: {user['startedAt']} 점프 완료", "INFO")
        return STATUS_SUCCESS, "점프 완료"
    if 'alert(\"본인의 업소만 점프가 가능합니다.\")' in res.text:
        return STATUS_FAILED, "본인 업소만 가능(권한/계정 확인)"

    return STATUS_FAILED, "점프 실패(알 수 없는 응답)"


# -------------------- 오피러브 --------------------


def _oplove_check(user: dict, emit: Callable[[str, str], None]) -> bool:
    req = _try_import_requests(emit, user)
    if req is None:
        return False
    BS = _try_import_bs4(emit, user)
    if BS is None:
        return False

    data = load_json(user["domain"], user["id"])
    if not data or not data.get("token"):
        return False

    host = urlparse(user["domain"]).netloc or user["domain"].replace("https://", "").strip("/")
    url = _join(user["domain"], "/bbs/board.php?bo_table=shop")
    try:
        res = req.get(
            url,
            headers=_headers(
                host=host,
                referer=_join(user["domain"], "/bbs/userinfo.php"),
                cookie=f"PHPSESSID={data['token']}",
            ),
            timeout=15,
            verify=False,
        )
    except Exception:
        return False

    if res.status_code == 403:
        return False
    if 'alert(\"회원만 가능합니다.\")' in res.text:
        return False

    # 원본 로직: onclick에서 wr_id(점프 대상)를 추출하여 저장
    try:
        soup = BS(res.text, "html.parser")
        tag = soup.find(
            "span",
            class_="jump_btn",
            onclick=lambda v: isinstance(v, str) and ("'" in v),
        )
        if not tag:
            return False
        onclick_value = tag.get("onclick") or ""
        parts = onclick_value.split("'")
        wr_id = parts[1] if len(parts) > 1 else ""
        if not wr_id:
            return False
        save_json(user["domain"], user["id"], {"wr_id": str(wr_id), "token": data["token"]})
        return True
    except Exception:
        return False


def _oplove_discover_wr_id(user: dict, token: str, emit: Callable[[str, str], None]) -> str | None:
    req = _try_import_requests(emit, user)
    if req is None:
        return None
    BS = _try_import_bs4(emit, user)
    if BS is None:
        return None

    url = _join(user["domain"], "/bbs/board.php?bo_table=shop")
    try:
        res = req.get(
            url,
            headers=_headers(referer=_join(user["domain"], "/"), cookie=f"PHPSESSID={token}"),
            timeout=15,
            verify=False,
        )
    except Exception as exc:  # noqa: BLE001
        emit(f"[{user['name']}] 샵 정보 조회 실패: {exc}", "WARNING")
        return None

    soup = BS(res.text, "html.parser")
    # 원본은 onclick에서 wr_id를 추출. 여기서는 href/query 기반으로 먼저 시도.
    for a in soup.find_all("a"):
        href = a.get("href") or ""
        if "wr_id=" not in href:
            continue
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        wr_id = (qs.get("wr_id") or [None])[0]
        if wr_id:
            return str(wr_id)
    return None


def _oplove_login(user: dict, emit: Callable[[str, str], None]) -> tuple[str | None, str | None]:
    # 원본 jump.exe는 oplove 로그인만 http.client(HTTPSConnection)로 구현되어 있다.
    try:
        import http.client
        import ssl
    except Exception:
        return (None, None)

    host = urlparse(user["domain"]).netloc or user["domain"].replace("https://", "").strip("/")
    if not host:
        return (None, None)

    uuid = _uuid26()
    payload = f"url=%252F&mb_id={user['id']}&mb_password={user['pw']}"

    headers = _headers(
        host=host,
        origin=user["domain"],
        referer=_join(user["domain"], "/"),
        cookie=f"PHPSESSID={uuid}",
        accept_json=False,
    )
    headers["Cache-Control"] = "max-age=0"
    headers["Content-Type"] = "application/x-www-form-urlencoded"

    try:
        ctx = ssl._create_unverified_context()
        conn = http.client.HTTPSConnection(host, timeout=15, context=ctx)
        conn.request("POST", "/bbs/login_check.php", payload, headers)
        res = conn.getresponse()
        status = int(getattr(res, "status", 0) or 0)
        set_cookie = res.getheader("Set-Cookie") or ""
    except Exception as exc:  # noqa: BLE001
        emit(f"[{user['name']}] 로그인 요청 실패: {exc}", "ERROR")
        return (None, None)
    finally:
        try:
            conn.close()  # type: ignore[name-defined]
        except Exception:
            pass

    if status == 403:
        return (None, None)
    if status != 302:
        return (None, None)

    # 원본 로직: Set-Cookie에서 PHPSESSID를 추출해 token으로 저장
    phpsessid: str | None = None
    try:
        cookies = str(set_cookie).split(", ")
        for c in cookies:
            if "PHPSESSID" not in c:
                continue
            phpsessid = c.split(";")[0].split("=", 1)[1]
            break
    except Exception:
        phpsessid = None

    if not phpsessid:
        return (None, None)
    save_json(user["domain"], user["id"], {"token": phpsessid})
    return (phpsessid, None)


def oplove(user: dict, emit: Callable[[str, str], None]) -> tuple[str, str]:
    req = _try_import_requests(emit, user)
    if req is None:
        return STATUS_FAILED, "requests 모듈 필요"

    if not _oplove_check(user, emit):
        token, _ = _oplove_login(user, emit)
        if not token:
            return STATUS_LOGIN_REQUIRED, "로그인 실패"
        if not _oplove_check(user, emit):
            return STATUS_LOGIN_REQUIRED, "로그인/샵정보 실패"

    data = load_json(user["domain"], user["id"]) or {}
    token = data.get("token")
    wr_id = data.get("wr_id")
    if not token or not wr_id:
        return STATUS_FAILED, "토큰/샵정보 없음"

    host = urlparse(user["domain"]).netloc or user["domain"].replace("https://", "").strip("/")
    url = _join(user["domain"], f"/run.jump.php?wr_id={wr_id}")
    try:
        res = req.get(
            url,
            headers=_headers(
                host=host,
                referer=_join(user["domain"], "/plugin/attendance/index.php"),
                cookie=f"PHPSESSID={token}",
            ),
            timeout=15,
            verify=False,
        )
    except Exception as exc:  # noqa: BLE001
        emit(f"[{user['name']}] 점프 요청 실패: {exc}", "ERROR")
        return STATUS_FAILED, "요청 실패"

    if 'alert(\"점프가 완료되었습니다.\")' in res.text:
        emit(f"[성공] {user['name']} - {user['id']}: {user['startedAt']} 점프 완료", "INFO")
        return STATUS_SUCCESS, "점프 완료"
    if 'alert(\"10분에 한번만 가능합니다.\")' in res.text:
        emit(f"[대기] {user['name']} - {user['id']}: {user['startedAt']} 10분 대기 룰", "INFO")
        return STATUS_COOLDOWN, "10분 대기 룰"
    if 'alert(\"회원만 가능합니다.\")' in res.text:
        return STATUS_LOGIN_REQUIRED, "로그인 필요"

    return STATUS_FAILED, "점프 실패(알 수 없는 응답)"


# -------------------- 오피나라 --------------------


def _opnara_shop_id_from_mypage(html: str) -> str | None:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except Exception:
        return None

    soup = BeautifulSoup(html, "html.parser")
    a = soup.find("a", string="출근부 수정하기")
    if not a:
        return None
    href = a.get("href") or ""
    parsed = urlparse(href)
    qs = parse_qs(parsed.query)
    wr_id = (qs.get("wr_id") or [None])[0]
    return str(wr_id) if wr_id else None


def _opnara_check(user: dict, emit: Callable[[str, str], None]) -> tuple[bool, str | None]:
    req = _try_import_requests(emit, user)
    if req is None:
        return (False, None)
    BS = _try_import_bs4(emit, user)
    if BS is None:
        return (False, None)

    data = load_json(user["domain"], user["id"])
    if not data or not data.get("token"):
        return (False, None)

    host = urlparse(user["domain"]).netloc or user["domain"].replace("https://", "").strip("/")
    url = _join(user["domain"], "/bbs/mypage.php")
    try:
        res = req.get(
            url,
            headers=_headers(host=host, referer=user["domain"], cookie=f"PHPSESSID={data['token']}"),
            timeout=15,
            verify=False,
        )
    except Exception:
        return (False, None)

    try:
        soup = BS(res.text, "html.parser")
        title = soup.title.string if soup.title else ""
    except Exception:
        title = ""

    if "마이페이지" in (title or ""):
        return (True, _opnara_shop_id_from_mypage(res.text))
    return (False, None)


def _opnara_login(user: dict, emit: Callable[[str, str], None]) -> tuple[str | None, str | None]:
    # 원본 jump.exe는 opnara 로그인만 http.client(HTTPSConnection)로 구현되어 있다.
    try:
        import http.client
        import ssl
    except Exception:
        return (None, None)

    host = urlparse(user["domain"]).netloc or user["domain"].replace("https://", "").strip("/")
    if not host:
        return (None, None)

    uuid = _uuid26()
    payload = f"url=%252F&mb_id={user['id']}&mb_password={user['pw']}"

    headers = _headers(
        host=host,
        origin=user["domain"],
        referer=_join(user["domain"], "/bbs/mypage.php"),
        cookie=f"PHPSESSID={uuid}",
        accept_json=False,
    )
    headers["Cache-Control"] = "max-age=0"
    headers["Content-Type"] = "application/x-www-form-urlencoded"

    try:
        ctx = ssl._create_unverified_context()
        conn = http.client.HTTPSConnection(host, timeout=15, context=ctx)
        conn.request("POST", "/bbs/login_check.php", payload, headers)
        res = conn.getresponse()
        status = int(getattr(res, "status", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        emit(f"[{user['name']}] 로그인 요청 실패: {exc}", "ERROR")
        return (None, None)
    finally:
        try:
            conn.close()  # type: ignore[name-defined]
        except Exception:
            pass

    if status == 403:
        return (None, None)
    if status != 302:
        return (None, None)

    # 토큰 저장 후 wr_id 재조회(원본 로직)
    save_json(user["domain"], user["id"], {"token": uuid, "wr_id": None})
    ok, wr_id = _opnara_check(user, emit)
    if ok and wr_id:
        save_json(user["domain"], user["id"], {"token": uuid, "wr_id": wr_id})
        return (uuid, wr_id)
    return (None, None)


def opnara(user: dict, emit: Callable[[str, str], None]) -> tuple[str, str]:
    req = _try_import_requests(emit, user)
    if req is None:
        return STATUS_FAILED, "requests 모듈 필요"

    ok, wr_id = _opnara_check(user, emit)
    if not ok or not wr_id:
        token, wr_id2 = _opnara_login(user, emit)
        if not token or not wr_id2:
            return STATUS_LOGIN_REQUIRED, "로그인/샵정보 실패"

    data = load_json(user["domain"], user["id"]) or {}
    token = data.get("token")
    wr_id = data.get("wr_id")
    if not token or not wr_id:
        return STATUS_FAILED, "토큰/샵정보 없음"

    host = urlparse(user["domain"]).netloc or user["domain"].replace("https://", "").strip("/")
    url = _join(user["domain"], f"/jump.php?wr_id={wr_id}")
    try:
        res = req.get(
            url,
            headers=_headers(
                host=host,
                referer=_join(user["domain"], "/bbs/mypage.php"),
                cookie=f"PHPSESSID={token};",
            ),
            timeout=15,
            verify=False,
        )
    except Exception as exc:  # noqa: BLE001
        emit(f"[{user['name']}] 점프 요청 실패: {exc}", "ERROR")
        return STATUS_FAILED, "요청 실패"

    text = res.text
    if "cuteAlert" in text and "점프가 완료되었습니다." in text:
        emit(f"[성공] {user['name']} - {user['id']}: {user['startedAt']} 점프 완료", "INFO")
        return STATUS_SUCCESS, "점프 완료"
    if "cuteAlert" in text and "5분에 한번만 가능합니다." in text:
        emit(f"[대기] {user['name']} - {user['id']}: {user['startedAt']} 5분 대기 룰", "INFO")
        return STATUS_COOLDOWN, "5분 대기 룰"
    if "cuteAlert" in text and "점프 횟수가 없습니다." in text:
        return STATUS_INSUFFICIENT, "점프 횟수 부족"
    if "cuteAlert" in text and "회원만 가능합니다." in text:
        return STATUS_LOGIN_REQUIRED, "로그인 필요"

    return STATUS_FAILED, "점프 실패(확인 필요)"


# -------------------- 오피마트 --------------------


def _opmart_login(user: dict, emit: Callable[[str, str], None]) -> bool:
    req = _try_import_requests(emit, user)
    if req is None:
        return False

    host = urlparse(user["domain"]).netloc or user["domain"].replace("https://", "").strip("/")
    url = f"https://{host}/login/login_proc.php"
    payload = f"act=login&referer={user.get('domain')}&user_id={user.get('id')}&user_pw={user.get('pw')}"

    try:
        res = req.post(
            url,
            data=payload,
            headers=_headers(
                host=host,
                origin=f"https://{host}",
                referer=f"https://{host}/login/login.php",
                accept_json=True,
            ),
            timeout=15,
            verify=False,
        )
    except Exception as exc:  # noqa: BLE001
        emit(f"[{user['name']}] 로그인 요청 실패: {exc}", "ERROR")
        return False

    try:
        result = res.json()
    except Exception:
        return False

    if res.status_code == 200 and bool(result.get("result")):
        user_auth = res.cookies.get("user_auth")
        wuinfo = res.cookies.get("wuinfo")
        if not user_auth or not wuinfo:
            return False
        save_json(user["domain"], user["id"], {"user_auth": user_auth, "wuinfo": wuinfo})
        return True
    return False


def _opmart_check(user: dict, emit: Callable[[str, str], None]) -> bool:
    req = _try_import_requests(emit, user)
    if req is None:
        return False
    BS = _try_import_bs4(emit, user)
    if BS is None:
        return False

    co = load_json(user.get("domain"), user.get("id"))
    if not co:
        return False

    host = urlparse(user["domain"]).netloc or user["domain"].replace("https://", "").strip("/")
    url = f"https://{host}/member/mypage_user_info.php"
    try:
        res = req.get(
            url,
            headers=_headers(
                host=host,
                referer=f"https://{host}/c_member/jump.php",
                cookie=f"user_auth={co.get('user_auth')}; wuinfo={co.get('wuinfo')};",
            ),
            timeout=15,
            verify=False,
        )
    except Exception:
        return False

    try:
        soup = BS(res.text, "html.parser")
        title = soup.title.string if soup.title else ""
    except Exception:
        title = ""
    return "마이페이지" in (title or "")


def opmart(user: dict, emit: Callable[[str, str], None]) -> tuple[str, str]:
    req = _try_import_requests(emit, user)
    if req is None:
        return STATUS_FAILED, "requests 모듈 필요"

    if not _opmart_check(user, emit):
        if not _opmart_login(user, emit):
            return STATUS_LOGIN_REQUIRED, "로그인 실패"
        if not _opmart_check(user, emit):
            return STATUS_LOGIN_REQUIRED, "로그인 실패(확인 필요)"

    co = load_json(user.get("domain"), user.get("id")) or {}
    host = urlparse(user["domain"]).netloc or user["domain"].replace("https://", "").strip("/")
    url = f"https://{host}/c_member/jump_proc.php"
    payload = "act=manual"
    try:
        res = req.post(
            url,
            data=payload,
            headers=_headers(
                host=host,
                origin=f"https://{host}",
                referer=f"https://{host}/c_member/jump.php",
                cookie=f"user_auth={co.get('user_auth')}; wuinfo={co.get('wuinfo')};",
                accept_json=True,
            ),
            timeout=15,
            verify=False,
        )
    except Exception as exc:  # noqa: BLE001
        emit(f"[{user['name']}] 점프 요청 실패: {exc}", "ERROR")
        return STATUS_FAILED, "요청 실패"

    try:
        v = res.json()
    except Exception:
        v = {}

    if res.status_code == 200 and bool(v.get("result")):
        emit(f"[성공] {user['name']} - {user['id']}: {user['startedAt']} 점프 완료", "INFO")
        return STATUS_SUCCESS, "점프 완료"

    # 원본은 200이 아니면서 result가 True인 경우를 10분대기/점프부족으로 분기했는데,
    # 여기서는 안전하게 실패로 처리하되 메시지를 남긴다.
    if bool(v.get("result")) and res.status_code != 200:
        return STATUS_COOLDOWN, "10분 대기 또는 점프 부족(구분 불가)"

    return STATUS_FAILED, "점프 실패(확인 필요)"
