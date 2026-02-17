#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "[1/7] npm 의존성 설치 확인"
if [[ ! -x "node_modules/.bin/wrangler" ]]; then
  echo " - wrangler가 설치되어 있지 않음 → npm i 실행"
  npm i
fi

WRANGLER_BIN="$ROOT/node_modules/.bin/wrangler"
if [[ ! -x "$WRANGLER_BIN" ]]; then
  WRANGLER_BIN="$(command -v wrangler || true)"
fi
if [[ -z "${WRANGLER_BIN:-}" ]]; then
  echo " - ERROR: wrangler를 찾지 못했습니다."
  echo "   npm i가 정상 동작했는지 확인하세요."
  exit 1
fi

wr_tty() {
  # Wrangler는 stdout이 TTY가 아닐 때(non-interactive) API 토큰을 강제하는 경우가 있어,
  # `script`로 pseudo-tty를 붙여서 항상 interactive로 실행한다.
  local tmp
  tmp="$(mktemp -t wrangler_out)"
  set +e
  script -q "$tmp" "$WRANGLER_BIN" "$@"
  local code=$?
  set -e
  cat "$tmp"
  rm -f "$tmp"
  return $code
}

wr_tty_sh() {
  # bash -lc 내부에서 파이프/리다이렉션을 쓰는 케이스용
  local tmp
  tmp="$(mktemp -t wrangler_out)"
  set +e
  script -q "$tmp" bash -lc "$1"
  local code=$?
  set -e
  cat "$tmp"
  rm -f "$tmp"
  return $code
}

echo "[2/7] Cloudflare 로그인 확인"
if ! wr_tty whoami >/dev/null 2>&1; then
  echo " - 로그인 필요: 브라우저 인증을 진행합니다."
  wr_tty login
fi

echo "[3/7] D1 생성/바인딩 설정"
if grep -q "REPLACE_ME_AFTER_WRANGLER_D1_CREATE" wrangler.toml; then
  echo " - D1 생성: jump_backend_db"
  OUT="$(wr_tty d1 create jump_backend_db)"
  echo "$OUT"

  DBID="$(python3 - <<'PY'
import re, sys
text = sys.stdin.read()
# `script` 출력에는 제어 문자가 섞일 수 있어 먼저 제거한다.
text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)

# 가장 안전하게 UUID 패턴을 찾는다.
m = re.search(r'([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})', text, re.I)
print(m.group(1) if m else "")
PY
<<<"$OUT")"

  if [[ -z "$DBID" ]]; then
    echo " - ERROR: wrangler 출력에서 database_id를 찾지 못했습니다."
    echo "   wrangler.toml의 database_id를 수동으로 채운 뒤 다시 실행하세요."
    exit 1
  fi

  echo " - wrangler.toml 업데이트: database_id=$DBID"
  python3 - <<PY
from pathlib import Path
p = Path("wrangler.toml")
txt = p.read_text(encoding="utf-8")
txt = txt.replace("REPLACE_ME_AFTER_WRANGLER_D1_CREATE", "$DBID")
p.write_text(txt, encoding="utf-8")
PY
else
  echo " - wrangler.toml에 database_id가 이미 설정되어 있습니다."
fi

echo "[4/7] D1 마이그레이션 적용"
wr_tty d1 migrations apply jump_backend_db --remote

echo "[5/7] Secret 설정(LICENSE_PEPPER)"
SECRETS="$(wr_tty secret list 2>/dev/null || true)"
if echo "$SECRETS" | grep -q "LICENSE_PEPPER"; then
  echo " - LICENSE_PEPPER 이미 설정됨"
else
  echo " - LICENSE_PEPPER 설정(자동 생성)"
  PEPPER="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)"
  # wrangler secret put 은 stdin 입력을 요구함. stdout은 pseudo-tty로 유지한다.
  wr_tty_sh "printf %s '$PEPPER' | \"$WRANGLER_BIN\" secret put LICENSE_PEPPER >/dev/null"
  echo " - LICENSE_PEPPER 설정 완료"
fi

