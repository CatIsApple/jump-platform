---
name: release
description: jump-platform의 버전 릴리즈 전체 자동화. 버전 번호만 받으면 pyproject.toml + __init__.py 업데이트, 커밋, 태그 푸시, GitHub Actions로 Nuitka + Inno Setup 빌드 + R2 업로드 + D1 등록까지 진행. 릴리즈 후 클라이언트는 상단 파란 배너로 업데이트 알림을 받고, 설치 후 What's New 다이얼로그로 변경사항을 자동 표시. 사용 시점: 사용자가 "릴리즈", "배포", "업데이트 릴리즈", "버전 올려", "v0.x.x 배포", "배포 진행" 등을 요청할 때.
---

# Release Skill — jump-platform 자동 릴리즈

jump-platform 프로젝트의 전체 릴리즈 사이클을 자동화합니다.

## 전제 조건 (이미 설정됨)

- **GitHub 리포**: `CatIsApple/jump-platform` (private)
- **CF Worker**: `api.guardian01.online` (R2 binding + D1)
- **R2 버킷**: `jump-platform-releases` (private)
- **D1 DB**: `jump_backend_db` (id `ae50d38a-4435-42f7-8d55-05159dc1397f`)
- **GitHub Secrets** (설정 완료):
  - `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ENDPOINT`
  - `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET`
  - `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`
- **워크플로우**:
  - `.github/workflows/build-release-nuitka.yml` — tag push 시 Nuitka 빌드 + R2 + D1 자동 등록
  - `.github/workflows/build-worker-binaries.yml` — main push 시 빠른 PyInstaller 검증

## 클라이언트 자동 업데이트 흐름

릴리즈 태그 푸시 후 약 30분 내 모든 클라이언트:

1. 30분 주기 heartbeat로 세션 체크
2. `GET /v1/updates/latest?platform={windows|macos}` 폴링
3. 새 버전 감지 → 상단 파란 배너에 "✨ 새 버전 vX.Y.Z 사용 가능"
4. 사용자 "지금 업데이트" 클릭 → 다이얼로그에 `notes` 표시 → 확인
5. Worker 프록시로 R2 바이너리 스트리밍 다운로드 (Bearer 인증)
6. sha256 무결성 검증
7. **macOS**: `ditto -x -k`로 zip 해제 → 기존 .app 백업 → ditto 복사 → chmod → xattr -cr → lsregister -f → open 재시작
8. **Windows**: PowerShell 헬퍼가 부모 종료 대기 → setup.exe `/VERYSILENT /SUPPRESSMSGBOXES` → 설치 wait → 새 exe 자동 실행
9. 새 버전 첫 실행 시 1.5초 후 **"🎉 vX.Y.Z 업데이트 완료" What's New 다이얼로그** (DB에 저장된 notes 재표시, 한 번만)

## 워크플로우

### 1단계: 현재 버전 파악 + 다음 버전 결정

```bash
grep __version__ jump_worker_dashboard/__init__.py
grep ^version jump_worker_dashboard/pyproject.toml
```

**버전 증분 규칙**:
- **patch** (0.8.0 → 0.8.1): 버그 수정만
- **minor** (0.8.0 → 0.9.0): 새 사이트 추가, UI 변경, 기능 추가
- **major** (0.x → 1.0): 구조 변경, 호환성 깨짐

### 2단계: 버전 동기화

두 파일을 **동시에** 업데이트:

```
# jump_worker_dashboard/__init__.py
__version__ = "NEW_VERSION"

# jump_worker_dashboard/pyproject.toml
version = "NEW_VERSION"
```

### 3단계: 릴리즈 노트 작성 (유저에게 보여짐)

**⚠️ 중요**: `build-release-nuitka.yml`에서 `NOTES: ${{ github.event.head_commit.message }}`로 커밋 메시지를 notes로 사용. 커밋 메시지가 그대로 "What's New" 다이얼로그에 표시되므로 **유저 친화적으로 작성**해야 함.

좋은 notes 예시:
```
🎉 v1.0.0 정식 버전 출시!

새로 추가된 기능:
• 자동 업데이트 시스템 (업데이트 있을 때 알림 배너 표시)
• 대구의밤, 오밤 사이트 지원
• What's New 다이얼로그로 변경사항 안내

버그 수정:
• macOS 재시작 안정성 개선
• 서버 동기화 SSL 인증 오류 수정
• 로그아웃 후 자동 재로그인 방지

감사합니다! 🚀
```

### 4단계: 커밋 + 태그 푸시 (1줄로 릴리즈)

```bash
git add jump_worker_dashboard/__init__.py jump_worker_dashboard/pyproject.toml

# notes가 될 커밋 메시지 (유저 친화적)
git commit -m "$(cat <<'EOF'
🎉 vX.Y.Z 출시

새 기능:
• ...

버그 수정:
• ...
EOF
)"
git push origin main

git tag vX.Y.Z
git push origin vX.Y.Z
```

