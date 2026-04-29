# MCP 拓扑与归位原则

这份文档的目标不是简单列清单，而是回答两个问题：

1. 某个 MCP 为什么应该进 `shared-gateway`？
2. 某个 MCP 为什么应该继续保留本地直连？

判断时建议优先看“运行时依赖和复用范围”，而不是只看名字或部署位置。

## 1. 进 shared-gateway 的 MCP

这些 MCP 属于**共享型能力**，适合被 Codex / OpenCode / Claude Code / OpenClaw 复用，因此统一挂到 `shared-gateway`：

| MCP | namespace | 进入 shared-gateway 的原因 |
| --- | --- | --- |
| mempalace | `mempalace` | 统一知识库/记忆能力，多客户端都要用 |
| mysql-db | `mysql_db` | 通用数据库只读查询能力，适合集中治理 |
| obsidian-kb | `obsidian_kb` | 共享知识库查询/写入接口，跨客户端复用 |
| tencent-cls | `tencent_cls` | 统一日志检索能力，适合集中接入 |
| arthas | `arthas` | JVM 运行时诊断能力，适合把安全开关、日志与熔断统一治理 |
| apifox | `apifox` | 接口文档/项目上下文能力，适合多客户端复用同一套 API 知识入口 |
| chrome-devtools | `chrome_devtools` | 浏览器调试、自动化、网络/性能分析能力，适合统一暴露给多个编码客户端 |

这些 MCP 放进共享网关后，可以统一获得：

- 同一套注册表配置入口
- 同一套结构化日志与 request_id 追踪
- 同一套健康检查与心跳探活
- 同一套最小熔断与失败隔离能力

## 2. 宿主机直跑时也建议进 shared-gateway 的 MCP

| MCP | namespace | 说明 |
| --- | --- | --- |
| OpenSpace | `openspace` | 虽然依赖宿主工作区与技能目录，但它本身支持 stdio MCP，可以通过 env 显式注入宿主上下文后纳入 shared-gateway |
| Chrome DevTools MCP | `chrome_devtools` | 宿主机直跑时可按需启动本机 Chrome；容器版默认通过 `CHROME_DEVTOOLS_BROWSER_URL` 连接宿主机 remote-debugging Chrome |

宿主机直跑时，OpenSpace 推荐这样接入：

- 命令：`/path/to/OpenSpace/.venv/bin/openspace-mcp`
- 环境变量：
  - `OPENSPACE_HOST_SKILL_DIRS=/path/to/openclaw-workspace/skills`
  - `OPENSPACE_WORKSPACE=/path/to/OpenSpace`

这样做之后，Codex / OpenCode / OpenClaw 都只需要连接 `shared-gateway`，不必再各自维护一份 OpenSpace 本地直连配置。

> 例外：如果网关本身运行在 Docker 容器里，需要额外把 OpenSpace 源码、宿主技能目录与 LLM 凭证一起注入容器；
> 只要这些运行条件满足，容器版也可以直接纳管 OpenSpace。

Chrome DevTools MCP 的容器版默认不在镜像中内置 Chrome，而是连接宿主机 Chrome：

- 宿主机 Chrome 启动 remote debugging 端口，例如 `9222`
- Compose 默认注入 `CHROME_DEVTOOLS_BROWSER_URL=http://host.docker.internal:9222`
- 如果要由 MCP 自己启动 Chrome，可清空 `CHROME_DEVTOOLS_BROWSER_URL`，并提供容器内可用的 Chrome 路径与 headless 参数

## 3. 判断规则

### 应该进 shared-gateway
- 多客户端复用
- 与单一宿主工具弱耦合
- 适合统一观测、统一日志、统一熔断
- 下游异常不应拖累客户端整体体验
- 通过 namespace 聚合后不会引起明显歧义
- 即使依赖宿主上下文，也能通过稳定 env / 路径注入方式复现运行条件

### 应该保留本地特例
- 强依赖某个宿主工作区/会话/技能目录
- 生命周期要跟随单一宿主独立运行
- 本地上下文比“统一纳管”更重要
- 混进共享网关后会放大宿主差异或调试复杂度
- 同一能力在不同宿主上的行为明显不一致

## 4. 当前落地结论

- 宿主机直跑场景下，`shared-gateway` 收口共享型 MCP，也收口可通过稳定 env 注入的宿主能力（例如 `OpenSpace`）
- 默认客户端配置优先只保留 `shared-gateway`
- 容器版部署如果缺少 OpenSpace 运行时，可暂时保留例外
- 统一的是**可稳定托管的能力层**

## 5. 新增 MCP 时的归位步骤

当后续要接入一个新的 MCP，建议按下面顺序判断：

1. 先确认这个能力是否会被多个客户端复用。
2. 确认它是否强依赖某个宿主工作区、本地缓存或单会话状态。
3. 单独启动该 MCP，验证它本身是否稳定。
4. 如果判断为共享型能力，再写入 `registry.toml` / `registry.compose.toml`。
5. 启动网关后看 `/healthz`，再执行 `scripts/self_check.py`。
6. 最后用 `scripts/render_client_configs.py` 更新客户端配置。

## 6. 一句话原则

能抽成“共享能力层”的，就放进 `shared-gateway`；
如果只是“依赖宿主上下文，但可以显式注入”，也优先尝试纳入 `shared-gateway`。
