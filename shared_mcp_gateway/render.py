from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .config import Registry


# 默认优先复用当前执行渲染脚本的 Python 解释器，避免把开发者本机绝对路径写死到产物里。
# 如果调用方明确希望使用其他解释器，也可以通过环境变量覆盖。
PYTHON_BIN = Path(os.environ.get('SHARED_MCP_GATEWAY_PYTHON', sys.executable)).resolve()


def _bridge_command(project_root: Path, registry: Registry, caller: str) -> str:
    """生成各客户端复用的 bridge 启动命令。"""

    bridge = project_root / 'shared_mcp_gateway' / 'stdio_bridge.py'
    return f'{PYTHON_BIN} {bridge} --url {registry.listen.url} --caller {caller}'


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

    obj = {
        'mcpServers': {
            'shared-gateway': {
                'url': registry.listen.url,
                'transport': 'streamable-http',
                'connectionTimeoutMs': 10000,
                'disabled': False,
            }
        }
    }
    return json.dumps(obj, ensure_ascii=False, indent=2) + '\n'
