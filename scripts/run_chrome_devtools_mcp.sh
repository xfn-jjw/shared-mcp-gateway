#!/usr/bin/env bash
set -euo pipefail

# Keep shared-gateway's default Chrome DevTools MCP posture privacy-first.
export CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS="${CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS:-1}"
export CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS="${CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS:-1}"

args=(
  --no-usage-statistics
  --no-performance-crux
  --redact-network-headers
)

resolve_host_docker_url() {
  local url="$1"
  if [[ ! "$url" =~ ^(https?://)host\.docker\.internal(:[0-9]+.*)?$ ]]; then
    printf '%s' "$url"
    return
  fi

  local scheme="${BASH_REMATCH[1]}"
  local suffix="${BASH_REMATCH[2]}"
  local host_ip=""

  if command -v getent >/dev/null 2>&1; then
    host_ip="$(getent ahostsv4 host.docker.internal 2>/dev/null | awk '{print $1; exit}')"
    if [[ -z "$host_ip" ]]; then
      host_ip="$(getent hosts host.docker.internal 2>/dev/null | awk '{print $1; exit}')"
    fi
  fi

  if [[ -z "$host_ip" ]] && command -v python3 >/dev/null 2>&1; then
    host_ip="$(python3 - <<'PY' 2>/dev/null
import socket
print(socket.gethostbyname("host.docker.internal"))
PY
)"
  fi

  if [[ -n "$host_ip" ]]; then
    printf '%s%s%s' "$scheme" "$host_ip" "$suffix"
    return
  fi

  printf '%s' "$url"
}

if [[ -n "${CHROME_DEVTOOLS_BROWSER_URL:-}" ]]; then
  args+=("--browser-url=$(resolve_host_docker_url "${CHROME_DEVTOOLS_BROWSER_URL}")")
fi

if [[ -n "${CHROME_DEVTOOLS_WS_ENDPOINT:-}" ]]; then
  args+=("--ws-endpoint=${CHROME_DEVTOOLS_WS_ENDPOINT}")
fi

if [[ -n "${CHROME_DEVTOOLS_WS_HEADERS:-}" ]]; then
  args+=("--ws-headers=${CHROME_DEVTOOLS_WS_HEADERS}")
fi

if [[ -n "${CHROME_DEVTOOLS_EXECUTABLE_PATH:-}" ]]; then
  args+=("--executable-path=${CHROME_DEVTOOLS_EXECUTABLE_PATH}")
fi

if [[ -n "${CHROME_DEVTOOLS_CHANNEL:-}" ]]; then
  args+=("--channel=${CHROME_DEVTOOLS_CHANNEL}")
fi

if [[ -n "${CHROME_DEVTOOLS_VIEWPORT:-}" ]]; then
  args+=("--viewport=${CHROME_DEVTOOLS_VIEWPORT}")
fi

if [[ "${CHROME_DEVTOOLS_HEADLESS:-false}" == "true" ]]; then
  args+=(--headless)
fi

if [[ "${CHROME_DEVTOOLS_ISOLATED:-false}" == "true" ]]; then
  args+=(--isolated)
fi

if [[ "${CHROME_DEVTOOLS_SLIM:-false}" == "true" ]]; then
  args+=(--slim)
fi

if [[ -n "${CHROME_DEVTOOLS_MCP_EXTRA_ARGS:-}" ]]; then
  # Intentionally split shell-style for simple flag lists such as:
  # CHROME_DEVTOOLS_MCP_EXTRA_ARGS="--viewport=1280x720 --headless"
  # shellcheck disable=SC2206
  extra_args=(${CHROME_DEVTOOLS_MCP_EXTRA_ARGS})
  args+=("${extra_args[@]}")
fi

if (($# > 0)); then
  args+=("$@")
fi

exec npx -y chrome-devtools-mcp@latest "${args[@]}"
