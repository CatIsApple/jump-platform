# Jump Platform — Claude Context

jump-platform monorepo의 Claude Code용 프로젝트 지침.

## 프로젝트 개요

3개 모듈로 구성:
- **jump_backend** — Cloudflare Worker + D1 + R2 (백엔드)
- **jump_worker_dashboard** — CustomTkinter GUI 클라이언트 (Windows/macOS 배포)
- **jump_admin_tui** — 관리자 TUI (Textual)

대상 도메인: 업소 자동 점프 (한국어 웹사이트들) — gnuboard, XE, Laravel 등 다양한 프레임워크.

## Claude Skills (중요!)

프로젝트 특화 skill 2개 설치됨:
- **`release`** — 버전 릴리즈 자동화 (`/Users/daon/Downloads/dist/jump_platform/.claude/skills/release/SKILL.md`)
- **`add-site`** — 새 사이트 추가 자동화 (`/Users/daon/Downloads/dist/jump_platform/.claude/skills/add-site/SKILL.md`)

사용자가 "릴리즈", "배포", "사이트 추가" 등을 요청하면 해당 skill을 활용.

## 시크릿 참조

`.claude/secrets.local.md` (gitignore)에 모든 민감정보 저장:
- Cloudflare API 자격증명 (Account ID, Global API Key, R2 S3 keys)
- CF Access Client ID/Secret (Admin API 호출)
- D1/R2 바인딩 정보
- 자주 쓰는 명령 스니펫

**필요할 때 `.claude/secrets.local.md` 를 직접 읽어서 사용**.

## 코드 규약

1. **사이트 모듈**: `jump_site_modules/{framework}/{name}/site.py`
   - 프레임워크: `gnuboard` / `xe` / `custom` (Laravel 등)
   - 반드시 기존 유사 구현 참고 (예: gnuboard면 `busanbibigi` 또는 `obam`)

2. **등록 3곳** (빼먹지 말 것):
   - `jump_site_modules/__init__.py` SITE_REGISTRY + import
   - `jump_site_modules/{framework}/__init__.py` export
   - `jump_worker_dashboard/app/sites.py` SITE_KEYS + BROWSER_REQUIRED_SITES

3. **버전**: `jump_worker_dashboard/__init__.py` + `pyproject.toml` 반드시 동기화.

4. **커밋 메시지**: Co-Authored-By 포함
   ```
   Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
   ```

## 빌드 파이프라인

| 트리거 | 워크플로우 | 용도 |
|---|---|---|
| main push | `build-worker-binaries.yml` (PyInstaller, ~5분) | 빠른 CI 검증, 릴리즈 안 함 |
| tag `v*` push | `build-release-nuitka.yml` (Nuitka, ~30분) | 정식 릴리즈, R2+D1 자동 등록 |

## 백엔드 엔드포인트 (api.guardian01.online)

### 사용자 API
- `POST /v1/auth/login` — 라이센스 로그인
- `GET /v1/auth/heartbeat` — 30초 주기 세션 체크 (자동 로그아웃 트리거)
- `GET /v1/platform-domains` — 활성 사이트 목록
- `GET /v1/updates/latest` — 최신 버전 정보
- `GET /v1/updates/download/{id}` — R2 프록시 다운로드

### 관리자 API (CF Access 헤더)
- `POST /v1/admin/licenses` 등 라이센스 관리
- `POST /v1/admin/sessions/{id}/revoke` 세션 강제 폐기
- `PUT/PATCH /v1/admin/platform-domains` 도메인 관리
- `POST /v1/admin/releases` 릴리즈 등록

## 보안 원칙

1. **소스 비노출**: GitHub private, 클라이언트에 GitHub URL 절대 노출 없음
2. **R2 private**: Worker R2 binding으로만 접근, presigned URL 사용 안 함 (Worker 프록시)
3. **라이센스 바인딩**: 모든 업데이트는 라이센스 인증 통해서만
4. **무결성**: sha256 검증, Inno Setup + Nuitka + XOR 난독화
5. **세션 통제**: 관리자가 언제든 세션 폐기 → 30초 내 클라이언트 자동 로그아웃

## 테스트 규칙

1. **로컬 macOS 빌드 먼저** — `scripts/build_macos.sh` (5분)
2. CI Nuitka 빌드는 태그 푸시 후 30분 소요
3. 실제 업데이트 흐름 테스트 시: `dist_v0.X.X_backup/` 디렉토리 활용

## Known Issues & Gotchas

- **Boost 1.90**: `boost_system`이 header-only로 바뀜, wrangler/cmake 빌드 시 cmake 파일 없음
- **Nuitka + trio**: `trio_websocket` 컴파일 크래시 → `--nofollow-import-to=trio` 필수
- **IPv6 + CF API**: `cfat_` 토큰은 IPv6에서 차단됨, Global API Key 사용
- **Inno Setup 6.7.0**: innoextract 1.9/HEAD도 미지원 (Ubuntu/macOS)
- **macOS Gatekeeper**: 빌드 후 `xattr -cr app.app` 필요
- **세션 폐기**: license_key까지 지워야 자동 재로그인 방지

## 개발 워크플로우 요약

1. 새 사이트 추가 요청 → `add-site` skill 실행
2. 로컬 macOS 빌드 + 테스트
3. 커밋 (main push → CI 빠른 검증)
4. 릴리즈 시점 → `release` skill (vX.Y.Z 태그 푸시)
5. 30분 대기 (Nuitka + R2 업로드 + D1 등록)
6. 모든 클라이언트가 자동 업데이트 수신
