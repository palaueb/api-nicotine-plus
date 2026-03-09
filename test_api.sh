#!/usr/bin/env bash
set -u

HOST="127.0.0.1"
PORT="12339"
TOKEN="${API_TOKEN:-}"
SEARCH_QUERY="nicotine_api_test_$(date +%s)"
RUN_AUTH_REQUIRED_TESTS=0

PASS_COUNT=0
FAIL_COUNT=0

usage() {
  cat <<'EOF'
Usage: ./test_api.sh [options]

Options:
  --host <host>           API host (default: 127.0.0.1)
  --port <port>           API port (default: 12339)
  --token <token>         API token (or use API_TOKEN env)
  --search-query <query>  Query used for POST /search
  --auth-required         Run tests that expect 401 without token
  -h, --help              Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      HOST="$2"
      shift 2
      ;;
    --port)
      PORT="$2"
      shift 2
      ;;
    --token)
      TOKEN="$2"
      shift 2
      ;;
    --search-query)
      SEARCH_QUERY="$2"
      shift 2
      ;;
    --auth-required)
      RUN_AUTH_REQUIRED_TESTS=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      usage
      exit 2
      ;;
  esac
done

BASE_URL="http://${HOST}:${PORT}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

log() {
  printf '%s\n' "$*"
}

pass() {
  PASS_COUNT=$((PASS_COUNT + 1))
  printf 'PASS %s\n' "$*"
}

fail() {
  FAIL_COUNT=$((FAIL_COUNT + 1))
  printf 'FAIL %s\n' "$*"
}

json_eval() {
  local file="$1"
  local code="$2"
  python3 - "$file" "$code" <<'PY'
import json
import sys

file_path = sys.argv[1]
expr = sys.argv[2]

try:
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
except Exception as e:
    print(f"JSON parse error: {e}")
    sys.exit(1)

ns = {"data": data}
try:
    ok = eval(expr, ns, {})
except Exception as e:
    print(f"Eval error: {e}")
    sys.exit(1)

if bool(ok):
    sys.exit(0)

print(f"Expression returned false: {expr}")
sys.exit(1)
PY
}

request() {
  local method="$1"
  local path="$2"
  local body="${3:-}"
  local with_auth="${4:-1}"

  local out_file="$TMP_DIR/response_$(date +%s%N).json"
  local status_file="$TMP_DIR/status_$(date +%s%N).txt"

  local -a headers
  headers=(-H "Content-Type: application/json")

  if [[ "$with_auth" -eq 1 && -n "$TOKEN" ]]; then
    headers+=(-H "Authorization: Bearer ${TOKEN}")
  fi

  local url="${BASE_URL}${path}"

  local curl_status
  if [[ "$method" == "GET" ]]; then
    curl -sS -X GET "${headers[@]}" "$url" -o "$out_file" -w "%{http_code}" > "$status_file"
    curl_status=$?
  else
    curl -sS -X "$method" "${headers[@]}" "$url" -d "$body" -o "$out_file" -w "%{http_code}" > "$status_file"
    curl_status=$?
  fi

  if [[ $curl_status -ne 0 ]]; then
    echo "CURL_ERROR" > "$status_file"
  fi

  echo "$out_file|$(cat "$status_file")"
}

assert_http_and_json() {
  local name="$1"
  local method="$2"
  local path="$3"
  local expected_status="$4"
  local json_expr="$5"
  local body="${6:-}"
  local with_auth="${7:-1}"

  local result
  result="$(request "$method" "$path" "$body" "$with_auth")"

  local file="${result%%|*}"
  local status="${result##*|}"

  if [[ "$status" == "CURL_ERROR" ]]; then
    fail "$name (curl error, cannot reach ${BASE_URL})"
    return
  fi

  if [[ "$status" != "$expected_status" ]]; then
    fail "$name (expected HTTP ${expected_status}, got ${status})"
    log "Body: $(cat "$file")"
    return
  fi

  if ! json_eval "$file" "$json_expr" >/dev/null; then
    fail "$name (JSON assertion failed)"
    log "Body: $(cat "$file")"
    return
  fi

  pass "$name"
}

log "Testing API at ${BASE_URL}"

# Basic connectivity check first
health_result="$(request "GET" "/health" "" 1)"
health_file="${health_result%%|*}"
health_status="${health_result##*|}"

if [[ "$health_status" == "CURL_ERROR" ]]; then
  echo "Could not connect to ${BASE_URL}. Is Nicotine+ running and plugin enabled?"
  exit 1
fi

