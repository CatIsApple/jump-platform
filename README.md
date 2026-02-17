# jump-platform

모노레포 구성:

- `jump_worker_dashboard`: 사용자 워커 GUI 앱 (Windows/macOS 배포 대상)
- `jump_backend`: Cloudflare Workers + D1 백엔드 (라이센스/도메인)
- `jump_admin_tui`: 관리자용 TUI

## 빠른 시작

### 1) 워커 GUI (로컬)

```bash
cd jump_worker_dashboard
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python3 main.py
```

### 2) 관리자 TUI

```bash
cd jump_admin_tui
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
jump-admin-tui
```

### 3) 백엔드

```bash
cd jump_backend
npm install
npx wrangler deploy
```

## 배포

- 태그(`v*`) 푸시 시 GitHub Actions가 Win/macOS 워커 빌드를 수행하고 Release에 아티팩트를 첨부합니다.
- 수동 실행은 Actions의 `Build Worker Binaries` 워크플로우에서 `workflow_dispatch`를 사용합니다.