### 5단계: 빌드 모니터링

```bash
sleep 15
RUN_ID=$(gh api repos/CatIsApple/jump-platform/actions/runs --jq '[.workflow_runs[] | select(.head_branch == "vX.Y.Z")][0].id')
gh api repos/CatIsApple/jump-platform/actions/runs/$RUN_ID/jobs --jq '.jobs[] | "\(.name) | \(.conclusion // "running")"'
```

약 30~35분 소요 (Windows Nuitka). `run_in_background: true`로 polling.

### 6단계: 릴리즈 검증

```bash
# GitHub Release
gh api repos/CatIsApple/jump-platform/releases/latest --jq '.tag_name, .html_url'

# R2 + D1 등록 확인
curl -s -H "CF-Access-Client-Id: fc1aa72f3308d25496c24f5ba6e9eae3.access" \
     -H "CF-Access-Client-Secret: 0626ec1116c10885e4e88300885619cd9c0fd74b167b4ebc90767da201da6895" \
     https://api.guardian01.online/v1/admin/releases | \
  python3 -c "import sys, json; [print(r['version'], r['platform'], r['size']) for r in json.load(sys.stdin)['releases'][:4]]"

# 클라이언트 관점 (테스트 라이센스로 로그인 후)
curl -s -H "Authorization: Bearer TOKEN" "https://api.guardian01.online/v1/updates/latest?platform=macos"
```

## 로컬 빠른 테스트 (CI 30분 대기 피하기)

### 로컬 macOS 빌드 + R2 수동 업로드 + D1 등록

```bash
cd jump_worker_dashboard

# 1. 로컬 빌드
./scripts/build_macos.sh

# 2. 메타데이터 계산
ZIP=dist/jump-worker-dashboard-macos.zip
SIZE=$(stat -f%z "$ZIP")
SHA=$(shasum -a 256 "$ZIP" | awk '{print $1}')

# 3. R2 업로드
cd ../jump_backend
CLOUDFLARE_API_KEY="ae832649cb729e7decfc0cbb487f6171d9897" \
CLOUDFLARE_EMAIL="tiok0812@gmail.com" \
CLOUDFLARE_ACCOUNT_ID="9606dcd69b23133a781ed46f4588f94a" \
npx wrangler r2 object put \
  jump-platform-releases/releases/vX.Y.Z/jump-worker-dashboard-macos.zip \
  --file "../jump_worker_dashboard/$ZIP" --remote

# 4. D1 등록
NOTES="업데이트 내용..."
curl -s -X POST -H "CF-Access-Client-Id: fc1aa72f3308d25496c24f5ba6e9eae3.access" \
  -H "CF-Access-Client-Secret: 0626ec1116c10885e4e88300885619cd9c0fd74b167b4ebc90767da201da6895" \
  -H "Content-Type: application/json" \
  -d "$(jq -nc --arg v "X.Y.Z" --arg p "macos" --arg k "releases/vX.Y.Z/jump-worker-dashboard-macos.zip" \
              --arg f "jump-worker-dashboard-macos.zip" --argjson s $SIZE --arg h "$SHA" --arg n "$NOTES" \
              '{version:$v, platform:$p, r2_key:$k, filename:$f, size:$s, sha256:$h, notes:$n}')" \
  https://api.guardian01.online/v1/admin/releases
```

### 테스트 루프

- **구버전 앱 보관**: 빌드 후 `cp -R dist/jump-worker-dashboard.app dist_vA.B.C` 백업. **단순 `cp -R` 대신 반드시 `ditto`** (확장 속성/심볼릭 링크 보존).
  ```bash
  ditto dist/jump-worker-dashboard.app dist_vA.B.C
  ```
- **복원도 `ditto`**:
  ```bash
  rm -rf dist/jump-worker-dashboard.app
  ditto dist_vA.B.C dist/jump-worker-dashboard.app
  xattr -cr dist/jump-worker-dashboard.app
  ```

## 수동 수습 (CI R2 업로드 실패 시)

GitHub Actions의 `release` job이 R2 업로드에서 실패(예: multipart AccessDenied)할 때
**빌드된 아티팩트는 남아있으므로** 수동으로 R2 업로드 + D1 등록만 하면 됨.

### 증상
- `build-windows-nuitka`, `build-macos`: success
- `release`: failure (`Upload binaries to R2` 단계)
- GitHub Release는 생성되어 있음 (파일 첨부 포함)
- R2/D1에는 등록 안 됨 → 클라이언트가 업데이트 못 받음

### 수동 복구 절차

