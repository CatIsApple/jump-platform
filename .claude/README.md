# Claude Skills for Jump Platform

이 프로젝트는 **Claude Code에 특화된 커스텀 skills**를 포함합니다.

## 설치된 Skills

| Skill | 트리거 | 설명 |
|---|---|---|
| **release** | "릴리즈", "배포", "버전 올려", "vX.Y.Z 배포" | 버전 동기화 + 커밋 + 태그 푸시 + CI 빌드 + R2 업로드 + D1 등록 전체 자동화 |
| **add-site** | "사이트 추가", "xxx.com 추가해줘", "새 사이트" | 프레임워크 자동 판별 + 모듈 생성 + 레지스트리 등록 + 백엔드 도메인 등록 + 로컬 검증 |

## 시크릿 관리

모든 민감정보(Cloudflare API, R2 키, CF Access, GitHub 등)는 `.claude/secrets.local.md`에 저장.
이 파일은 `.gitignore`로 제외되며 **절대 git에 commit 금지**.

## Claude 사용 시 팁

- "릴리즈 진행해줘" → release skill 자동 활성화
- "부산비비기 사이트 추가해" + HTML/URL 정보 → add-site skill 자동 활성화
- Claude는 skill 실행 전 `.claude/secrets.local.md`를 자동으로 읽어 필요한 값 활용

## 디렉토리 구조

```
.claude/
├── README.md                       # 이 파일
├── secrets.local.md                # 시크릿 (gitignore)
└── skills/
    ├── release/
    │   └── SKILL.md                # 릴리즈 자동화
    └── add-site/
        └── SKILL.md                # 사이트 추가 자동화
```

## 프로젝트 핵심 인프라 (요약)

```
┌─────────────────┐     ┌───────────────────┐     ┌──────────────────┐
│ GitHub Actions  │     │ CF Worker         │     │ R2 Bucket        │
│ tag push v*     │────▶│ api.guardian01... │◀────│ jump-platform-   │
│ → build+upload  │     │ /v1/* endpoints   │     │    releases      │
└─────────────────┘     └───────┬───────────┘     └──────────────────┘
                                │
                          ┌─────▼──────┐
                          │ D1 DB      │
                          │ licenses,  │
                          │ sessions,  │
                          │ releases,  │
                          │ domains    │
                          └────────────┘
                                ▲
                                │  Bearer license token
                                │
                ┌───────────────┴──────────────┐
                │ Clients (Windows .exe / .app) │
                │  + updater.py (auto-update)   │
                │  + heartbeat (session check)  │
                └───────────────────────────────┘
```

## 전체 흐름 (사이트 추가 → 배포 → 사용자 업데이트)

1. 사용자 요청: "부산비비기 사이트 추가해줘" (+ HTML)
2. Claude: `add-site` skill 실행
   - 모듈 생성 → 레지스트리 등록 → 백엔드 도메인 등록 → 로컬 빌드 검증 → 커밋
3. 사용자 요청: "v0.9.0 릴리즈"
4. Claude: `release` skill 실행
   - 버전 동기화 → 커밋 → 태그 푸시
5. GitHub Actions 자동 (~30분):
   - Nuitka Windows 빌드 + Inno Setup
   - PyInstaller macOS 빌드
   - GitHub Release 생성
   - R2에 업로드
   - D1에 메타데이터 등록
6. 모든 클라이언트 (30분 이내):
   - heartbeat로 세션 확인
   - `/v1/updates/latest`로 새 버전 감지
   - 사이드바에 "업데이트 가능 v0.9.0" 배지 표시
   - 사용자 클릭 → 다운로드 → 설치 → 재시작

## 문제 해결

### skill이 활성화되지 않을 때
`.claude/skills/*/SKILL.md`의 `description`에 적절한 트리거 단어가 있는지 확인.

### 시크릿이 업데이트됐을 때
`.claude/secrets.local.md` 직접 수정. GitHub Secrets는 `gh secret set` 사용.

### 새 프레임워크 지원
`add-site/SKILL.md`의 "프레임워크 판별" 섹션에 패턴 추가.
