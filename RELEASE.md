# Release Guide

## GitHub Secrets

Repository Settings -> Secrets and variables -> Actions에 아래를 추가합니다.

- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`

(선택) 코드 서명 자동화를 추가할 경우:

- Windows 코드서명 인증서/비밀번호
- Apple notarization 관련 키체인 시크릿

## Worker Desktop Binary Release

1. 버전을 올리고 커밋
2. 태그 생성

```bash
git tag v0.1.0
git push origin v0.1.0
```

3. `Build Worker Binaries` 워크플로우가 Win/macOS 빌드 후 Release 생성

## Backend Deploy

- `main` 브랜치에 `jump_backend` 변경이 푸시되면 `Deploy Backend Worker`가 자동 배포됩니다.
- 수동 실행은 Actions에서 `workflow_dispatch` 사용.
