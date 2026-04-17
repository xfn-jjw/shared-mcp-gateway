from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .config import Registry


def _resolve_python_bin(project_root: Path) -> Path:
    """为 bridge 选择一个稳定可用的 Python 解释器。

    优先级：
    1. `SHARED_MCP_GATEWAY_PYTHON` 显式覆盖
    2. 当前项目 `.venv/bin/python`
    3. 当前进程 `sys.executable`

    这样可以避免误用其他项目的虚拟环境，导致 `stdio_bridge.py`
    缺少依赖（例如 anyio / mcp）。
    """

    override = os.environ.get('SHARED_MCP_GATEWAY_PYTHON')
    if override:
        return Path(override).expanduser().resolve()

    project_venv_python = project_root / '.venv' / 'bin' / 'python'
    if project_venv_python.exists():
        return project_venv_python.resolve()

    return Path(sys.executable).resolve()


def _bridge_command(project_root: Path, registry: Registry, caller: str) -> str:
    """生成各客户端复用的 bridge 启动命令。"""

    bridge = project_root / 'shared_mcp_gateway' / 'stdio_bridge.py'
    python_bin = _resolve_python_bin(project_root)
    return f'{python_bin} {bridge} --url {registry.listen.url} --caller {caller}'


def _render_openclaw_local_servers(registry: Registry) -> dict[str, dict]:
    """把 OpenClaw 的本地特例 MCP 也渲染进最终配置。

    当前注册表里的 `local_exceptions.openclaw` 主要用于描述
    “哪些能力不进 shared-gateway、但仍需要由 OpenClaw 直连”的场景。
    例如 OpenSpace 会继续保留在本地独立运行。
    """

    config = registry.local_exceptions.get('openclaw', {})
    endpoint = config.get('endpoint')
    keep_local = config.get('keep_local', [])
    if not endpoint or not keep_local:
        return {}

    transport = config.get('transport', 'streamable-http')
    connection_timeout_ms = int(config.get('connection_timeout_ms', 10000))
    disabled = bool(config.get('disabled', False))

    local_servers: dict[str, dict] = {}
    for server_name in keep_local:
        # 这里默认每个 keep_local server 复用同一个 endpoint，
        # 满足当前 OpenSpace 独立本地服务的接入方式。
        local_servers[server_name] = {
            'url': endpoint,
            'transport': transport,
            'connectionTimeoutMs': connection_timeout_ms,
            'disabled': disabled,
        }
    return local_servers


def render_codex_config(registry: Registry, project_root: Path) -> str:
    """渲染 Codex 使用的 MCP TOML 片段。"""

    lines = [
        '[mcp_servers.shared-gateway]',
        'command = "/bin/bash"',
        f'args = ["-lc", "{_bridge_command(project_root, registry, "codex")}"]',
        'enabled = true',
        '',
    ]
    return '\n'.join(lines)


def render_opencode_config(registry: Registry, project_root: Path) -> str:
    """渲染 OpenCode 使用的 JSON 配置。"""

    obj = {
        '$schema': 'https://opencode.ai/config.json',
        'mcp': {
            'shared-gateway': {
                'type': 'local',
                'enabled': True,
                'command': [
                    '/bin/bash',
                    '-lc',
                    _bridge_command(project_root, registry, 'opencode'),
                ],
            }
        },
    }
    return json.dumps(obj, ensure_ascii=False, indent=2) + '\n'


def render_openclaw_config(registry: Registry) -> str:
    """渲染 OpenClaw 直接访问 HTTP MCP 网关的配置。"""

    mcp_servers = {
        'shared-gateway': {
            'url': registry.listen.url,
            'transport': 'streamable-http',
            'connectionTimeoutMs': 10000,
            'disabled': False,
        }
    }
    # 把 OpenClaw 仍需本地直连的 MCP 一并写进配置，避免手工合并。
    mcp_servers.update(_render_openclaw_local_servers(registry))

    obj = {
        'mcpServers': mcp_servers
    }
    return json.dumps(obj, ensure_ascii=False, indent=2) + '\n'
