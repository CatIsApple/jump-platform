---
name: release
description: jump-platform의 버전 릴리즈 전체 자동화. 버전 번호만 받으면 pyproject.toml + __init__.py 업데이트, 커밋, 태그 푸시, GitHub Actions로 Nuitka + Inno Setup 빌드 + R2 업로드 + D1 등록까지 진행. 릴리즈 후 클라이언트는 자동 업데이트 알림을 받음. 사용 시점: 사용자가 "릴리즈", "배포", "업데이트 릴리즈", "버전 올려", "v0.x.x 배포", "배포 진행" 등을 요청할 때.
---

# Release Skill — jump-platform 자동 릴리즈

jump-platform 프로젝트의 전체 릴리즈 사이클을 자동화합니다.

## 전제 조건 (이미 설정되어 있음)

- **GitHub 리포**: `CatIsApple/jump-platform` (private)
- **CF Worker**: `api.guardian01.online` (R2 binding + D1)
- **R2 버킷**: `jump-platform-releases`
- **GitHub Secrets** (설정 완료):
  - `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ENDPOINT`
  - `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET`
  - `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`
- **워크플로우**:
  - `.github/workflows/build-release-nuitka.yml` — tag push 시 실행
  - `.github/workflows/build-worker-binaries.yml` — main push 시 빠른 검증

## 워크플로우

### 1단계: 현재 버전 파악 + 다음 버전 결정

```bash
# 현재 버전 확인 (두 곳 동기화되어 있어야 함)
grep __version__ jump_worker_dashboard/__init__.py
grep ^version jump_worker_dashboard/pyproject.toml
```

**버전 증분 규칙**:
- **patch** (0.8.0 → 0.8.1): 버그 수정만
- **minor** (0.8.0 → 0.9.0): 새 사이트 추가, 기능 추가, UI 변경
- **major** (0.8.0 → 1.0.0): 구조적 변경, 호환성 깨짐

사용자가 버전을 명시하지 않으면 변경 내용 기반으로 제안.

### 2단계: 버전 동기화

두 파일을 **동시에** 업데이트:

```python
# jump_worker_dashboard/__init__.py
__version__ = "NEW_VERSION"

# jump_worker_dashboard/pyproject.toml
version = "NEW_VERSION"
```

### 3단계: 변경사항 요약 (git diff)

```bash
git diff HEAD --stat
git log --oneline main..HEAD  # 커밋 있으면
```

변경 내용을 요약해서 커밋 메시지 작성. 형식:

```
chore: bump version to vX.Y.Z

Changes since last release:
- feat(sites): added 대구의밤 (eorn3.com)
- fix(ui): logout button colors
- feat(updater): auto-update system

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
```

### 4단계: 커밋 + 태그 푸시 (1줄로 릴리즈)

```bash
git add jump_worker_dashboard/__init__.py jump_worker_dashboard/pyproject.toml
git commit -m "$(cat <<'EOF'
chore: bump version to vX.Y.Z
...
EOF
)"
git push origin main

git tag vX.Y.Z
git push origin vX.Y.Z
```

태그 푸시 시 GitHub Actions 자동 실행:
1. **build-macos** (PyInstaller, ~5분) — `jump-worker-dashboard-macos.zip`
2. **build-windows-nuitka** (Nuitka + Inno Setup, ~30분) — `GUARDIAN_Jump_Setup.exe`
3. **release** (Ubuntu):
   - GitHub Release 생성 + 자산 첨부
   - `aws s3 cp` → R2 업로드 (`releases/vX.Y.Z/`)
   - `POST /v1/admin/releases` → D1 메타데이터 등록

### 5단계: 빌드 모니터링

```bash
sleep 15
RUN_ID=$(gh api repos/CatIsApple/jump-platform/actions/runs --jq '[.workflow_runs[] | select(.head_branch == "vX.Y.Z")][0].id')
gh api repos/CatIsApple/jump-platform/actions/runs/$RUN_ID/jobs --jq '.jobs[] | "\(.name) | \(.conclusion // "running")"'
```

Windows Nuitka가 가장 오래 걸리므로 약 30~35분 기다려야 함. `run_in_background: true`로 polling 추천.

