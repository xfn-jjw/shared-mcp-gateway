from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import os
import re
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
    language: str | None = None
    description: str | None = None

    @property
    def display_language(self) -> str:
        """返回 dashboard 展示用语言。

        优先使用注册表里显式声明的 `language`，只有没配置时才做启发式推断，
        这样既兼顾当前示例开箱即用，也给未来多语言 MCP 留出人工覆盖入口。
        """

        explicit_language = (self.language or "").strip()
        if explicit_language:
            return explicit_language
        return infer_server_language(command=self.command, args=self.args, env=self.env)


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


def infer_server_language(command: str, args: list[str], env: dict[str, str] | None = None) -> str:
    """按启动命令、参数、环境变量与脚本 shebang 推断下游 MCP 语言。

    这里刻意只做轻量、稳定的启发式判断：
    1. 先看命令/参数中是否直接出现语言运行时特征；
    2. 再看环境变量里是否暴露出典型生态痕迹；
    3. 最后尝试读取可执行脚本首行 shebang，补足类似 `openspace-mcp` 这类包装脚本。
    """

    env = env or {}
    lowered_segments = _collect_lowered_segments(command=command, args=args, env=env)

    # 先匹配更明确的语言运行时，避免 `/bin/bash -lc "python server.py"` 被误判成 Shell。
    language_matchers: list[tuple[str, tuple[str, ...]]] = [
        ("Python", ("python", "python3", "pypy", "uvicorn", ".py", "pyproject.toml", "pip", "poetry", "PYTHONPATH")),
        ("Node.js", ("node", "npm", "pnpm", "yarn", "tsx", "ts-node", "bun", ".js", ".mjs", ".cjs", ".ts", "package.json")),
        ("Go", ("go ", "/go/bin/", ".go", "go.mod")),
        ("Rust", ("cargo", "rustc", ".rs", "cargo.toml")),
        ("Java", ("java", ".jar", "mvn", "gradle", "pom.xml", "build.gradle")),
        (".NET", ("dotnet", ".dll", ".csproj", ".sln", "nuget")),
        ("Ruby", ("ruby", "bundle exec", ".rb", "gemfile")),
        ("PHP", ("php", "composer", ".php")),
        ("Shell", ("/bin/bash", "/bin/sh", " bash ", " sh ", ".sh")),
    ]

    for language, hints in language_matchers:
        if any(hint.lower() in lowered_segments for hint in hints):
            return language

    shebang_language = _infer_language_from_shebang(command)
    if shebang_language:
        return shebang_language

    return "未知"


def _collect_lowered_segments(command: str, args: list[str], env: dict[str, str]) -> str:
    """把命令、参数、环境变量折叠成统一小写文本，方便做包含判断。"""

    parts = [command, *args]
    # 环境变量只拼接关键名字和值，避免丢掉像 PYTHONPATH 这类强提示信号。
    parts.extend(f"{key}={value}" for key, value in env.items())
    normalized_parts: list[str] = []
    for part in parts:
        normalized_parts.append(str(part))
        normalized_parts.append(os.path.basename(str(part)))
    return " ".join(normalized_parts).lower()


def _infer_language_from_shebang(command: str) -> str | None:
    """尝试从可执行脚本首行 shebang 推断语言。"""

    command_path = Path(command).expanduser()
    if not command_path.is_file():
        return None

    try:
        with command_path.open("r", encoding="utf-8", errors="ignore") as handle:
            first_line = handle.readline().strip().lower()
    except OSError:
        return None

    if not first_line.startswith("#!"):
        return None

    shebang = re.sub(r"\s+", " ", first_line)
    if "python" in shebang:
        return "Python"
    if any(token in shebang for token in ("node", "bun", "deno")):
        return "Node.js"
    if "ruby" in shebang:
        return "Ruby"
    if "php" in shebang:
        return "PHP"
    if any(token in shebang for token in ("bash", "sh", "zsh")):
        return "Shell"
    return None
