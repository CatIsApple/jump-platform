# jump-backend (Cloudflare Workers + D1)

라이센스 키 기반 로그인 + 플랫폼 도메인 제공 API.

관리자 API는 `/v1/admin/*` 경로로 분리되어 있고, Cloudflare Access(Service Token)로 잠그는 구성을 권장합니다.

## 1) 준비

- Cloudflare에 도메인이 연결되어 `Active` 상태
- `api.<도메인>` DNS 레코드가 생성되어 있고, **주황 구름(Proxied)** ON
- 로컬에 Node.js 설치
  - (선택) 아래 스크립트로 한 번에 배포 가능: `scripts/bootstrap.sh`

## 2) Wrangler 설치/로그인

```bash
npm i -g wrangler
wrangler login
wrangler whoami
```

## (추천) 원클릭 부트스트랩

아래 스크립트는 다음을 자동으로 처리합니다.

- `npm i`
- `wrangler login` (필요 시)
- `wrangler d1 create` 후 `wrangler.toml`의 `database_id` 자동 채움
- D1 마이그레이션 적용
- `LICENSE_PEPPER` Secret 자동 생성/설정
- 배포

```bash
cd /Users/daon/Downloads/dist/jump_backend
./scripts/bootstrap.sh
```

## 3) D1 생성 + 바인딩

```bash
wrangler d1 create jump_backend_db
```

출력되는 `database_id`를 `wrangler.toml`의 `database_id`에 넣습니다.

## 4) 마이그레이션 적용

이 레포는 `migrations/0001_init.sql`을 포함합니다.

```bash
wrangler d1 migrations apply jump_backend_db --remote
```

## 5) Secret 설정

라이센스/세션 해시용 Pepper는 반드시 Secret로 넣습니다.

```bash
wrangler secret put LICENSE_PEPPER
```

(선택) 관리자 이중 보호용 토큰:

```bash
wrangler secret put ADMIN_TOKEN
```

## 6) 로컬 개발 실행

원격 D1을 붙여서 테스트(권장):

```bash
wrangler dev --remote
```

## 7) 배포 + 커스텀 도메인 라우팅

`wrangler.toml`의 `routes`를 채운 뒤:

```toml
routes = [
  { pattern = "api.YOUR_DOMAIN/*", zone_name = "YOUR_DOMAIN" }
]
```

배포:

```bash
wrangler deploy
```

## 8) Cloudflare Access로 관리자 API 보호(권장)

Zero Trust > Access > Applications에서 Self-hosted 앱을 생성:

- Domain: `api.<도메인>`
- Path: `/v1/admin/*`
- Policy: **Service Auth** (Service Token 기반)

Service Token 생성 후, 관리자 호출 시 아래 헤더를 포함합니다:

- `CF-Access-Client-Id: ...`
- `CF-Access-Client-Secret: ...`

## 9) 운영 스모크 테스트

운영 URL/Access/라이센스 키 기준으로 엔드포인트를 한 번에 점검합니다.

```bash
cd /Users/daon/Downloads/dist/jump_backend
python3 scripts/prod_smoke.py \
  --base-url https://api.guardian01.online \
  --license-key 'JUMP-...' \
  --device-id 'prod-smoke' \
  --access-id '...access' \
  --access-secret '...'
```

옵션:
- `--skip-auth`: 로그인/도메인/로그아웃 체크를 건너뛰고 health만 점검

## 10) 로컬 JSON → 백엔드 도메인 동기화

`jump_worker_dashboard/data/platform_domains.json` 내용을 관리자 API로 일괄 반영합니다.

```bash
cd /Users/daon/Downloads/dist/jump_backend
python3 scripts/sync_domains_from_json.py \
  --base-url https://api.guardian01.online \
  --access-id '...access' \
  --access-secret '...'
```

## 11) API 요약

### 인증

- `POST /v1/auth/login`
  - body: `{ "license_key": "...", "device_id": "..." }`
  - resp: `{ "token": "...", "license": {...} }`
- `POST /v1/auth/logout`
  - header: `Authorization: Bearer <token>`

### 플랫폼 도메인

- `GET /v1/platform-domains`
  - header: `Authorization: Bearer <token>`

### 관리자(Access로 보호 권장)

- `GET /v1/admin/licenses`
- `POST /v1/admin/licenses` (생성, one-time license_key 반환)
- `POST /v1/admin/licenses/:id/extend`
- `POST /v1/admin/licenses/:id/suspend|resume|revoke`
- `GET /v1/admin/platform-domains`
- `PUT /v1/admin/platform-domains` (site_key/domain 설정)
- `DELETE /v1/admin/platform-domains?site_key=...` (삭제)
