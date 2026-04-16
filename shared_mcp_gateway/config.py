from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib


@dataclass(slots=True)
class ListenConfig:
    """网关监听配置。"""

    host: str
    port: int
    path: str

    @property
    def url(self) -> str:
        """拼接出完整监听地址，供 HTTP 服务与客户端渲染复用。"""
        return f"http://{self.host}:{self.port}{self.path}"


@dataclass(slots=True)
class ServerConfig:
    """单个下游 MCP Server 的进程启动参数。"""

    key: str
    namespace: str
    command: str
    args: list[str]
    env: dict[str, str] | None = None
    enabled: bool = True


@dataclass(slots=True)
class GatewayConfig:
    """聚合网关本身的对外展示配置。"""

    name: str
    namespace_separator: str
    description: str


@dataclass(slots=True)
class Registry:
    """从 `registry.toml` 读取后的完整运行时配置对象。"""

    source_path: Path
    listen: ListenConfig
    gateway: GatewayConfig
    servers: list[ServerConfig]
    clients: dict[str, dict[str, Any]]
    local_exceptions: dict[str, Any]

    @property
    def enabled_servers(self) -> list[ServerConfig]:
        """只返回启用状态的下游服务，避免调用侧重复写过滤逻辑。"""
        return [server for server in self.servers if server.enabled]


def load_registry(path: str | Path) -> Registry:
    """读取 TOML 配置并转换成强类型 dataclass，作为全局配置入口。"""

    # 统一展开 `~` 并转成绝对路径，方便日志与错误信息稳定定位到同一个文件。
    source_path = Path(path).expanduser().resolve()
    data = tomllib.loads(source_path.read_text())

    listen = ListenConfig(**data['listen'])
    gateway = GatewayConfig(**data['gateway'])
    servers = [ServerConfig(**item) for item in data['servers']]

    return Registry(
        source_path=source_path,
        listen=listen,
        gateway=gateway,
        servers=servers,
        # clients / local_exceptions 是面向渲染与健康检查的扩展字段，不一定在所有环境都存在。
        clients=data.get('clients', {}),
        local_exceptions=data.get('local_exceptions', {}),
    )
