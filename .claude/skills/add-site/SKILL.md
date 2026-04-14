---
name: add-site
description: jump-platform에 새 사이트(업소 점프 대상)를 추가. gnuboard/XE/Laravel 등 프레임워크를 자동 판별해 적절한 템플릿으로 Site 모듈 생성, SITE_REGISTRY + SITE_KEYS 등록, 백엔드 D1에 도메인 등록, 로컬 빌드 검증까지 일괄 처리. 사용 시점: 사용자가 "사이트 추가", "새 사이트", "xxx 추가해줘(URL)", "xxx.com 사이트 만들어" 등을 요청할 때.
---

# Add Site Skill — 새 사이트 추가 전체 자동화

jump-platform에 점프 대상 사이트를 추가하는 표준 워크플로우.

---

## 1. 사용자로부터 정보 수집

다음 정보를 **반드시** 확인:

| 필수 | 정보 | 예시 |
|---|---|---|
| 1 | 사이트 한글명 (SITE_KEY) | "부산비비기", "대구의밤" |
| 2 | 도메인 | "busanb37.net", "eorn3.com" |
| 3 | 로그인 폼 HTML 또는 URL | `/login`, `/bbs/login.php`, 또는 메인 페이지 헤더 폼 |
| 4 | 점프 버튼/링크 HTML | onclick, href, 또는 form action |
| 5 | 성공 alert 또는 페이지 텍스트 | "완료", "올렸습니다" |
| 6 | 실패/쿨다운 alert/텍스트 | "초과", "5분에 한번" |

부족하면 사용자에게 질문. **절대 추측하지 말 것**.

---

## 2. 프레임워크 판별

HTML에서 프레임워크 감지:

### gnuboard (가장 흔함)
- 로그인 URL: `/bbs/login.php` 또는 홈페이지의 `form#basic_outlogin`
- 필드: `name="mb_id"`, `name="mb_password"`
- 점프/기능: `/bbs/*.php?wr_id=N` 또는 JS 함수
- 쿠키: `PHPSESSID`
- **템플릿**: `GnuboardSite` 상속
- **경로**: `jump_site_modules/gnuboard/{name}/`

### XE (XpressEngine)
- 로그인 URL: `/index.php?act=procMemberLogin`
- 필드: `name="user_id"`, `name="password"`, hidden `_rx_csrf_token`
- 점프: `doCallModuleAction('module', 'action', id)` JS 호출
- 쿠키: `PHPSESSID`
- **템플릿**: `BaseSite` 상속 (custom) 또는 직접 구현
- **경로**: `jump_site_modules/xe/{name}/`

### Laravel
- 로그인 URL: `/login` POST
- 필드: `name="username"` (또는 email), `name="password"`, hidden `name="_token"`
- 쿠키: `laravel_session`, `XSRF-TOKEN`
- **템플릿**: `BaseSite` 상속
- **경로**: `jump_site_modules/custom/{name}/`

### 기타 (전용 프레임워크)
- 모든 게 커스텀 → `jump_site_modules/custom/{name}/`

---

## 3. 기존 유사 사이트 참고

프레임워크별 참고 구현 (이미 검증됨):

| 프레임워크 | 단순 | 복잡 (alert+파싱) | JS 기반 점프 | 멀티 포스트 |
|---|---|---|---|---|
| gnuboard | `bamje`, `indal` | `busanbibigi`, `opguide` | `obam` | - |
| XE | `sexbam` | `albam` (아이러브밤) | `albam` | `albam` |
| Laravel | - | `daegubam` | - | - |

**반드시 유사 사이트의 `site.py`를 먼저 읽고 패턴을 재사용**. 새 패턴 억지로 만들지 말 것.

---

## 4. 구현 체크리스트

### 4-1. 모듈 생성

```
jump_site_modules/{framework}/{name}/
├── __init__.py       # from .site import {Name}Site
└── site.py           # 실제 구현
```

`__init__.py`:
```python
from .site import {Name}Site
__all__ = ["{Name}Site"]
```

### 4-2. site.py 구현 체크리스트

**gnuboard 기반** (예: 부산비비기, 인천달리기, 오밤):
```python
class {Name}Site(GnuboardSite):
    SITE_NAME = "{한글명}"
    COOKIE_KEYS = ["PHPSESSID"]
    LOGIN_URL_PATH = "/"           # 홈 페이지 outlogin 폼
    LOGIN_CHECK_TEXT = "로그아웃"
    LOGIN_ID_SELECTOR = "#outlogin_mb_id"     # 사이트별 다름
    LOGIN_PW_SELECTOR = "#outlogin_mb_password"
    LOGIN_SUBMIT_SELECTOR = "#basic_outlogin input[type='submit']"
    LOGIN_POST_SUBMIT_DELAY = 2.0

    def jump(self) -> JumpResult:
        # 1. 메인 페이지 이동
        self.driver.get(f"{self.base_url}/")
        time.sleep(1.5)
        self.require_human_check()

        # 2. 로그인 체크
        if not self.page_contains("로그아웃"):
            return JumpResult(status=STATUS_LOGIN_REQUIRED, ...)

        # 3. 점프 링크/버튼 찾기 + 횟수 파싱
        # 4. JS 호출 or URL 이동
        # 5. alert 처리
        # 6. before/after 카운트 비교 or 텍스트 파싱
```