assert_http_and_json "GET /health" "GET" "/health" "200" "isinstance(data, dict) and data.get('status') == 'ok' and 'plugin' in data"
assert_http_and_json "GET /status" "GET" "/status" "200" "isinstance(data, dict) and all(k in data for k in ['connected','login_status','login_username','uploads_total','downloads_total'])"
assert_http_and_json "GET /searches (initial)" "GET" "/searches" "200" "isinstance(data.get('count'), int) and isinstance(data.get('items'), list)"
assert_http_and_json "GET /uploads" "GET" "/uploads" "200" "data.get('direction') == 'uploads' and isinstance(data.get('items'), list) and isinstance(data.get('count'), int)"
assert_http_and_json "GET /downloads" "GET" "/downloads" "200" "data.get('direction') == 'downloads' and isinstance(data.get('items'), list) and isinstance(data.get('count'), int)"
assert_http_and_json "GET /uploads/users" "GET" "/uploads/users" "200" "data.get('direction') == 'uploads' and isinstance(data.get('items'), list)"
assert_http_and_json "GET /downloads/users" "GET" "/downloads/users" "200" "data.get('direction') == 'downloads' and isinstance(data.get('items'), list)"
assert_http_and_json "GET /uploads?active_only=true" "GET" "/uploads?active_only=true" "200" "data.get('direction') == 'uploads' and data.get('active_only') is True"
assert_http_and_json "GET /downloads?active_only=true" "GET" "/downloads?active_only=true" "200" "data.get('direction') == 'downloads' and data.get('active_only') is True"
assert_http_and_json "GET /uploads?active_only=false" "GET" "/uploads?active_only=false" "200" "data.get('direction') == 'uploads' and data.get('active_only') is False"
assert_http_and_json "GET /downloads?active_only=false" "GET" "/downloads?active_only=false" "200" "data.get('direction') == 'downloads' and data.get('active_only') is False"
assert_http_and_json "GET /uploads?user=pepe&active_only=true" "GET" "/uploads?user=pepe&active_only=true" "200" "data.get('direction') == 'uploads' and data.get('user') == 'pepe'"
assert_http_and_json "GET /downloads?user=pepe&active_only=true" "GET" "/downloads?user=pepe&active_only=true" "200" "data.get('direction') == 'downloads' and data.get('user') == 'pepe'"

search_payload=$(printf '{"query":"%s","mode":"global","switch_page":false}' "$SEARCH_QUERY")
assert_http_and_json "POST /search (global)" "POST" "/search" "200" "data.get('ok') is True and isinstance(data.get('token'), int) and data.get('mode') == 'global' and data.get('query') != ''" "$search_payload"
assert_http_and_json "GET /searches (after search)" "GET" "/searches" "200" "isinstance(data.get('last_token'), int) and data.get('count', 0) >= 1 and isinstance(data.get('items'), list)"
assert_http_and_json "GET /search/results (latest)" "GET" "/search/results" "200" "isinstance(data.get('token'), int) and isinstance(data.get('items'), list) and isinstance(data.get('total'), int)"
assert_http_and_json "GET /search/results?limit=10&offset=0" "GET" "/search/results?limit=10&offset=0" "200" "data.get('limit') == 10 and data.get('offset') == 0 and isinstance(data.get('items'), list)"
assert_http_and_json "GET /search/results?token=999999999 (-> 400)" "GET" "/search/results?token=999999999" "400" "'error' in data"

assert_http_and_json "POST /downloads/enqueue (missing fields -> 400)" "POST" "/downloads/enqueue" "400" "'error' in data" '{}'
assert_http_and_json "POST /search/download (missing index -> 400)" "POST" "/search/download" "400" "'error' in data" '{"token":12345}'
assert_http_and_json "POST /search/download (unknown token -> 400)" "POST" "/search/download" "400" "'error' in data" '{"token":999999999,"index":0}'

assert_http_and_json "POST /search (empty query -> 400)" "POST" "/search" "400" "'error' in data" '{"query":"","mode":"global"}'
assert_http_and_json "POST /search (bad mode -> 400)" "POST" "/search" "400" "'error' in data" '{"query":"abc","mode":"invalid"}'
assert_http_and_json "GET /unknown (-> 400)" "GET" "/unknown" "400" "'error' in data"

if [[ "$RUN_AUTH_REQUIRED_TESTS" -eq 1 ]]; then
  if [[ -z "$TOKEN" ]]; then
    fail "Auth required tests requested but no token provided (--token or API_TOKEN)"
  else
    assert_http_and_json "GET /health without token (-> 401)" "GET" "/health" "401" "data.get('error') == 'unauthorized'" "" 0
    assert_http_and_json "GET /health with token" "GET" "/health" "200" "data.get('status') == 'ok'" "" 1
  fi
fi

log ""
log "Summary: ${PASS_COUNT} passed, ${FAIL_COUNT} failed"

if [[ $FAIL_COUNT -gt 0 ]]; then
  exit 1
fi

exit 0
