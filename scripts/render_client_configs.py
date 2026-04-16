from __future__ import annotations

import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from shared_mcp_gateway.config import load_registry
from shared_mcp_gateway.render import (
    render_codex_config,
    render_openclaw_config,
    render_opencode_config,
)


def main() -> None:
    """读取统一注册表，并一次性生成各客户端的接入配置文件。

    这样可以把“共享网关地址、bridge 启动命令、客户端接入格式”集中维护，
    避免手工复制时出现配置漂移。
    """

    registry = load_registry(project_root / 'registry.toml')
    generated = project_root / 'generated'
    # generated 目录不存在时自动创建，便于脚本在新环境首次执行。
    generated.mkdir(exist_ok=True)

    (generated / 'codex-mcp.toml').write_text(render_codex_config(registry, project_root))
    (generated / 'opencode-mcp.jsonc').write_text(render_opencode_config(registry, project_root))
    (generated / 'openclaw-mcp.json').write_text(render_openclaw_config(registry))

    # 打印产物路径，方便 CI 或人工执行后直接确认输出位置。
    print('generated:')
    print(generated / 'codex-mcp.toml')
    print(generated / 'opencode-mcp.jsonc')
    print(generated / 'openclaw-mcp.json')


if __name__ == '__main__':
    main()