**XE 기반** (예: 아이러브밤, 섹밤):
- `BaseSite` 상속
- `login()`: 홈페이지 폼에서 user_id/password 채우고 `form.submit()` (CSRF 자동)
- `jump()`: `doCallModuleAction('module', 'action', doc_id)` JS 호출
- 멀티 포스트 지원 필요시 `jump_posts(urls)` 추가 구현

**Laravel 기반** (예: 대구의밤):
- `BaseSite` 상속
- `login()`: `/login` 페이지 접근 → CSRF `_token` 자동 포함 (폼 전체 submit) → 리디렉션 확인
- `jump()`: 대상 페이지 이동 → form.submit() → 리디렉션 후 페이지 텍스트에서 결과 파싱

### 4-3. 중요 구현 디테일

**로그인 상태 판별은 텍스트 매칭보다 DOM 기반 선호** (오탐 방지):
```python
def _is_logged_in(self) -> bool:
    # 로그아웃 링크 존재 확인
    if self.driver.find_elements(By.CSS_SELECTOR, "a[href*='logout']"):
        return True
    # 로그인 폼 존재하면 비로그인
    if self.driver.find_elements(By.CSS_SELECTOR, "input[name='mb_id']"):
        return False
    return False
```

**confirm() 우회**:
```python
self.driver.execute_script("window.confirm = function() { return true; };")
# 그 후 버튼 클릭 또는 form.submit()
```

**alert 처리 패턴**:
```python
try:
    WebDriverWait(self.driver, 5).until(EC.alert_is_present())
    alert = self.driver.switch_to.alert
    text = alert.text
    alert.accept()
except Exception:
    text = ""

# 분류
if "완료" in text or "올렸습니다" in text or "되었습니다" in text:
    status = STATUS_SUCCESS
elif "초과" in text or "모두 사용" in text:
    status = STATUS_COOLDOWN
elif "분에 한번" in text or "분만" in text:
    status = STATUS_COOLDOWN
elif "로그인" in text or "회원" in text:
    status = STATUS_LOGIN_REQUIRED
else:
    status = STATUS_FAILED
```

**URL에서 ID 추출** (rewrite URL 대응):
```python
# /index.php?document_srl=12345 또는 /board_xx/12345 또는 /12345 모두 대응
m = re.search(r"document_srl[=/](\d+)", post_url)
if not m:
    m = re.search(r"/(\d{5,})(?:[/?#]|$)", post_url)
doc_id = m.group(1) if m else None
```

### 4-4. 레지스트리 등록 (3곳)

**A. `jump_site_modules/__init__.py`** (import + SITE_REGISTRY):
```python
from .{framework}.{name} import {Name}Site

SITE_REGISTRY: dict[str, type[BaseSite]] = {
    ...
    "{한글명}": {Name}Site,
}
```

**B. `jump_site_modules/{framework}/__init__.py`** (프레임워크 인덱스):
```python
from .{name} import {Name}Site
__all__ = [..., "{Name}Site"]
```

**C. `jump_worker_dashboard/app/sites.py`** (SITE_KEYS + BROWSER_REQUIRED_SITES):
```python
SITE_KEYS = [..., "{한글명}"]
BROWSER_REQUIRED_SITES = {..., "{한글명}"}
```

### 4-5. 백엔드 도메인 등록

> **CF Access 자격증명은 `.claude/secrets.local.md`에서 참조** (gitignore로 제외됨).

```bash
# secrets.local.md 의 Access Client ID/Secret 사용
curl -s -X PUT \
  -H "CF-Access-Client-Id: fc1aa72f3308d25496c24f5ba6e9eae3.access" \
  -H "CF-Access-Client-Secret: 0626ec1116c10885e4e88300885619cd9c0fd74b167b4ebc90767da201da6895" \
  -H "Content-Type: application/json" \
  -d '{"site_key":"{한글명}","domain":"{domain}","enabled":true}' \
  https://api.guardian01.online/v1/admin/platform-domains
```

이미 서버 DB에 site_key가 있으면 PATCH로 활성화만 하면 됨:
```bash
# 활성화
-X PATCH ... -d '{"site_key":"{한글명}","enabled":true}'
```

---

## 5. 검증

### 5-1. Python import 테스트

