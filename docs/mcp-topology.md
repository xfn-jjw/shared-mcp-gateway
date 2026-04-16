# MCP 拓扑与归位原则

## 1. 进 shared-gateway 的 MCP

这些 MCP 属于**共享型能力**，适合被 Codex / OpenCode / Claude Code / OpenClaw 复用，因此统一挂到 `shared-gateway`：

| MCP | namespace | 进入 shared-gateway 的原因 |
| --- | --- | --- |
| mempalace | `mempalace` | 统一知识库/记忆能力，多客户端都要用 |
| mysql-db | `mysql_db` | 通用数据库只读查询能力，适合集中治理 |
| obsidian-kb | `obsidian_kb` | 共享知识库查询/写入接口，跨客户端复用 |
| tencent-cls | `tencent_cls` | 统一日志检索能力，适合集中接入 |

## 2. 必须保留本地特例的 MCP

| MCP | 保持本地的客户端 | 原因 |
| --- | --- | --- |
| OpenSpace | OpenClaw | 强依赖宿主工作区、宿主技能目录和本地上下文，生命周期也独立于共享网关 |

当前 OpenSpace 仍由本地脚本启动：
- 脚本：`/Users/jervis.jiang/jervis.jiang/OpenSpace/start-openspace-mcp.sh`
- 端点：`http://127.0.0.1:8081/mcp`

## 3. 判断规则

### 应该进 shared-gateway
- 多客户端复用
- 与单一宿主工具弱耦合
- 适合统一观测、统一日志、统一熔断
- 下游异常不应拖累客户端整体体验

### 应该保留本地特例
- 强依赖某个宿主工作区/会话/技能目录
- 生命周期要跟随单一宿主独立运行
- 本地上下文比“统一纳管”更重要
- 混进共享网关后会放大宿主差异或调试复杂度

## 4. 当前落地结论

- `shared-gateway`：只收口共享型 MCP
- `OpenSpace`：继续保留为本地特例
- 不追求“所有 MCP 全统一”
- 统一的是**共享能力层**，不是所有宿主能力
