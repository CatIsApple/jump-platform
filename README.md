# jump-platform

업소 자동 점프 플랫폼 모노레포.

## 구성

| 패키지 | 역할 | 스택 |
|---|---|---|
| `jump_worker_dashboard` | 사용자 워커 GUI 앱 (Windows/macOS 배포) | Python 3.10+, CustomTkinter, Selenium |
| `jump_site_modules` | 사이트별 자동화 모듈 | Python (프레임워크별 분리) |
| `jump_backend` | Cloudflare Worker + D1 + R2 백엔드 | TypeScript, Wrangler |
| `jump_admin_tui` | 관리자 TUI | Textual |
| `jump_admin_web` | 관리자 웹 대시보드 (별도 레포) | FastAPI + Tailwind |

## 지원 사이트 (현재)

- 대구의밤 (eorn3.com) · 오밤 (obam37.com) · 부산비비기 (busanb37.net)
- 아이러브밤 · 인천달리기 (indal666.com)
- opguide · opmania · bamminjok · kakaotteok · bamje · opmart

신규 사이트 추가 시 `/claude:add-site` skill 사용.

## 핵심 기능

### 1. 자동 업데이트 (v1.0.0+, v1.1.0 검증 완료)

- 앱 실행 시 서버에 최신 버전 조회
- 새 버전 감지 시 상단 파란색 배너 알림
- 원클릭 다운로드 → sha256 검증 → 자동 설치 → 재시작
- **Windows**: `cmd.exe` 배치 + 3단계 폴백 (`CREATE_BREAKAWAY_FROM_JOB` → 일반 Popen → `schtasks`)
  - Nuitka onefile의 Job Object 문제 회피
  - UAC 프롬프트 + Inno Setup `/VERYSILENT` 자동 설치
- **macOS**: `ditto` 기반 .app 교체 (확장 속성 보존)
- 진단 로그: `%LOCALAPPDATA%\jump_worker_dashboard\update*.log` / `~/jump_worker_dashboard/data/update.log`

### 2. 라이센스 + 세션 보안

- 라이센스 키 기반 인증 (sha256 + pepper 해싱)
- 30초 heartbeat 으로 세션 유효성 확인
- 동일 디바이스 재로그인 시 이전 세션 자동 폐기
- 관리자 세션 폐기 시 30초 이내 자동 로그아웃
- stale 세션 (heartbeat 끊긴 지 180초+) UI 구분 표시
- IP + 국가(CF-IPCountry) + User-Agent 캡처로 모니터링

### 3. 관리자 통합 세션 관리 (v1.0.9+)

- 웹 대시보드 "세션" 탭: 모든 라이센스의 활성 세션 한 화면
- 컬럼: ID / 업체 / 상태 뱃지 / 디바이스 / IP / 국가 / 브라우저(UA 요약) / 최근접속
- 필터: active / stale / revoked / all + 업체·디바이스·IP 검색
- 세션 종료 · 30분+ stale 세션 일괄 정리 버튼

### 4. 플랫폼 도메인 관리

- gnuboard/xe/custom 프레임워크별 사이트 모듈
- 도메인만 바꾸면 기존 모듈 재사용 (도메인 DB 관리)

## 빠른 시작

### 1) 워커 GUI (로컬 개발)

```bash
cd jump_worker_dashboard
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
python3 main.py
```

### 2) 관리자 TUI

```bash
cd jump_admin_tui
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
jump-admin-tui
# 단축키: 1=라이센스, 2=도메인, 3=세션, k=세션종료, u=오래된세션정리, f=필터
```

### 3) 백엔드 (Cloudflare Worker)

```bash
cd jump_backend
npm install
CLOUDFLARE_API_KEY=... CLOUDFLARE_EMAIL=... CLOUDFLARE_ACCOUNT_ID=... \
  npx wrangler deploy
```

### 4) 업데이트 흐름 테스트 (빌드 없이)

Windows에서 실제 업데이트 흐름만 검증:

```powershell
cd <repo>
python jump_worker_dashboard\scripts\test_update_flow.py
```

## 배포

- `git tag vX.Y.Z && git push origin vX.Y.Z` → Actions `Build Release (Nuitka + Inno Setup)` 자동 트리거
- Nuitka Windows (~30분) + PyInstaller macOS (~5분) + Inno Setup 인스톨러
- CI가 R2 업로드 + D1 `releases` 테이블에 자동 등록
- 기존 유저는 앱 실행 시 자동 업데이트 알림

상세: [.claude/skills/release/SKILL.md](.claude/skills/release/SKILL.md)

## Claude Code Skills

- **`/release`** — 버전 릴리즈 자동화 (버전 bump, 태그, CI 모니터링, 장애 수습)
- **`/add-site`** — 신규 사이트 추가 자동화 (모듈 생성 + 3곳 등록 + 빌드/등록)

## 백엔드 엔드포인트 요약

### 사용자
- `POST /v1/auth/login` · `POST /v1/auth/logout` · `GET /v1/auth/heartbeat`
- `GET /v1/platform-domains`
- `GET /v1/updates/latest` · `GET /v1/updates/download/{id}`

### 관리자 (CF Access 또는 X-Admin-Token)
- 라이센스: `GET/POST /v1/admin/licenses`, `POST /v1/admin/licenses/{id}/(extend|suspend|resume|revoke)`
- 세션: `GET /v1/admin/sessions?status=active|stale|revoked`, `GET /v1/admin/licenses/{id}/sessions`
- 세션 관리: `POST /v1/admin/sessions/{id}/revoke`, `POST /v1/admin/sessions/cleanup-stale?min_age_seconds=1800`
- 도메인: `GET/PUT/PATCH/DELETE /v1/admin/platform-domains`
- 릴리즈: `GET/POST /v1/admin/releases`, `POST /v1/admin/releases/{id}/(publish|unpublish)`

### CI 전용
- `POST /v1/ci/releases` — 빌드 파이프라인에서 X-Admin-Token 헤더로 등록

## 문서

- [CLAUDE.md](./CLAUDE.md) — Claude Code 프로젝트 지침
- [.claude/skills/release/SKILL.md](.claude/skills/release/SKILL.md) — 릴리즈 자동화 skill
- [.claude/skills/add-site/SKILL.md](.claude/skills/add-site/SKILL.md) — 사이트 추가 자동화 skill