### 6단계: 릴리즈 검증

빌드 완료 후:

```bash
# GitHub Release 확인
gh api repos/CatIsApple/jump-platform/releases/latest --jq '.tag_name, .html_url'

# R2 업로드 확인
curl -s -H "CF-Access-Client-Id: ..." -H "CF-Access-Client-Secret: ..." \
  https://api.guardian01.online/v1/admin/releases | \
  python3 -c "import sys, json; [print(r['version'], r['platform'], r['size']) for r in json.load(sys.stdin)['releases'][:4]]"

# 클라이언트 관점 테스트 (라이센스 로그인 후)
curl -s -H "Authorization: Bearer TOKEN" https://api.guardian01.online/v1/updates/latest?platform=macos
```

## 긴급 롤백

잘못된 릴리즈를 배포 중지해야 할 때:

```bash
# D1에서 unpublish (서버 측)
curl -X POST -H "CF-Access-Client-Id: ..." -H "CF-Access-Client-Secret: ..." \
  https://api.guardian01.online/v1/admin/releases/{ID}/unpublish

# 또는 태그 삭제 (GitHub)
git tag -d vX.Y.Z
git push origin :refs/tags/vX.Y.Z
gh release delete vX.Y.Z -R CatIsApple/jump-platform --yes
```

## 빠른 재시도 (빌드 실패 시)

같은 태그에 재시도:

```bash
# 코드 수정 후
git tag -d vX.Y.Z && git push origin :refs/tags/vX.Y.Z
git commit --amend   # 또는 새 커밋
git push origin main
git tag vX.Y.Z && git push origin vX.Y.Z
```

## 시크릿 값

> **⚠️ 모든 민감정보는 `.claude/secrets.local.md` 파일에 저장되어 있음 (gitignore).**
> skill 실행 전 이 파일을 먼저 읽어 필요한 값을 참조.

포함된 내용:
- Cloudflare Account ID, Global API Key, R2 Access Keys, S3 endpoint
- CF Access Client ID/Secret (관리자 API 호출용)
- D1 Database ID, R2 Bucket 정보
- 자주 쓰는 명령어 스니펫 (wrangler deploy, D1 execute, R2 upload, admin API curl)

추가 참조:
- `~/.config/jump_admin_tui/config.json` — Admin TUI/Web이 사용하는 설정
- `~/.wrangler/config` — wrangler CLI 세션 캐시
- GitHub Secrets — CI 빌드 시 자동 주입 (R2/CF Access)

## 자주 하는 실수

1. **버전 불일치**: `__init__.py`와 `pyproject.toml` 둘 다 업데이트 해야 함
2. **태그 누락**: `git push origin main`만 하면 Nuitka 빌드 안 됨, `git tag` + `git push origin TAG` 필수
3. **Inno Setup 실패**: `.iss` 수정 후에는 로컬 검증 없이 CI만 돌리면 발견 늦음
4. **R2 업로드 실패**: GitHub Secrets 만료/변경 시 Actions 로그에서 "403 Forbidden" 확인
5. **같은 버전 재등록**: `ON CONFLICT DO UPDATE`라 덮어써짐 (의도치 않게 덮어쓰지 않도록 주의)

## 테스트 목적 로컬 릴리즈 (실제 사용자에게 영향 없음)

개발 중 업데이트 흐름을 테스트하려면:

```bash
# 1. 로컬 macOS 빌드
cd jump_worker_dashboard && ./scripts/build_macos.sh

# 2. 로컬에서 R2 업로드
npx wrangler r2 object put jump-platform-releases/releases/vTEST/... \
  --file dist/jump-worker-dashboard-macos.zip --remote

# 3. D1 등록 (size, sha256 계산 후)
SIZE=$(stat -f%z dist/jump-worker-dashboard-macos.zip)
SHA=$(shasum -a 256 dist/jump-worker-dashboard-macos.zip | awk '{print $1}')
curl -X POST -d "{\"version\":\"TEST\",\"platform\":\"macos\",\"r2_key\":\"...\",...}" \
  https://api.guardian01.online/v1/admin/releases
```

테스트 후 `POST /v1/admin/releases/{id}/unpublish`로 정리.