```bash
cd /Users/daon/Downloads/dist/jump_platform
PYTHONPATH=. python3 -c "
from jump_site_modules import list_sites, get_site_class
sites = list_sites()
assert '{한글명}' in sites, f'Missing: {한글명}'
cls = get_site_class('{한글명}')
print(f'✓ {cls.SITE_NAME} / {cls.__name__}')
"
```

### 5-2. 서버 동기화 확인

```bash
# 토큰 필요 (테스트 라이센스 생성 → login)
curl -H "Authorization: Bearer TOKEN" https://api.guardian01.online/v1/platform-domains | \
  python3 -c "import sys, json; d=json.load(sys.stdin); print('{한글명}' in d['domains'])"
```

### 5-3. 로컬 macOS 빌드 + 실행

```bash
cd /Users/daon/Downloads/dist/jump_platform/jump_worker_dashboard
rm -rf ./jump_site_modules
cp -r ../jump_site_modules ./jump_site_modules
./scripts/build_macos.sh
xattr -cr dist/jump-worker-dashboard.app
open dist/jump-worker-dashboard.app
```

클라이언트에서 "서버 동기화" → 새 사이트가 드롭다운에 나타나는지 확인.

---

## 6. 커밋 & 릴리즈

### 6-1. 커밋 (main push, 빠른 PyInstaller 검증 빌드 트리거)

```bash
git add \
  jump_site_modules/__init__.py \
  jump_site_modules/{framework}/__init__.py \
  jump_site_modules/{framework}/{name}/ \
  jump_worker_dashboard/app/sites.py

git commit -m "$(cat <<'EOF'
feat: add {한글명} ({domain}) site — {framework}

- {주요 구현 요약}
- Login: {로그인 방식}
- Jump: {점프 방식}
- Result detection: {성공/실패 판정 방식}

Backend: registered {한글명} → {domain}

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
git push origin main
```

### 6-2. 태그 푸시 (Nuitka 릴리즈 빌드 트리거)

`release` skill 활용:
```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

---

## 7. 자주 하는 실수 & 방지

| 실수 | 해결 |
|---|---|
| 텍스트 매칭으로 로그인 판별 (숨겨진 "로그아웃" 문자열에 오탐) | DOM 요소 존재로 판별 |
| URL에서 ID 추출 실패 (`/store/12345` 같은 rewrite URL) | `r"/(\d{5,})(?:[/?#]|$)"` 추가 |
| confirm() 때문에 자동화 블락 | `window.confirm = () => true` 선주입 |
| SITE_KEYS에만 추가하고 SITE_REGISTRY 누락 | `create_site("key")` 호출 시 KeyError |
| 백엔드 도메인 등록 빠짐 | 서버 동기화했을 때 안 보임 |
| 서버에는 "xxx"인데 코드는 "xxx_bam" (이름 불일치) | 먼저 `GET /v1/admin/platform-domains`로 기존 key 확인 |
| form.submit()시 CSRF 미포함 | `closest('form').submit()`으로 폼 전체 제출 |

---

## 8. 기존 구현 참고표

사용자의 요구사항과 유사한 기존 구현을 반드시 먼저 읽어볼 것:

| 요구사항 | 참고 사이트 | 주요 기법 |
|---|---|---|
| gnuboard 단순 점프 | `bamje`, `indal` | URL 이동 + alert |
| gnuboard 카운트 비교 | `busanbibigi` | before/after 파싱 |
| gnuboard JS 함수 | `obam` | `jump_shop(wr_id)` JS 호출 |
| XE 멀티 포스트 | `albam` (아이러브밤) | `jump_posts(urls)` + document_srl |
| XE 단일 점프 | `sexbam` | `doCallModuleAction` |
| Laravel 폼 제출 | `daegubam` (대구의밤) | CSRF `_token` + form.submit() |
| 자동 도메인 전환 방어 | 모든 사이트 | handlers.py에서 `_maybe_switch_announced_domain` 비활성 |

---

## 9. 전체 플로우 요약 (순서대로)

1. 사용자에게 6가지 정보 수집
2. HTML 기반 프레임워크 판별
3. 가장 유사한 기존 사이트 하나 골라 읽기
4. `jump_site_modules/{framework}/{name}/` 디렉토리 + `__init__.py` + `site.py` 생성
5. 3곳에 등록 (SITE_REGISTRY, framework __init__, sites.py)
6. `PUT /v1/admin/platform-domains`로 도메인 등록
7. Python import 테스트
8. 로컬 macOS 빌드 + 실행 + 드롭다운 확인
9. 커밋 + main push (PyInstaller CI 통과 확인)
10. 태그 푸시 (Nuitka 릴리즈, `release` skill 호출)

**각 단계를 건너뛰지 말 것**. 특히 8번 로컬 검증은 CI에서 30분 기다리기 전에 필수.
