#!/usr/bin/env bash
set -euo pipefail

workdir="${MCP_1688_WORKDIR:-/workspace/1688mcp}"
runtime_dir="${MCP_1688_RUNTIME_DIR:-/tmp/1688mcp-runtime}"
daemon_port="${OPENCLI_DAEMON_PORT:-19825}"
proxy_target="${OPENCLI_DAEMON_PROXY_TARGET:-host.docker.internal:${daemon_port}}"

# 1. 在容器内把 OpenCLI 固定访问的本机 daemon 端口转发到宿主机。
if [ -n "${proxy_target}" ] && command -v socat >/dev/null 2>&1; then
  if ! node -e "require('node:net').connect(${daemon_port}, '127.0.0.1').once('connect', function(){ this.destroy(); process.exit(0) }).once('error', function(){ process.exit(1) })" >/dev/null 2>&1; then
    socat "TCP-LISTEN:${daemon_port},bind=127.0.0.1,reuseaddr,fork" "TCP:${proxy_target}" &
  fi
fi

# 2. 校验 1688 MCP 源码挂载，避免 gateway 启动时出现难读的 module not found。
if [ ! -f "${workdir}/src/index.js" ]; then
  echo "1688 MCP entrypoint not found: ${workdir}/src/index.js" >&2
  exit 1
fi
if [ ! -f "${workdir}/package.json" ]; then
  echo "1688 MCP package.json not found: ${workdir}/package.json" >&2
  exit 1
fi

# 3. 如果源码挂载目录没有 node_modules，在容器可写缓存里安装依赖后启动。
if [ ! -d "${workdir}/node_modules/@modelcontextprotocol/sdk" ] || [ ! -d "${workdir}/node_modules/zod" ]; then
  case "${runtime_dir}" in
    ""|"/"|"/tmp"|"/var/tmp")
      echo "Refuse to recreate unsafe 1688 MCP runtime dir: ${runtime_dir}" >&2
      exit 1
      ;;
  esac
  rm -rf "${runtime_dir}"
  mkdir -p "${runtime_dir}"
  cp -R "${workdir}/src" "${runtime_dir}/src"
  cp "${workdir}/package.json" "${runtime_dir}/package.json"
  if [ -f "${workdir}/package-lock.json" ]; then
    cp "${workdir}/package-lock.json" "${runtime_dir}/package-lock.json"
    (cd "${runtime_dir}" && npm ci --omit=dev --no-audit --no-fund)
  else
    (cd "${runtime_dir}" && npm install --omit=dev --no-audit --no-fund)
  fi
  workdir="${runtime_dir}"
fi

# 4. 启动 stdio MCP，工具调用时会通过 opencli 与 1688 Chrome 插件通信。
cd "${workdir}"
exec node ./src/index.js