```bash
# 1. 아티팩트 다운로드
mkdir -p /tmp/vX.Y.Z_release && cd /tmp/vX.Y.Z_release
RUN_ID=$(gh api repos/CatIsApple/jump-platform/actions/runs \
  --jq '[.workflow_runs[] | select(.head_branch == "vX.Y.Z")][0].id')
gh run download $RUN_ID -R CatIsApple/jump-platform

# 2. 메타데이터 계산
WIN=/tmp/vX.Y.Z_release/jump-worker-dashboard-windows/GUARDIAN_Jump_Setup.exe
MAC=/tmp/vX.Y.Z_release/jump-worker-dashboard-macos/jump-worker-dashboard-macos.zip
WIN_SIZE=$(stat -f%z "$WIN") && WIN_SHA=$(shasum -a 256 "$WIN" | awk '{print $1}')
MAC_SIZE=$(stat -f%z "$MAC") && MAC_SHA=$(shasum -a 256 "$MAC" | awk '{print $1}')

# 3. R2 업로드 (wrangler — multipart 이슈 없음)
cd /Users/daon/Downloads/dist/jump_platform/jump_backend
export CLOUDFLARE_API_KEY="ae832649cb729e7decfc0cbb487f6171d9897"
export CLOUDFLARE_EMAIL="tiok0812@gmail.com"
export CLOUDFLARE_ACCOUNT_ID="9606dcd69b23133a781ed46f4588f94a"

npx wrangler r2 object put \
  jump-platform-releases/releases/vX.Y.Z/GUARDIAN_Jump_Setup.exe \
  --file "$WIN" --remote

npx wrangler r2 object put \
  jump-platform-releases/releases/vX.Y.Z/jump-worker-dashboard-macos.zip \
  --file "$MAC" --remote

# 4. D1 등록 (Windows + macOS 각각)
NOTES="유저용 changelog..."

for META in "windows::GUARDIAN_Jump_Setup.exe::$WIN_SIZE::$WIN_SHA" \
            "macos::jump-worker-dashboard-macos.zip::$MAC_SIZE::$MAC_SHA"; do
  IFS="::" read -r PLATFORM FILENAME SIZE SHA <<< "$META"
  curl -s -X POST \
    -H "CF-Access-Client-Id: fc1aa72f3308d25496c24f5ba6e9eae3.access" \
    -H "CF-Access-Client-Secret: 0626ec1116c10885e4e88300885619cd9c0fd74b167b4ebc90767da201da6895" \
    -H "Content-Type: application/json" \
    -d "$(jq -nc --arg v "X.Y.Z" --arg p "$PLATFORM" \
                 --arg k "releases/vX.Y.Z/$FILENAME" --arg f "$FILENAME" \
                 --argjson s $SIZE --arg h "$SHA" --arg n "$NOTES" \
                 '{version:$v, platform:$p, r2_key:$k, filename:$f, size:$s, sha256:$h, notes:$n}')" \
    https://api.guardian01.online/v1/admin/releases
  echo ""
done
```

### 근본 해결 (다음 릴리즈 전)

R2는 multipart upload 권한이 없는 토큰 사용 시 `CreateMultipartUpload` 실패.
`.github/workflows/build-release-nuitka.yml`의 `Setup AWS CLI for R2` 스텝에
다음 config 포함되어야 함 (이미 추가됨):

```yaml
- name: Setup AWS CLI for R2
  run: |
    if ! command -v aws &> /dev/null; then
      sudo apt-get install -y awscli
    fi
    aws --version
    # R2는 multipart upload 권한이 없으므로 single PUT으로 강제
    mkdir -p ~/.aws
    cat > ~/.aws/config <<'EOF'
    [default]
    s3 =
        multipart_threshold = 5GB
        multipart_chunksize = 5GB
    EOF
```

## 긴급 롤백

```bash
# D1에서 unpublish
curl -X POST -H "CF-Access-Client-Id: ..." -H "CF-Access-Client-Secret: ..." \
  https://api.guardian01.online/v1/admin/releases/{ID}/unpublish

# 태그 삭제 (GitHub)
git tag -d vX.Y.Z
git push origin :refs/tags/vX.Y.Z
gh release delete vX.Y.Z -R CatIsApple/jump-platform --yes

# R2 객체 삭제 (선택)
npx wrangler r2 object delete jump-platform-releases/releases/vX.Y.Z/GUARDIAN_Jump_Setup.exe --remote
```

## 빌드 파이프라인 주의사항

**PyInstaller 필수 옵션** (이미 `scripts/build_*`에 포함):
- `--collect-data=certifi` — SSL 인증서 번들 (필수! 없으면 HTTPS 실패)
- `--copy-metadata=certifi,requests,urllib3` — 메타데이터 누락 방지
- `--collect-submodules=selenium` — selenium 서브모듈
- `--collect-submodules=jump_site_modules` — 사이트 모듈

