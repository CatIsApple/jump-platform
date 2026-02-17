# jump-worker-dashboard

백엔드 라이센스 로그인 + 플랫폼 도메인 동기화를 포함한 운영용 GUI 워커 앱입니다.

## 1) 로컬 실행

```bash
cd /Users/daon/Downloads/dist/jump_worker_dashboard
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python3 main.py
```

## 2) 설정(프로덕션)

앱 설정에서 아래 값을 입력합니다.

- 백엔드 URL: `https://api.guardian01.online`
- 라이센스 키: `JUMP-...`
- 디바이스 ID: 자동 생성값 사용 가능
- 2Captcha API 키

그 후 순서:

1. `라이센스 로그인`
2. `도메인 동기화`
3. 작업 저장/실행

## 3) 백엔드 연동 스모크 테스트

앱 DB의 설정값(`backend_base_url`, `backend_license_key`)을 그대로 사용해 로그인/도메인 조회/로그아웃을 점검합니다.

```bash
cd /Users/daon/Downloads/dist/jump_worker_dashboard
python3 scripts/backend_smoke.py
```

직접 값 지정:

```bash
python3 scripts/backend_smoke.py \
  --base-url https://api.guardian01.online \
  --license-key 'JUMP-...'
```

## 4) macOS 패키징

```bash
cd /Users/daon/Downloads/dist/jump_worker_dashboard
./scripts/build_macos.sh
```

산출물:
- `dist/jump-worker-dashboard.app`
- `dist/jump-worker-dashboard-macos.zip`

## 5) Windows 패키징 (Windows에서 실행)

```powershell
cd C:\path\to\jump_worker_dashboard
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
```

산출물:
- `dist\jump-worker-dashboard\`

## 6) 데이터 경로

- 소스 실행: `jump_worker_dashboard/data`
- 실행파일(frozen) 실행: `~/jump_worker_dashboard/data`
- 환경변수 `JUMP_WORKER_DATA_DIR`가 있으면 해당 경로 우선 사용

