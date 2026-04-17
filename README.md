# Shared MCP Gateway

统一把多个共享型 MCP Server 收口到一个 HTTP 网关里，对外提供一套稳定、可观测、可复用的 MCP 接入层，方便 Codex、OpenCode、Claude Code、OpenClaw 等客户端共同使用。

## 项目解决了什么问题

在多客户端、多 MCP Server 并行使用的场景下，通常会遇到这些问题：

- 每个客户端都要单独维护一套 MCP 配置，重复劳动多。
- 同一个工具链在不同客户端里配置不一致，容易出现“这个客户端能用、那个客户端不能用”。
- 下游 MCP Server 一旦异常，排查入口分散，不方便统一日志、自检和熔断处理。
- 新增或替换一个 MCP Server 时，需要分别改多份配置，变更成本高。

`shared-mcp-gateway` 的目标，就是把这些共享型能力统一纳管：

- **一处维护注册表**：通过 `registry.toml` / `registry.compose.toml` 统一维护下游 MCP。
- **一处对外暴露能力**：通过一个 HTTP MCP 端点聚合多个下游服务。
- **一处做运维治理**：统一健康检查、结构化日志、失败隔离、最小熔断。
- **一处生成客户端配置**：自动产出 Codex / OpenCode / OpenClaw 的接入配置片段。

## 项目能干什么

当前项目已经支持：

- 聚合多个基于 stdio 的下游 MCP Server。
- 把下游工具按 `namespace.tool_name` 方式统一暴露。
- 为不同客户端自动打上 `caller` 标识，方便日志追踪。
- 提供 `/healthz` 健康检查接口，查看已连接服务、失败服务、熔断状态。
- 提供结构化 `logfmt` 日志，便于 grep、CLS、Loki 等系统检索。
- 在下游异常时做最小隔离，避免单个 MCP Server 挂掉影响整体体验。
- 生成客户端配置文件：
  - Codex：`generated/codex-mcp.toml`
  - OpenCode：`generated/opencode-mcp.jsonc`
  - OpenClaw：`generated/openclaw-mcp.json`
- 通过 `scripts/self_check.py` 进行连通性、自检工具、关键能力探活。

## 适用场景

适合以下场景直接使用：

- 同一套 MCP 能力需要被多个 AI 客户端复用。
- 希望把“共享能力”与“宿主本地特例能力”分层治理。
- 希望统一日志、自检、健康检查与故障隔离。
- 希望新增一个共享 MCP 时，只改一份注册表配置。

## 项目结构

```text
shared-mcp-gateway/
├── Dockerfile                          # 网关镜像构建文件
├── docker-compose.yml                  # 当前本地落地用 Compose 编排
├── registry.toml                       # 宿主机直跑配置
├── registry.compose.toml               # 容器内运行配置
├── requirements.txt                    # Python 依赖
├── docs/
│   └── mcp-topology.md                 # 哪些 MCP 进入网关、哪些保留本地特例
├── generated/                          # 自动生成的客户端配置文件
├── templates/                          # 可复制的配置模板
│   ├── docker-compose.template.yml     # Compose 配置模板
│   ├── registry.compose.template.toml  # 容器内注册表模板
│   └── registry.template.toml          # 宿主机注册表模板
├── scripts/
│   ├── render_client_configs.py        # 生成客户端配置片段
│   └── self_check.py                   # 健康检查与关键工具自检
├── shared_mcp_gateway/
│   ├── config.py                       # 注册表解析
│   ├── gateway.py                      # HTTP MCP 聚合网关主程序
│   ├── logging_utils.py                # 结构化日志输出
│   ├── render.py                       # 客户端配置渲染
│   └── stdio_bridge.py                 # stdio 客户端到 HTTP MCP 的桥接
```

## 核心工作方式

```mermaid
flowchart LR
    A["Codex / OpenCode / OpenClaw"] --> B["stdio_bridge / HTTP Client"]
    B --> C["Shared MCP Gateway"]
    C --> D["mempalace"]
    C --> E["mysql-db"]
    C --> F["obsidian-kb"]
    C --> G["tencent-cls"]
```

## 运行链路说明

一次 MCP 请求进入共享网关后，关键路径如下：

1. 客户端通过 `stdio_bridge.py` 或直接通过 HTTP 访问共享网关。
2. `RequestLoggingMiddleware` 注入 `caller`、`request_id`、访问日志上下文。
3. `SharedMcpGateway` 依据工具名 / 资源 URI / prompt 名定位目标下游。
4. 若对应下游已被熔断，请求会被快速拒绝，避免持续打到异常服务。
5. 若允许转发，请求进入 `DownstreamConnection`，通过单 session 锁串行访问下游 MCP。
6. 调用完成后更新 metrics、failure streak、circuit breaker，并同步到 heartbeat / healthz。

