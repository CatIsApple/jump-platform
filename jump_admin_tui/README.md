# jump-admin-tui (Textual)

화살표/엔터 기반의 관리자용 TUI(터미널 대시보드)입니다.

Cloudflare Access(Service Token)으로 보호된 `jump-backend`의 `/v1/admin/*` API를 호출해
라이센스/플랫폼 도메인을 관리합니다.

## 1) 설치

```bash
cd /Users/daon/Downloads/dist/jump_admin_tui
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## 2) 설정

첫 실행 시 설정 화면에서 아래 값을 입력하면 로컬에 저장됩니다.

- API 기본 URL: `https://api.<도메인>`
- Access Client ID
- Access Client Secret
- (선택) X-Admin-Token (서버에서 ADMIN_TOKEN을 설정한 경우)

## 3) 실행

```bash
jump-admin-tui
```

## 키 가이드(기본)

- `r`: 새로고침
- `n`: 라이센스 생성
- `e`: (선택 항목) 기간 연장/도메인 수정
- `s`: 라이센스 정지/해제 토글
- `x`: 라이센스 폐기(revoke)
- `d`: 도메인 삭제
- `q`: 종료