**Nuitka 옵션** (`build-release-nuitka.yml`):
- `--msvc=latest` (MinGW 대신)
- `--nofollow-import-to=trio,trio_websocket` — 컴파일 크래시 방지
- `--enable-plugin=tk-inter` — CustomTkinter

## 시크릿 값

> **⚠️ `.claude/secrets.local.md`에 모든 민감정보 (gitignore).**
> skill 실행 전 이 파일 먼저 읽기.

## 자주 하는 실수

1. **버전 불일치**: `__init__.py`와 `pyproject.toml` 둘 다 업데이트
2. **태그 누락**: `git push origin main`만 하면 Nuitka 빌드 안 됨, `git tag` + `git push origin TAG` 필수
3. **macOS .app 복사/복원 시 `cp -R` 사용**: 확장 속성 손실로 Mach-O 바이너리 손상. 반드시 `ditto` 사용.
4. **zip 해제 시 Python `zipfile`**: 심볼릭 링크 미지원. `ditto -x -k` 또는 `unzip` 사용 (이미 `updater.py`가 처리).
5. **커밋 메시지 = 유저에게 보이는 notes**: 기술 용어 아닌 유저 친화적으로 작성. `_sanitize_release_notes()` 가 `Co-Authored-By`, `heartbeat`, `D1`, `R2`, `Nuitka` 등 자동 필터.

## Windows 자동 업데이트 — 검증된 아키텍처 (v1.0.9+)

### 절대 지키기
1. **Nuitka `--onefile` 유지** (팀 합의). `--standalone` 전환 금지 (과거 문제 있었음).
2. **`--jobs=4` 유지** — GH runner(4 물리코어)에서 8로 올리면 **더 느려짐** (컨텍스트 스위칭 오버헤드).
3. **PowerShell 업데이트 헬퍼 금지** — Nuitka onefile Job Object 가 PowerShell grandchild 를 kill. `cmd.exe` + 배치파일만 사용.
4. **`CREATE_NO_WINDOW` + `DETACHED_PROCESS` 조합 금지** — 상호 배타, 조용히 실패. `CREATE_NEW_CONSOLE` + `STARTUPINFO.wShowWindow=0` 사용.

### install_and_restart_windows 3단계 폴백 (updater.py)
| Method | 플래그 | 동작 |
|---|---|---|
| 1 | `CREATE_BREAKAWAY_FROM_JOB` | 부모 Job Object 탈출 시도 (Nuitka onefile 대응) |
| 2 | flag 없음 | Job이 SILENT_BREAKAWAY_OK 인 경우 자동 탈출 |
| 3 | `schtasks.exe` | Task Scheduler 서비스가 부모 — 100% 독립 실행 |

### Inno Setup 필수 설정 (guardian-setup.iss)
```ini
PrivilegesRequired=admin            ; UAC 내부 상승 (정상 동작)
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll ; 없으면 /CLOSEAPPLICATIONS 무효
RestartApplications=no
```

### 진단 로그 (모두 `%LOCALAPPDATA%\jump_worker_dashboard\`)
- `update_py.log` — Python 쪽 단계
- `update.log` — cmd.exe 배치 실행 단계
- `update.bat` — 실제 실행된 배치 사본
- `inno_setup.log` — Inno Setup 자체 로그

## 업데이트 흐름 테스트 (빌드 없이)

```powershell
# Windows에서
cd <repo>
python jump_worker_dashboard\scripts\test_update_flow.py
# 또는 demo 릴리즈 등록 후 실제 앱에서 업데이트 버튼 클릭
```

Demo 릴리즈 등록 (테스트용, 기존 v1.0.9 setup exe 재사용):
```bash
curl -s -X POST "https://api.guardian01.online/v1/admin/releases" \
  -H "CF-Access-Client-Id: <CLIENT_ID>" -H "CF-Access-Client-Secret: <CLIENT_SECRET>" \
  -H "Content-Type: application/json" \
  -d '{
    "version":"1.0.99","platform":"windows",
    "r2_key":"releases/v1.0.9/GUARDIAN_Jump_Setup.exe",
    "filename":"GUARDIAN_Jump_Setup.exe",
    "size":<SIZE>,"sha256":"<SHA256>"
  }'
# 테스트 완료 후: POST /v1/admin/releases/{id}/unpublish
```

## User-Agent 형식

클라이언트는 실행 플랫폼을 반영한 UA 전송 (v1.1.0+):
```
Mozilla/5.0 (Windows NT 10.0; AMD64) jump-worker-dashboard/1.1.0 AppleWebKit/... Chrome/...
Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) jump-worker-dashboard/1.1.0 AppleWebKit/...
```
관리자 UI가 `jump-worker-dashboard/{ver}` 토큰을 파싱해서 `Win/jump v1.1.0` / `macOS/jump v1.1.0` 로 표시.