核心模块职责建议按下面理解：

- `shared_mcp_gateway/config.py`：注册表解析与强类型配置对象。
- `shared_mcp_gateway/gateway.py`：统一索引、请求转发、熔断隔离、健康检查、心跳日志。
- `shared_mcp_gateway/stdio_bridge.py`：给只支持 stdio 的客户端提供 HTTP 网关桥接层。
- `shared_mcp_gateway/render.py`：把统一注册表渲染成不同客户端的接入配置。
- `scripts/self_check.py`：从健康接口和真实 MCP 调用两个维度做联通性自检。

## 请求时序图

下面这张图更适合对应代码阅读时建立整体心智模型：

```mermaid
sequenceDiagram
    participant Client as "MCP Client"
    participant Bridge as "stdio_bridge / HTTP Client"
    participant Middleware as "RequestLoggingMiddleware"
    participant Gateway as "SharedMcpGateway"
    participant Breaker as "CircuitBreaker"
    participant Downstream as "DownstreamConnection"
    participant Server as "Downstream MCP Server"

    Client->>Bridge: 发起 list_tools / call_tool / read_resource
    Bridge->>Middleware: HTTP 请求进入网关
    Middleware->>Gateway: 注入 caller / request_id 后转发
    Gateway->>Breaker: 检查目标下游是否允许访问
    alt breaker open
        Breaker-->>Gateway: reject
        Gateway-->>Client: 快速失败 / 返回熔断提示
    else breaker closed
        Gateway->>Downstream: 按 namespace 路由请求
        Downstream->>Server: 串行发起 MCP 调用
        Server-->>Downstream: 返回结果或异常
        Downstream-->>Gateway: 返回标准 MCP 响应
        Gateway->>Gateway: 更新 metrics / failure streak / breaker
        Gateway-->>Client: 返回聚合后的 MCP 响应
    end
```

## 阅读代码建议

如果要快速看懂主链路，建议按这个顺序读：

1. `shared_mcp_gateway/config.py`：先理解注册表结构。
2. `shared_mcp_gateway/render.py`：理解客户端接入配置是怎么生成的。
3. `shared_mcp_gateway/stdio_bridge.py`：理解 stdio 客户端如何接到 HTTP 网关。
4. `shared_mcp_gateway/gateway.py`：重点看 `SharedMcpGateway`、`DownstreamConnection`、`RequestLoggingMiddleware`。
5. `scripts/self_check.py`：理解上线后如何验证“接口活着”和“真实能力可用”。

## 快速开始

### 1. 安装依赖

```bash
cd /path/to/shared-mcp-gateway
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 准备配置

你可以直接参考模板文件：

- `templates/registry.template.toml`
- `templates/registry.compose.template.toml`
- `templates/docker-compose.template.yml`

最常见的做法是：

```bash
cp templates/registry.template.toml registry.local.toml
cp templates/registry.compose.template.toml registry.compose.local.toml
cp templates/docker-compose.template.yml docker-compose.local.yml
```

然后把模板里的路径、端口、下游服务命令替换成你自己的实际环境。

### 3. 本地直接启动

```bash
python3 shared_mcp_gateway/gateway.py --registry registry.toml --log-level INFO
```

启动后默认访问：

- MCP 端点：`http://127.0.0.1:8787/mcp`
- 健康检查：`http://127.0.0.1:8787/healthz`

### 4. Docker Compose 启动

```bash
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:8787/healthz
```

停止：

```bash
docker compose down
```

## 如何配置：核心配置说明

项目的核心配置文件是 `registry.toml`，主要包含五部分：

### 1. 监听配置

```toml
[listen]
host = "127.0.0.1"
port = 8787
path = "/mcp"
```

含义：

- `host`：网关监听地址
- `port`：网关监听端口
- `path`：MCP HTTP 路径

### 2. 网关元信息

```toml
[gateway]
name = "shared-gateway"
namespace_separator = "."
description = "Shared MCP gateway for Codex, OpenCode and OpenClaw."
```

含义：

- `name`：对外暴露的网关名称
- `namespace_separator`：命名空间分隔符，默认通常使用 `.`
- `description`：网关描述信息

### 3. 下游 MCP Server 配置

```toml
[[servers]]
key = "mysql-db"
enabled = true
namespace = "mysql_db"
language = "Python"
description = "MySQL 只读查询能力"
command = "/bin/bash"
args = ["-lc", "cd /opt/mcps/mysql-connector && ./.venv/bin/python server.py"]
```

含义：