echo "[6/7] (선택) 관리자 이중 보호 토큰(ADMIN_TOKEN)"
if echo "$SECRETS" | grep -q "ADMIN_TOKEN"; then
  echo " - ADMIN_TOKEN 이미 설정됨"
else
  echo " - ADMIN_TOKEN은 선택입니다. 설정하려면 아래를 실행하세요:"
  echo "   $WRANGLER_BIN secret put ADMIN_TOKEN"
fi

echo "[6.5/7] (선택) 커스텀 도메인 라우트(routes) 설정"
if grep -qE '^[[:space:]]*routes[[:space:]]*=' wrangler.toml; then
  echo " - routes가 이미 설정되어 있습니다."
else
  read -r -p " - api.<도메인> 라우트를 wrangler.toml에 설정할까요? (y/N): " SET_ROUTES
  if [[ "${SET_ROUTES:-}" =~ ^[Yy]$ ]]; then
    read -r -p "   도메인 입력 (예: guardian01.online 또는 https://api.guardian01.online/): " DOMAIN_INPUT
    DOMAIN_INPUT="$(echo "${DOMAIN_INPUT:-}" | tr -d ' ' )"

    # allow full URL
    DOMAIN_INPUT="${DOMAIN_INPUT#http://}"
    DOMAIN_INPUT="${DOMAIN_INPUT#https://}"
    DOMAIN_INPUT="${DOMAIN_INPUT%%/*}"   # drop path
    DOMAIN_INPUT="${DOMAIN_INPUT%%:*}"   # drop port

    BASE_DOMAIN="$DOMAIN_INPUT"
    # if user pasted api.<domain>, normalize to apex domain
    if [[ "$BASE_DOMAIN" == api.* ]]; then
      BASE_DOMAIN="${BASE_DOMAIN#api.}"
    fi

    if [[ -z "$BASE_DOMAIN" || "$BASE_DOMAIN" != *.* ]]; then
      echo " - ERROR: 도메인 형식이 올바르지 않습니다."
      exit 1
    fi
    echo " - routes 설정: api.$BASE_DOMAIN/*"
    python3 - <<PY
from pathlib import Path

p = Path("wrangler.toml")
txt = p.read_text(encoding="utf-8")

base = "$BASE_DOMAIN"
pattern_host = f"api.{base}"

lines = txt.splitlines()
out: list[str] = []
replaced = False
had_routes = False
for line in lines:
    s = line.strip()
    if s.startswith("routes") and "=" in s:
        had_routes = True
    if s == "# routes = [":
        out.append("routes = [")
        replaced = True
        continue
    if "pattern = \"api.YOUR_DOMAIN/*\"" in line and "zone_name" in line:
        out.append(f'  {{ pattern = "{pattern_host}/*", zone_name = "{base}" }}')
        replaced = True
        continue
    if s == "# ]":
        out.append("]")
        replaced = True
        continue
    out.append(line)

txt2 = "\n".join(out).rstrip() + "\n"
if not had_routes:
    if not replaced:
        txt2 = txt.rstrip() + f'\n\nroutes = [\n  {{ pattern = "{pattern_host}/*", zone_name = "{base}" }}\n]\n'

p.write_text(txt2, encoding="utf-8")
PY
  else
    echo " - routes 설정을 건너뜁니다. (workers.dev로만 접근 가능)"
  fi
fi

echo "[7/7] 배포"
wr_tty deploy

echo
echo "완료. 테스트:"
echo " - workers.dev URL 또는 (라우트 설정 후) https://api.<도메인>/v1/health"

echo
echo "추가(권장): 커스텀 도메인 라우트 설정"
echo " - wrangler.toml에 routes를 설정하거나, Cloudflare 대시보드에서 Route를 추가해야"
echo "   https://api.<도메인> 으로 접근할 때 Worker가 응답합니다."
echo " - routes 설정을 원하면 wrangler.toml의 주석을 풀고 아래 형태로 넣으세요:"
echo "   routes = [ { pattern = \"api.YOUR_DOMAIN/*\", zone_name = \"YOUR_DOMAIN\" } ]"