- `key`：下游服务唯一标识
- `enabled`：是否启用
- `namespace`：工具名前缀命名空间
- `language`：可选，dashboard 展示的实现语言；不填时会按命令做启发式推断
- `description`：可选，dashboard 中展示“这个 MCP 是干什么的”的说明
- `command`：启动命令
- `args`：启动参数
- `env`：可选，给该服务单独注入环境变量

### 4. 本地特例说明（可选）

```toml
[local_exceptions.some-client]
keep_local = ["host-only-mcp"]
reason = "示例：某个能力必须跟随单一宿主独立运行。"
endpoint = "http://127.0.0.1:8090/mcp"
```

用于记录哪些能力不走共享网关，而是继续保留本地直连。


### 5. 客户端配置路径元信息（可选）

```toml
[clients.codex]
config_path = "~/.codex/config.toml"
```

含义：

- `clients.*` 主要用于记录目标客户端配置文件所在位置。
- 当前项目默认**不会自动写回**这些路径。
- 更推荐先运行 `scripts/render_client_configs.py`，再把生成结果复制到对应客户端配置里。

## 如何配置：案例

### 案例 1：宿主机直跑配置

下面是一个可直接参考的最小示例：

```toml
[listen]
host = "127.0.0.1"
port = 8787
path = "/mcp"

[gateway]
name = "shared-gateway"
namespace_separator = "."
description = "Shared MCP gateway for local development."

[[servers]]
key = "mempalace"
enabled = true
namespace = "mempalace"
command = "/opt/mempalace/.venv/bin/python"
args = ["-m", "mempalace.mcp_server"]
env = { PYTHONPATH = "/opt/mempalace" }

[[servers]]
key = "mysql-db"
enabled = true
namespace = "mysql_db"
command = "/bin/bash"
args = ["-lc", "cd /opt/mcps/mysql-connector && ./.venv/bin/python server.py"]

[[servers]]
key = "openspace"
enabled = true
namespace = "openspace"
command = "/opt/OpenSpace/.venv/bin/openspace-mcp"
args = []
env = { OPENSPACE_HOST_SKILL_DIRS = "/opt/openclaw-workspace/skills", OPENSPACE_WORKSPACE = "/opt/OpenSpace" }

[local_exceptions.shared_gateway]
managed = ["mempalace", "mysql_db", "openspace"]
reason = "共享能力统一由 shared-gateway 纳管。"
```

### 案例 2：Docker Compose 配置思路

如果你希望容器内统一运行网关，可参考下面的思路：

```yaml
services:
  shared-mcp-gateway:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: shared-mcp-gateway
    restart: unless-stopped
    ports:
      - "127.0.0.1:8787:8787"
    environment:
      OBSIDIAN_VAULT_PATH: /workspace/openclaw-workspace
      PYTHONPATH: /workspace/mempalace
      OPENROUTER_API_KEY: ${OPENROUTER_API_KEY:-}
    volumes:
      - /opt/mcps:/workspace/mcps:ro
      - /opt/mempalace:/workspace/mempalace:ro
      - /opt/OpenSpace:/workspace/OpenSpace:rw
      - /opt/openclaw-workspace:/workspace/openclaw-workspace:rw
      - /opt/mempalace-data:/root/.mempalace:rw
```

适合：

- 把多个 MCP 运行时依赖挂进同一个容器上下文。
- 通过只读挂载保证下游代码目录稳定。

如果希望 Docker 版 `shared-gateway` 也托管 `OpenSpace`，还需要：

- 在镜像里安装 OpenSpace 的 Python 依赖
- 把 OpenSpace 源码挂载到 `/workspace/OpenSpace`
- 通过环境变量把 LLM / OpenSpace API 凭证透传进容器
- 在 `registry.compose.toml` 里把 `openspace` 加入 `[[servers]]`
- 统一使用容器里的 `registry.compose.toml`。

## 配置模板文件

为了便于直接落地，项目已经补充了可复制的模板文件：

### 1. 注册表模板

文件：`templates/registry.template.toml`

用途：

- 新环境初始化时，直接复制一份改路径即可。
- 适合作为宿主机直跑的起点配置。
- 保留了 `listen`、`gateway`、`servers`、`clients`、`local_exceptions` 的完整结构。

建议使用方式：

```bash
cp templates/registry.template.toml registry.local.toml
```


### 2. 容器内注册表模板

文件：`templates/registry.compose.template.toml`

用途：

- 给 Docker / Compose 场景提供容器内路径版本的注册表模板。
- 避免把宿主机绝对路径误带入容器配置。
- 适合作为 `registry.compose.toml` 的可复制起点。

建议使用方式：

```bash
cp templates/registry.compose.template.toml registry.compose.local.toml
```

### 3. Compose 模板

文件：`templates/docker-compose.template.yml`

用途：

- 新机器或新环境快速准备 Compose 编排。
- 避免直接修改现网或当前机器专用的 `docker-compose.yml`。
- 便于把挂载路径、环境变量改成团队自己的规范。

建议使用方式：

```bash
cp templates/docker-compose.template.yml docker-compose.local.yml
```


## 客户端接入示例

推荐接入流程：

1. 先启动 shared-gateway，并确认 `http://127.0.0.1:8787/healthz` 正常。
2. 执行 `python3 scripts/render_client_configs.py` 生成当前环境下的客户端配置片段。
3. 优先复制 `generated/` 目录里的实际产物，不要手写环境相关路径。

### Codex 接入示例

更推荐直接使用 `generated/codex-mcp.toml`。其结构大致如下：

```toml
[mcp_servers.shared-gateway]
command = "/bin/bash"
args = ["-lc", "python3 /absolute/path/to/shared_mcp_gateway/stdio_bridge.py --url http://127.0.0.1:8787/mcp --caller codex"]
enabled = true
```

### OpenCode 接入示例

更推荐直接使用 `generated/opencode-mcp.jsonc`。其结构大致如下：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "shared-gateway": {
      "type": "local",
      "enabled": true,
      "command": [
        "/bin/bash",
        "-lc",
        "python3 /absolute/path/to/shared_mcp_gateway/stdio_bridge.py --url http://127.0.0.1:8787/mcp --caller opencode"
      ]
    }
  }
}
```

### OpenClaw 接入示例

OpenClaw 可以直接走 HTTP MCP，推荐直接使用 `generated/openclaw-mcp.json`：

```json
{
  "mcpServers": {
    "shared-gateway": {
      "url": "http://127.0.0.1:8787/mcp",
      "transport": "streamable-http",
      "connectionTimeoutMs": 10000,
      "disabled": false
    }
  }
}
```

### Claude Code 接入思路

当前项目已经支持通过 `stdio_bridge.py` 给 `claude-code` 注入调用方标识。核心思路是把 bridge 作为本地 stdio MCP 命令：

```bash
python3 /absolute/path/to/shared_mcp_gateway/stdio_bridge.py --url http://127.0.0.1:8787/mcp --caller claude-code
```

如果你的客户端配置体系允许自定义 stdio MCP command，直接复用这条 bridge 命令即可。

## 配置落地建议

为了减少环境问题，建议按下面顺序落地：

1. 先复制模板文件，不要直接修改项目内现成样例。
2. 先确保每个下游 MCP Server 单独可启动。
3. 再把下游服务逐个写入 `registry.toml` 或 `registry.compose.toml`。
4. 启动网关后，先检查 `/healthz`，再执行 `scripts/self_check.py`。
5. 最后执行 `scripts/render_client_configs.py`，同步客户端接入配置。

推荐区分三类文件：

- `registry.toml`：宿主机直跑配置
- `registry.compose.toml`：容器内运行配置
- `templates/*.template.*`：新环境初始化模板

## 常用命令

### 生成客户端配置

```bash
python3 scripts/render_client_configs.py
```

这个脚本会：

- 读取 `registry.toml`
- 统一生成 Codex / OpenCode / OpenClaw 的配置片段
- 避免手工复制 bridge 启动命令时产生配置漂移

生成结果位于：

- `generated/codex-mcp.toml`
- `generated/opencode-mcp.jsonc`
- `generated/openclaw-mcp.json`

### 执行健康检查

```bash
python3 scripts/self_check.py
python3 scripts/self_check.py --json
```

默认会执行两类检查：

- `healthz`：检查网关是否正常暴露、下游是否缺失、熔断器是否打开。
- `gateway_tools`：直接以 MCP 客户端身份连到网关，检查关键工具是否存在，并执行无副作用探活。

### 查看日志

```bash
docker compose logs -f shared-mcp-gateway
```

## 当前接入的共享型 MCP

- `mempalace`
- `mysql-db`
- `obsidian-kb`
- `tencent-cls`

拓扑归位说明见：`/path/to/shared-mcp-gateway/docs/mcp-topology.md`

## 后续建议

如果你要继续扩展这个项目，推荐按下面顺序推进：

1. 先在 `registry.toml` 中新增一个 `[[servers]]`。
2. 本地验证该 MCP 是否能独立启动。
3. 启动网关后检查 `/healthz`。
4. 运行 `scripts/self_check.py` 看关键能力是否正常。
5. 重新执行 `scripts/render_client_configs.py`，同步客户端配置。

---

如果你当前就是要在这个项目里继续补充文档、模板或默认配置，优先维护：

- `README.md`
- `templates/registry.template.toml`
- `templates/registry.compose.template.toml`
- `templates/docker-compose.template.yml`
- `docs/mcp-topology.md`
