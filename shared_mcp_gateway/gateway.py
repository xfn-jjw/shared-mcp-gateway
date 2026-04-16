from __future__ import annotations

import argparse
import asyncio
import base64
import contextvars
import json
import logging
import os
import re
import sys
import time
import traceback
import uuid
from collections import Counter
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import anyio
import uvicorn
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp.server import StreamableHTTPASGIApp, StreamableHTTPSessionManager
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.shared.exceptions import McpError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from shared_mcp_gateway.config import Registry, ServerConfig, load_registry
from shared_mcp_gateway.logging_utils import configure_structured_logging, log_event


LOGGER = logging.getLogger("shared_mcp_gateway.gateway")
UNKNOWN_METHOD_MARKER = "Unknown method"
RESOURCE_PREFIX = "/resource/"
HEARTBEAT_INTERVAL_SECONDS = 30 * 60
FAILURE_ALERT_THRESHOLD = 3
CIRCUIT_BREAKER_FAILURE_THRESHOLD = 3
CIRCUIT_BREAKER_OPEN_SECONDS = 60
ANSI_RESET = "\033[0m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_RED = "\033[31m"
TRANSPORT_ERROR_TOKENS = (
    "broken pipe",
    "connection reset",
    "connection refused",
    "connection aborted",
    "connection closed",
    "closed resource",
    "stream closed",
    "endofstream",
    "end of stream",
    "timeout",
    "timed out",
    "network is unreachable",
    "transport",
    "socket",
    "disconnect",
    "remoteprotocolerror",
    "connecterror",
    "readerror",
    "writeerror",
)
REQUEST_CONTEXT: contextvars.ContextVar[RequestContext | None] = contextvars.ContextVar(
    "shared_mcp_gateway_request_context",
    default=None,
)


@dataclass(slots=True)
class RequestContext:
    """绑定到当前 HTTP 请求的轻量上下文，用于打通 caller / request_id 等链路日志。

    这份上下文会通过 `contextvars` 传播到 MCP handler 的更深层调用，
    这样无论日志在多深的位置打印，都能拿到同一批请求维度字段。
    """

    caller: str = "unknown"
    request_id: str = "-"
    client_ip: str = "-"
    user_agent: str = "-"
    method: str = "-"
    path: str = "-"


@dataclass(slots=True)
class GatewayMetrics:
    """网关运行期的轻量指标集合。

    这里只做进程内聚合，不依赖外部 metrics 系统，目的是：
    - 在 `/healthz` 暴露排障信息；
    - 在 heartbeat 日志里快速看到调用画像；
    - 避免为了简单观测引入额外依赖。
    """

    http_requests: int = 0
    tool_calls: int = 0
    resource_reads: int = 0
    prompt_requests: int = 0
    operation_counts: Counter[str] = field(default_factory=Counter)
    tool_counts: Counter[str] = field(default_factory=Counter)
    caller_counts: Counter[str] = field(default_factory=Counter)

    def mark_http_request(self, caller: str) -> None:
        """记录 HTTP 入口流量。"""

        self.http_requests += 1
        self.caller_counts[caller] += 1

    def mark_operation(self, operation: str, caller: str) -> None:
        """记录 MCP 维度的操作调用次数。"""

        self.operation_counts[operation] += 1
        self.caller_counts[caller] += 1

    def mark_tool_call(self, tool_name: str, caller: str) -> None:
        """单独累积工具调用次数，便于识别热点 tool。"""

        self.tool_calls += 1
        self.operation_counts["call_tool"] += 1
        self.tool_counts[tool_name] += 1
        self.caller_counts[caller] += 1


@dataclass(slots=True)
class FailureAlertState:
    """记录同一故障键连续失败的摘要信息，用于告警去噪。"""

    count: int = 0
    first_failure_at: str = ""
    last_failure_at: str = ""
    operation: str = ""
    downstream: str = ""
    caller: str = ""
    request_id: str = ""
    error_code: str | None = None
    error_summary: str | None = None
    traceback_summary: str | None = None


@dataclass(slots=True)
class CircuitBreakerState:
    """按 downstream 维度维护最小熔断状态，避免单点坏掉拖垮整体观感。"""

    state: str = "closed"
    failure_count: int = 0
    opened_at: str | None = None
    open_until: str | None = None
    open_until_ts: float | None = None
    last_failure_at: str | None = None
    last_error_code: str | None = None
    last_error_summary: str | None = None
    last_traceback_summary: str | None = None
    last_operation: str | None = None

    def is_open(self) -> bool:
        """判断熔断器是否仍在打开状态；冷却期结束后自动进入 half-open。"""

        if self.state != "open":
            return False
        if self.open_until_ts is not None and time.time() >= self.open_until_ts:
            # 进入 half-open 后允许下一次真实请求穿透，以便自动探测恢复情况。
            self.state = "half-open"
            return False
        return True

    def remaining_seconds(self) -> int:
        """返回剩余冷却时间，主要用于日志与错误提示。"""

        if self.open_until_ts is None:
            return 0
        return max(0, int(self.open_until_ts - time.time()))

    def snapshot(self) -> dict[str, Any]:
        """导出面向 `/healthz` 的熔断器快照。"""

        return {
            "state": self.state,
            "failureCount": self.failure_count,
            "openedAt": self.opened_at,
            "openUntil": self.open_until,
            "remainingSeconds": self.remaining_seconds(),
            "lastFailureAt": self.last_failure_at,
            "lastErrorCode": self.last_error_code,
            "lastErrorSummary": self.last_error_summary,
            "lastTracebackSummary": self.last_traceback_summary,
            "lastOperation": self.last_operation,
        }


@dataclass(slots=True)
class DownstreamConnection:
    """单个下游 MCP server 的连接与目录缓存。

    每个下游会维护独立的 session / lock / catalog：
    - session 负责真正的 MCP 双向通信；
    - lock 保证一个 session 上的请求顺序可控；
    - tools/resources/prompts 等目录缓存用于对外构建统一索引。
    """

    config: ServerConfig
    session: ClientSession
    stack: AsyncExitStack
    server_info: types.Implementation | None = None
    instructions: str | None = None
    lock: anyio.Lock = field(default_factory=anyio.Lock)
    tools: dict[str, types.Tool] = field(default_factory=dict)
    resources: dict[str, types.Resource] = field(default_factory=dict)
    resource_templates: dict[str, types.ResourceTemplate] = field(default_factory=dict)
    prompts: dict[str, types.Prompt] = field(default_factory=dict)

    @classmethod
    async def open(cls, config: ServerConfig) -> "DownstreamConnection":
        """启动 stdio 子进程、完成 MCP initialize，并拉取首份目录缓存。"""

        params = _build_stdio_server_parameters(config)
        stack = AsyncExitStack()
        try:
            # 通过 AsyncExitStack 统一托管流与 session，出错时可以完整回滚。
            read_stream, write_stream = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            init_result = await session.initialize()
            connection = cls(
                config=config,
                session=session,
                stack=stack,
                server_info=init_result.serverInfo,
                instructions=init_result.instructions,
            )
            await connection.refresh_catalog()
            return connection
        except BaseException:
            await _safe_aclose(stack, f"downstream:{config.key}")
            raise

    async def refresh_catalog(self) -> None:
        """刷新下游工具/资源/prompt 目录。

        某些 MCP 能力是可选的，因此资源和 prompt 相关接口会用 `_optional_request`
        做兼容探测，避免把“方法未实现”误判成连接不可用。
        """

        async with self.lock:
            tools_result = await self.session.list_tools()
            self.tools = {tool.name: tool for tool in tools_result.tools}

            resources_result = await _optional_request(
                self.config.key,
                "resources/list",
                self.session.list_resources,
            )
            self.resources = {
                resource.uri.unicode_string(): resource
                for resource in (resources_result.resources if resources_result else [])
            }

            templates_result = await _optional_request(
                self.config.key,
                "resources/templates/list",
                self.session.list_resource_templates,
            )
            self.resource_templates = {
                template.uriTemplate: template
                for template in (templates_result.resourceTemplates if templates_result else [])
            }

            prompts_result = await _optional_request(
                self.config.key,
                "prompts/list",
                self.session.list_prompts,
            )
            self.prompts = {prompt.name: prompt for prompt in (prompts_result.prompts if prompts_result else [])}

    async def probe(self) -> dict[str, Any]:
        """执行一次轻量探活；只拉工具列表，尽量降低心跳成本。"""

        started = time.perf_counter()
        async with self.lock:
            tools_result = await self.session.list_tools()
        return {
            "ok": True,
            "toolCount": len(tools_result.tools),
            "durationMs": round((time.perf_counter() - started) * 1000, 1),
        }

    async def close(self) -> None:
        """关闭下游连接。"""

        await _safe_aclose(self.stack, f"downstream:{self.config.key}")

    def summary(self) -> dict[str, Any]:
        """输出面向健康检查的下游摘要。"""

        return {
            "namespace": self.config.namespace,
            "serverKey": self.config.key,
            "serverInfo": self.server_info.model_dump(mode="json") if self.server_info else None,
            "catalog": {
                "tools": len(self.tools),
                "resources": len(self.resources),
                "resourceTemplates": len(self.resource_templates),
                "prompts": len(self.prompts),
            },
        }


class SharedMcpGateway:
    """聚合多个 stdio MCP，下游异常时按 server 维度隔离，并对外暴露统一 HTTP MCP。"""

    def __init__(self, registry: Registry):
        """初始化网关核心状态，包括连接池、统一索引、失败统计与熔断器。"""

        self.registry = registry
        self.separator = registry.gateway.namespace_separator or "."
        self.connections: dict[str, DownstreamConnection] = {}
        self.failed_servers: dict[str, str] = {}
        self.failure_streaks: dict[str, FailureAlertState] = {}
        self.circuit_breakers: dict[str, CircuitBreakerState] = {
            server.key: CircuitBreakerState() for server in registry.enabled_servers
        }
        self.metrics = GatewayMetrics()
        self.started_at = time.time()
        self.last_heartbeat_at: str | None = None
        self.last_probe_snapshot: dict[str, Any] = {}
        self._tool_index: dict[str, tuple[DownstreamConnection, str, types.Tool]] = {}
        self._resource_index: dict[str, tuple[DownstreamConnection, str, types.Resource]] = {}
        self._template_index: dict[str, tuple[DownstreamConnection, str, types.ResourceTemplate]] = {}
        self._prompt_index: dict[str, tuple[DownstreamConnection, str, types.Prompt]] = {}

    async def start(self) -> None:
        """按配置依次连接所有启用的下游，并建立统一索引。"""

        log_event(
            LOGGER,
            logging.INFO,
            "gateway_starting",
            downstream_count=len(self.registry.enabled_servers),
            listen=self.registry.listen.url,
        )
        for server_config in self.registry.enabled_servers:
            try:
                connection = await DownstreamConnection.open(server_config)
            except Exception as exc:
                # 启动阶段允许单个下游失败，只要至少还有一个可用就继续对外提供服务。
                error_code = _extract_error_code(exc)
                error_summary = _summarize_exception(exc)
                traceback_summary = _traceback_summary_from_exception(exc)
                self.failed_servers[server_config.key] = f"{type(exc).__name__}: {exc}"
                self._record_transport_failure(
                    server_config.key,
                    operation="startup_connect",
                    error_code=error_code,
                    error_summary=error_summary,
                    traceback_summary=traceback_summary,
                )
                log_event(
                    LOGGER,
                    logging.ERROR,
                    "downstream_connect_failed",
                    downstream=server_config.key,
                    namespace=server_config.namespace,
                    error_code=error_code,
                    error_summary=error_summary,
                    traceback_summary=traceback_summary,
                )
                continue

            self.connections[server_config.namespace] = connection
            self._record_transport_success(server_config.key, source="startup_connect")
            log_event(
                LOGGER,
                logging.INFO,
                "downstream_connected",
                downstream=server_config.key,
                namespace=server_config.namespace,
                tools=len(connection.tools),
                resources=len(connection.resources),
                resource_templates=len(connection.resource_templates),
                prompts=len(connection.prompts),
            )

        self._rebuild_indexes()
        if not self.connections:
            raise RuntimeError("No downstream MCP server could be connected")

    async def close(self) -> None:
        """关闭所有已建立的下游连接并清空索引缓存。"""

        for namespace, connection in list(self.connections.items()):
            log_event(
                LOGGER,
                logging.INFO,
                "downstream_closing",
                namespace=namespace,
                downstream=connection.config.key,
            )
            await connection.close()
        self.connections.clear()
        self._tool_index.clear()
        self._resource_index.clear()
        self._template_index.clear()
        self._prompt_index.clear()

    @asynccontextmanager
    async def run(self):
        """把 start / close 包装为可复用的异步生命周期上下文。"""

        await self.start()
        try:
            yield self
        finally:
            await self.close()

    def _rebuild_indexes(self) -> None:
        """根据当前 catalog 生成对外统一命名空间索引。

        网关对外暴露的工具名、资源 URI、prompt 名称都要带 namespace，
        否则多个下游出现同名对象时会互相覆盖。
        """

        self._tool_index.clear()
        self._resource_index.clear()
        self._template_index.clear()
        self._prompt_index.clear()

        for namespace, connection in self.connections.items():
            for original_name, tool in connection.tools.items():
                self._tool_index[self._namespaced_name(namespace, original_name)] = (connection, original_name, tool)

            for original_uri, resource in connection.resources.items():
                self._resource_index[self._public_resource_uri(namespace, original_uri)] = (
                    connection,
                    original_uri,
                    resource,
                )

            for original_template, template in connection.resource_templates.items():
                self._template_index[self._public_resource_uri(namespace, original_template)] = (
                    connection,
                    original_template,
                    template,
                )

            for original_name, prompt in connection.prompts.items():
                self._prompt_index[self._namespaced_name(namespace, original_name)] = (
                    connection,
                    original_name,
                    prompt,
                )

    def _mark_success(self, key: str) -> None:
        """故障键恢复成功时清理 streak，并在跨阈值恢复时打印恢复日志。"""

        state = self.failure_streaks.pop(key, None)
        if state and state.count >= FAILURE_ALERT_THRESHOLD:
            log_event(
                LOGGER,
                logging.INFO,
                "failure_alert_recovered",
                key=key,
                streak=state.count,
                operation=state.operation,
                downstream=state.downstream,
                last_error_code=state.error_code,
                last_error_summary=state.error_summary,
            )

    def _mark_failure(
        self,
        key: str,
        *,
        operation: str,
        downstream: str,
        caller: str,
        request_id: str,
        error_code: str | None,
        error_summary: str | None,
        traceback_summary: str | None,
    ) -> FailureAlertState:
        """记录一次业务失败，并在达到告警阈值时做聚合告警。"""

        now = _now_string()
        state = self.failure_streaks.get(key)
        if state is None:
            state = FailureAlertState(
                count=1,
                first_failure_at=now,
            )
            self.failure_streaks[key] = state
        else:
            state.count += 1

        state.last_failure_at = now
        state.operation = operation
        state.downstream = downstream
        state.caller = caller
        state.request_id = request_id
        state.error_code = error_code
        state.error_summary = error_summary
        state.traceback_summary = traceback_summary

        if state.count >= FAILURE_ALERT_THRESHOLD and state.count % FAILURE_ALERT_THRESHOLD == 0:
            log_event(
                LOGGER,
                logging.ERROR,
                "failure_alert_threshold_reached",
                key=key,
                streak=state.count,
                threshold=FAILURE_ALERT_THRESHOLD,
                operation=state.operation,
                downstream=state.downstream,
                caller=state.caller,
                request_id=state.request_id,
                first_failure_at=state.first_failure_at,
                last_failure_at=state.last_failure_at,
                error_code=state.error_code,
                error_summary=state.error_summary,
                traceback_summary=state.traceback_summary,
            )
        return state

    def _record_transport_failure(
        self,
        server_key: str,
        *,
        operation: str,
        error_code: str | None,
        error_summary: str | None,
        traceback_summary: str | None,
    ) -> CircuitBreakerState:
        """只把疑似 transport/runtime 失败计入 breaker，避免用户参数错误把整条链路误伤。"""

        state = self.circuit_breakers.setdefault(server_key, CircuitBreakerState())
        state.failure_count += 1
        state.last_failure_at = _now_string()
        state.last_error_code = error_code
        state.last_error_summary = error_summary
        state.last_traceback_summary = traceback_summary
        state.last_operation = operation

        if state.failure_count >= CIRCUIT_BREAKER_FAILURE_THRESHOLD:
            state.state = "open"
            state.opened_at = state.last_failure_at
            state.open_until_ts = time.time() + CIRCUIT_BREAKER_OPEN_SECONDS
            state.open_until = time.strftime("%Y-%m-%d %H:%M:%S%z", time.localtime(state.open_until_ts))
            log_event(
                LOGGER,
                logging.ERROR,
                "circuit_breaker_opened",
                downstream=server_key,
                operation=operation,
                failure_count=state.failure_count,
                threshold=CIRCUIT_BREAKER_FAILURE_THRESHOLD,
                cooldown_seconds=CIRCUIT_BREAKER_OPEN_SECONDS,
                open_until=state.open_until,
                error_code=error_code,
                error_summary=error_summary,
                traceback_summary=traceback_summary,
            )
        else:
            log_event(
                LOGGER,
                logging.WARNING,
                "circuit_breaker_failure",
                downstream=server_key,
                operation=operation,
                failure_count=state.failure_count,
                threshold=CIRCUIT_BREAKER_FAILURE_THRESHOLD,
                error_code=error_code,
                error_summary=error_summary,
                traceback_summary=traceback_summary,
            )
        return state

    def _record_transport_success(self, server_key: str, *, source: str) -> None:
        """当下游恢复可用时重置熔断器状态。"""

        state = self.circuit_breakers.setdefault(server_key, CircuitBreakerState())
        previous_state = state.state
        previous_failures = state.failure_count
        if previous_state == "closed" and previous_failures == 0:
            return

        self.circuit_breakers[server_key] = CircuitBreakerState()
        log_event(
            LOGGER,
            logging.INFO,
            "circuit_breaker_recovered",
            downstream=server_key,
            source=source,
            previous_state=previous_state,
            previous_failure_count=previous_failures,
        )

    def _ensure_breaker_allows(
        self,
        connection: DownstreamConnection,
        *,
        operation: str,
        target: str,
        caller: str,
        request_id: str,
    ) -> tuple[bool, CircuitBreakerState]:
        """真正转发前先看 breaker，单个 downstream 挂掉时直接快速失败，不拖累整体。"""

        state = self.circuit_breakers.setdefault(connection.config.key, CircuitBreakerState())
        if not state.is_open():
            return True, state

        # 熔断打开时不再尝试真实请求，避免每次都把请求压到已经异常的下游。
        log_event(
            LOGGER,
            logging.WARNING,
            "circuit_breaker_reject",
            downstream=connection.config.key,
            namespace=connection.config.namespace,
            operation=operation,
            target=target,
            caller=caller,
            request_id=request_id,
            open_until=state.open_until,
            remaining_seconds=state.remaining_seconds(),
            failure_count=state.failure_count,
            last_error_code=state.last_error_code,
            last_error_summary=state.last_error_summary,
        )
        return False, state

    def _handle_operation_exception(
        self,
        connection: DownstreamConnection,
        *,
        failure_key: str,
        operation: str,
        caller: str,
        request_id: str,
        target: str,
        exc: Exception,
    ) -> None:
        """统一处理转发过程中的异常：记 streak、打熔断、打印结构化日志。"""

        error_code = _extract_error_code(exc)
        error_summary = _summarize_exception(exc)
        traceback_summary = _traceback_summary_from_exception(exc)
        self._mark_failure(
            failure_key,
            operation=operation,
            downstream=connection.config.key,
            caller=caller,
            request_id=request_id,
            error_code=error_code,
            error_summary=error_summary,
            traceback_summary=traceback_summary,
        )
        if _should_trip_circuit_breaker(exc):
            self._record_transport_failure(
                connection.config.key,
                operation=operation,
                error_code=error_code,
                error_summary=error_summary,
                traceback_summary=traceback_summary,
            )
        log_event(
            LOGGER,
            logging.ERROR,
            "downstream_call_failed",
            downstream=connection.config.key,
            namespace=connection.config.namespace,
            operation=operation,
            target=target,
            caller=caller,
            request_id=request_id,
            error_code=error_code,
            error_summary=error_summary,
            traceback_summary=traceback_summary,
        )

    def _namespaced_name(self, namespace: str, original_name: str) -> str:
        """把下游原始名字映射成网关侧全局唯一名字。"""

        return f"{namespace}{self.separator}{original_name}"

    def _public_resource_uri(self, namespace: str, original_uri: str) -> str:
        """把下游资源 URI 转成网关对外暴露的统一 URI。"""

        encoded = quote(original_uri, safe="{}")
        return f"shared-mcp://{namespace}{RESOURCE_PREFIX}{encoded}"

    def _decode_public_resource_uri(self, public_uri: str) -> tuple[DownstreamConnection, str]:
        """把网关侧 URI 反解为目标下游连接和原始 URI。"""

        if not public_uri.startswith("shared-mcp://") or RESOURCE_PREFIX not in public_uri:
            raise ValueError(f"Unsupported resource URI: {public_uri}")

        prefix, encoded = public_uri.split(RESOURCE_PREFIX, 1)
        namespace = prefix.removeprefix("shared-mcp://")
        connection = self.connections.get(namespace)
        if connection is None:
            raise ValueError(f"Unknown resource namespace: {namespace}")
        return connection, unquote(encoded)

    def list_tools(self) -> list[types.Tool]:
        """列出聚合后的工具列表，并把名称、标题与 meta 注入 namespace。"""

        context = _get_request_context()
        self.metrics.mark_operation("list_tools", context.caller)
        log_event(
            LOGGER,
            logging.INFO,
            "mcp_list_tools",
            caller=context.caller,
            request_id=context.request_id,
            total=len(self._tool_index),
        )

        tools: list[types.Tool] = []
        for public_name, (connection, original_name, tool) in sorted(self._tool_index.items()):
            tools.append(
                tool.model_copy(
                    update={
                        "name": public_name,
                        "title": _prefix_title(connection.config.namespace, tool.title),
                        "meta": _with_gateway_meta(tool.meta, connection.config.namespace, original_name=original_name),
                    }
                )
            )
        return tools

    async def call_tool(self, public_name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        """按公开工具名路由到具体下游并执行调用。"""

        if public_name not in self._tool_index:
            raise ValueError(f"Unknown tool: {public_name}")

        context = _get_request_context()
        self.metrics.mark_tool_call(public_name, context.caller)
        connection, original_name, _tool = self._tool_index[public_name]
        allowed, breaker = self._ensure_breaker_allows(
            connection,
            operation="call_tool",
            target=public_name,
            caller=context.caller,
            request_id=context.request_id,
        )
        if not allowed:
            # 对 tool 调用返回 MCP 级错误结果，而不是直接抛异常，便于客户端统一消费。
            return _build_circuit_open_call_tool_result(connection, breaker)

        argument_summary = _summarize_arguments(arguments)
        started = time.perf_counter()
        log_event(
            LOGGER,
            logging.INFO,
            "mcp_call_tool_start",
            caller=context.caller,
            request_id=context.request_id,
            tool=public_name,
            downstream=connection.config.key,
            original=original_name,
            args=argument_summary,
        )
        try:
            async with connection.lock:
                # 同一个 session 上串行执行，避免并发 call_tool 破坏底层流状态。
                result = await connection.session.call_tool(original_name, arguments)
        except Exception as exc:
            self._handle_operation_exception(
                connection,
                failure_key=f"call_tool:{public_name}",
                operation="call_tool",
                caller=context.caller,
                request_id=context.request_id,
                target=public_name,
                exc=exc,
            )
            raise

        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        result_summary = _summarize_tool_result(result)
        error_details = _extract_tool_error_details(result)
        if getattr(result, "isError", False):
            self._mark_failure(
                f"call_tool:{public_name}",
                operation="call_tool",
                downstream=connection.config.key,
                caller=context.caller,
                request_id=context.request_id,
                error_code=error_details["errorCode"],
                error_summary=error_details["errorSummary"],
                traceback_summary=error_details["tracebackSummary"],
            )
            log_event(
                LOGGER,
                logging.ERROR,
                "mcp_call_tool_error_result",
                caller=context.caller,
                request_id=context.request_id,
                tool=public_name,
                downstream=connection.config.key,
                duration_ms=duration_ms,
                error_code=error_details["errorCode"],
                error_summary=error_details["errorSummary"],
                traceback_summary=error_details["tracebackSummary"],
                result=result_summary,
            )
        else:
            self._mark_success(f"call_tool:{public_name}")
            self._record_transport_success(connection.config.key, source="call_tool")
            log_event(
                LOGGER,
                logging.INFO,
                "mcp_call_tool_done",
                caller=context.caller,
                request_id=context.request_id,
                tool=public_name,
                downstream=connection.config.key,
                duration_ms=duration_ms,
                result=result_summary,
            )
        return result

    def list_resources(self) -> list[types.Resource]:
        """列出聚合后的资源清单。"""

        context = _get_request_context()
        self.metrics.mark_operation("list_resources", context.caller)
        log_event(
            LOGGER,
            logging.INFO,
            "mcp_list_resources",
            caller=context.caller,
            request_id=context.request_id,
            total=len(self._resource_index),
        )

        resources: list[types.Resource] = []
        for public_uri, (connection, original_uri, resource) in sorted(self._resource_index.items()):
            resources.append(
                resource.model_copy(
                    update={
                        "uri": public_uri,
                        "name": self._namespaced_name(connection.config.namespace, resource.name or original_uri),
                        "title": _prefix_title(connection.config.namespace, resource.title),
                        "meta": _with_gateway_meta(resource.meta, connection.config.namespace, original_uri=original_uri),
                    }
                )
            )
        return resources

    async def read_resource(self, public_uri: str) -> list[ReadResourceContents]:
        """按公开 URI 读取下游资源。"""

        context = _get_request_context()
        connection, original_uri = self._decode_public_resource_uri(public_uri)
        self.metrics.resource_reads += 1
        self.metrics.mark_operation("read_resource", context.caller)
        allowed, breaker = self._ensure_breaker_allows(
            connection,
            operation="read_resource",
            target=public_uri,
            caller=context.caller,
            request_id=context.request_id,
        )
        if not allowed:
            raise RuntimeError(_circuit_open_message(connection, breaker))

        started = time.perf_counter()
        log_event(
            LOGGER,
            logging.INFO,
            "mcp_read_resource_start",
            caller=context.caller,
            request_id=context.request_id,
            uri=public_uri,
            downstream=connection.config.key,
        )
        try:
            async with connection.lock:
                result = await connection.session.read_resource(original_uri)
        except Exception as exc:
            self._handle_operation_exception(
                connection,
                failure_key=f"read_resource:{connection.config.namespace}",
                operation="read_resource",
                caller=context.caller,
                request_id=context.request_id,
                target=public_uri,
                exc=exc,
            )
            raise

        self._mark_success(f"read_resource:{connection.config.namespace}")
        self._record_transport_success(connection.config.key, source="read_resource")
        log_event(
            LOGGER,
            logging.INFO,
            "mcp_read_resource_done",
            caller=context.caller,
            request_id=context.request_id,
            uri=public_uri,
            downstream=connection.config.key,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            items=len(result.contents),
        )
        return [_to_helper_content(item) for item in result.contents]

    def list_resource_templates(self) -> list[types.ResourceTemplate]:
        """列出聚合后的资源模板。"""

        context = _get_request_context()
        self.metrics.mark_operation("list_resource_templates", context.caller)
        log_event(
            LOGGER,
            logging.INFO,
            "mcp_list_resource_templates",
            caller=context.caller,
            request_id=context.request_id,
            total=len(self._template_index),
        )

        templates: list[types.ResourceTemplate] = []
        for public_uri_template, (connection, original_template, template) in sorted(self._template_index.items()):
            templates.append(
                template.model_copy(
                    update={
                        "uriTemplate": public_uri_template,
                        "name": self._namespaced_name(connection.config.namespace, template.name),
                        "title": _prefix_title(connection.config.namespace, template.title),
                        "meta": _with_gateway_meta(
                            template.meta,
                            connection.config.namespace,
                            original_uri=original_template,
                        ),
                    }
                )
            )
        return templates

    def list_prompts(self) -> list[types.Prompt]:
        """列出聚合后的 prompt。"""

        context = _get_request_context()
        self.metrics.mark_operation("list_prompts", context.caller)
        log_event(
            LOGGER,
            logging.INFO,
            "mcp_list_prompts",
            caller=context.caller,
            request_id=context.request_id,
            total=len(self._prompt_index),
        )

        prompts: list[types.Prompt] = []
        for public_name, (connection, original_name, prompt) in sorted(self._prompt_index.items()):
            prompts.append(
                prompt.model_copy(
                    update={
                        "name": public_name,
                        "title": _prefix_title(connection.config.namespace, prompt.title),
                        "meta": _with_gateway_meta(prompt.meta, connection.config.namespace, original_name=original_name),
                    }
                )
            )
        return prompts

    async def get_prompt(self, public_name: str, arguments: dict[str, str] | None) -> types.GetPromptResult:
        """按公开 prompt 名获取 prompt 内容。"""

        if public_name not in self._prompt_index:
            raise ValueError(f"Unknown prompt: {public_name}")

        context = _get_request_context()
        self.metrics.prompt_requests += 1
        self.metrics.mark_operation("get_prompt", context.caller)
        connection, original_name, _prompt = self._prompt_index[public_name]
        allowed, breaker = self._ensure_breaker_allows(
            connection,
            operation="get_prompt",
            target=public_name,
            caller=context.caller,
            request_id=context.request_id,
        )
        if not allowed:
            raise RuntimeError(_circuit_open_message(connection, breaker))

        started = time.perf_counter()
        log_event(
            LOGGER,
            logging.INFO,
            "mcp_get_prompt_start",
            caller=context.caller,
            request_id=context.request_id,
            prompt=public_name,
            downstream=connection.config.key,
            args=_summarize_arguments(arguments or {}),
        )
        try:
            async with connection.lock:
                result = await connection.session.get_prompt(original_name, arguments)
        except Exception as exc:
            self._handle_operation_exception(
                connection,
                failure_key=f"get_prompt:{public_name}",
                operation="get_prompt",
                caller=context.caller,
                request_id=context.request_id,
                target=public_name,
                exc=exc,
            )
            raise

        self._mark_success(f"get_prompt:{public_name}")
        self._record_transport_success(connection.config.key, source="get_prompt")
        log_event(
            LOGGER,
            logging.INFO,
            "mcp_get_prompt_done",
            caller=context.caller,
            request_id=context.request_id,
            prompt=public_name,
            downstream=connection.config.key,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            messages=len(result.messages),
        )
        return result.model_copy(
            update={
                "description": _prefix_description(connection.config.namespace, result.description),
            }
        )

    async def heartbeat_loop(self, interval_seconds: int = HEARTBEAT_INTERVAL_SECONDS) -> None:
        """后台定时打印探活结果与运行摘要。"""

        await self.log_heartbeat(reason="startup")
        while True:
            await asyncio.sleep(interval_seconds)
            await self.log_heartbeat(reason="interval")

    async def log_heartbeat(self, reason: str) -> None:
        """收集每个下游的探活快照，并输出统一 heartbeat 日志。"""

        probe_snapshot: dict[str, Any] = {}
        for namespace, connection in sorted(self.connections.items()):
            try:
                probe_snapshot[namespace] = await connection.probe()
                self._mark_success(f"heartbeat_probe:{namespace}")
                self._record_transport_success(connection.config.key, source="heartbeat_probe")
            except Exception as exc:  # noqa: BLE001
                # 心跳失败不影响主线程，但会把失败信息沉淀到 healthz 与 breaker 状态里。
                error_code = _extract_error_code(exc)
                error_summary = _summarize_exception(exc)
                traceback_summary = _traceback_summary_from_exception(exc)
                probe_snapshot[namespace] = {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "errorCode": error_code,
                    "errorSummary": error_summary,
                }
                self._mark_failure(
                    f"heartbeat_probe:{namespace}",
                    operation="heartbeat_probe",
                    downstream=connection.config.key,
                    caller="gateway",
                    request_id="-",
                    error_code=error_code,
                    error_summary=error_summary,
                    traceback_summary=traceback_summary,
                )
                if _should_trip_circuit_breaker(exc):
                    self._record_transport_failure(
                        connection.config.key,
                        operation="heartbeat_probe",
                        error_code=error_code,
                        error_summary=error_summary,
                        traceback_summary=traceback_summary,
                    )
                log_event(
                    LOGGER,
                    logging.ERROR,
                    "heartbeat_probe_failed",
                    namespace=namespace,
                    downstream=connection.config.key,
                    error=probe_snapshot[namespace]["error"],
                    error_code=error_code,
                    error_summary=error_summary,
                    traceback_summary=traceback_summary,
                )

        self.last_probe_snapshot = probe_snapshot
        self.last_heartbeat_at = _now_string()
        status_plain, status_ansi = self._build_probe_status_line(probe_snapshot)
        log_event(
            LOGGER,
            logging.INFO,
            "gateway_heartbeat",
            reason=reason,
            uptime_s=int(time.time() - self.started_at),
            status=status_plain,
            status_ansi=status_ansi,
            connected=sorted(self.connections.keys()),
            failed=self.failed_servers,
            counters={
                "httpRequests": self.metrics.http_requests,
                "toolCalls": self.metrics.tool_calls,
                "resourceReads": self.metrics.resource_reads,
                "promptRequests": self.metrics.prompt_requests,
            },
            top_callers=_format_top_counter(self.metrics.caller_counts),
            top_tools=_format_top_counter(self.metrics.tool_counts),
            probes=probe_snapshot,
        )

    def _build_probe_status_line(self, probe_snapshot: dict[str, Any]) -> tuple[str, str]:
        """把探活结果渲染成便于人眼扫描的单行状态文本。"""

        plain_segments: list[str] = []
        ansi_segments: list[str] = []
        all_namespaces = sorted(set(self.connections.keys()) | set(probe_snapshot.keys()))
        for namespace in all_namespaces:
            connection = self.connections.get(namespace)
            downstream = connection.config.key if connection else namespace
            probe = probe_snapshot.get(namespace)
            state = self._probe_display_state(namespace, downstream, probe)
            color = {
                "UP": ANSI_GREEN,
                "WARN": ANSI_YELLOW,
                "DOWN": ANSI_RED,
            }[state]
            detail = self._probe_display_detail(namespace, downstream, probe)
            plain_segments.append(f"{namespace}={state}({detail})")
            ansi_segments.append(f"{namespace}={color}{state}{ANSI_RESET}({detail})")
        return " ".join(plain_segments), " ".join(ansi_segments)

    def _probe_display_state(self, namespace: str, downstream: str, probe: dict[str, Any] | None) -> str:
        """根据 breaker、探活结果和历史告警决定展示态。"""

        breaker = self.circuit_breakers.get(downstream)
        if breaker and breaker.is_open():
            return "DOWN"
        if not probe or not probe.get("ok"):
            return "DOWN"
        if self._has_active_downstream_warning(namespace, downstream):
            return "WARN"
        return "UP"

    def _probe_display_detail(self, namespace: str, downstream: str, probe: dict[str, Any] | None) -> str:
        """补充状态详情，方便在 heartbeat 单行日志里直接排障。"""

        breaker = self.circuit_breakers.get(downstream)
        if breaker and breaker.is_open():
            return f"breaker=open,remain={breaker.remaining_seconds()}s"
        if not probe:
            return "no-probe"
        if not probe.get("ok"):
            return _truncate_text(str(probe.get("error") or "probe-failed"), 120) or "probe-failed"
        warning_count = self._count_active_downstream_warnings(namespace, downstream)
        tool_count = probe.get("toolCount", "?")
        duration_ms = probe.get("durationMs", "?")
        if warning_count > 0:
            return f"tools={tool_count},probeMs={duration_ms},warnings={warning_count}"
        return f"tools={tool_count},probeMs={duration_ms}"

    def _has_active_downstream_warning(self, namespace: str, downstream: str) -> bool:
        """判断某个下游当前是否存在尚未恢复的告警痕迹。"""

        return self._count_active_downstream_warnings(namespace, downstream) > 0

    def _count_active_downstream_warnings(self, namespace: str, downstream: str | None = None) -> int:
        """统计某个 namespace / downstream 当前挂着多少未恢复警告。"""

        count = 0
        for key, state in self.failure_streaks.items():
            if state.count <= 0:
                continue
            if key == f"heartbeat_probe:{namespace}":
                continue
            if downstream is not None and state.downstream == downstream:
                count += 1
            elif downstream is None and (state.downstream == namespace or key.startswith(f"{namespace}:")):
                count += 1
        if downstream and self.circuit_breakers.get(downstream) and self.circuit_breakers[downstream].state != "closed":
            count += 1
        return count

    def health_snapshot(self) -> dict[str, Any]:
        """输出 `/healthz` 使用的完整健康快照。"""

        return {
            "gateway": {
                "name": self.registry.gateway.name,
                "listen": self.registry.listen.url,
                "configuredServers": [server.key for server in self.registry.enabled_servers],
                "connectedServers": sorted(self.connections.keys()),
                "failedServers": self.failed_servers,
                "uptimeSeconds": int(time.time() - self.started_at),
                "lastHeartbeatAt": self.last_heartbeat_at,
            },
            "servers": {namespace: connection.summary() for namespace, connection in self.connections.items()},
            "indexes": {
                "tools": len(self._tool_index),
                "resources": len(self._resource_index),
                "resourceTemplates": len(self._template_index),
                "prompts": len(self._prompt_index),
            },
            "metrics": {
                "httpRequests": self.metrics.http_requests,
                "toolCalls": self.metrics.tool_calls,
                "resourceReads": self.metrics.resource_reads,
                "promptRequests": self.metrics.prompt_requests,
                "operations": dict(self.metrics.operation_counts),
                "topCallers": _format_top_counter(self.metrics.caller_counts),
                "topTools": _format_top_counter(self.metrics.tool_counts),
            },
            "failureAlertThreshold": FAILURE_ALERT_THRESHOLD,
            "activeFailureStreaks": {
                key: {
                    "count": state.count,
                    "firstFailureAt": state.first_failure_at,
                    "lastFailureAt": state.last_failure_at,
                    "operation": state.operation,
                    "downstream": state.downstream,
                    "caller": state.caller,
                    "requestId": state.request_id,
                    "errorCode": state.error_code,
                    "errorSummary": state.error_summary,
                    "tracebackSummary": state.traceback_summary,
                }
                for key, state in self.failure_streaks.items()
            },
            "circuitBreakers": {
                server_key: state.snapshot() for server_key, state in sorted(self.circuit_breakers.items())
            },
            "topology": {
                "sharedGatewayServers": [
                    {"key": server.key, "namespace": server.namespace} for server in self.registry.enabled_servers
                ],
                "localExceptions": self.registry.local_exceptions,
            },
            "lastProbeSnapshot": self.last_probe_snapshot,
        }


async def _optional_request(server_key: str, method_name: str, func):
    """执行可选 MCP 方法。

    某些下游只实现 tools，不实现 resources / prompts；遇到 `Unknown method`
    时返回 `None` 表示“能力缺失但连接正常”，其余异常仍按真实故障处理。
    """

    try:
        return await func()
    except McpError as exc:
        if UNKNOWN_METHOD_MARKER in str(exc):
            log_event(
                LOGGER,
                logging.INFO,
                "downstream_optional_method_missing",
                downstream=server_key,
                method=method_name,
            )
            return None
        log_event(
            LOGGER,
            logging.ERROR,
            "downstream_optional_request_failed",
            downstream=server_key,
            method=method_name,
            error_code=_extract_error_code(exc),
            error_summary=_summarize_exception(exc),
            traceback_summary=_traceback_summary_from_exception(exc),
        )
        raise
    except Exception as exc:  # noqa: BLE001
        log_event(
            LOGGER,
            logging.ERROR,
            "downstream_optional_request_failed",
            downstream=server_key,
            method=method_name,
            error_code=_extract_error_code(exc),
            error_summary=_summarize_exception(exc),
            traceback_summary=_traceback_summary_from_exception(exc),
        )
        raise


async def _safe_aclose(stack: AsyncExitStack, label: str) -> None:
    """安全关闭异步资源；清理失败只记 debug 日志，不反向覆盖主错误。"""

    try:
        await stack.aclose()
    except BaseException as exc:  # noqa: BLE001
        log_event(
            LOGGER,
            logging.DEBUG,
            "cleanup_ignored_error",
            label=label,
            error_summary=_summarize_exception(exc),
        )


def _build_stdio_server_parameters(config: ServerConfig) -> StdioServerParameters:
    """所有下游统一走 supervisor，标准化 stderr 日志，同时不影响 stdio MCP 协议。"""

    supervisor = project_root / "shared_mcp_gateway" / "stdio_supervisor.py"
    return StdioServerParameters(
        command=sys.executable,
        args=[
            str(supervisor),
            "--downstream",
            config.key,
            "--command",
            config.command,
            "--args-json",
            json.dumps(config.args, ensure_ascii=False),
            "--env-json",
            json.dumps(config.env or {}, ensure_ascii=False),
        ],
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                filter(None, [str(project_root), os.environ.get("PYTHONPATH", "")])
            ),
        },
    )


def _with_gateway_meta(
    original_meta: dict[str, Any] | None,
    namespace: str,
    *,
    original_name: str | None = None,
    original_uri: str | None = None,
) -> dict[str, Any]:
    """在原始 meta 上补充网关侧追踪字段，便于客户端反查来源。"""

    meta = dict(original_meta or {})
    meta.setdefault("gatewayNamespace", namespace)
    if original_name is not None:
        meta.setdefault("gatewayOriginalName", original_name)
    if original_uri is not None:
        meta.setdefault("gatewayOriginalUri", original_uri)
    return meta


def _prefix_title(namespace: str, title: str | None) -> str | None:
    """给标题补 namespace 前缀，方便前端肉眼区分来源。"""

    if not title:
        return title
    return f"[{namespace}] {title}"


def _prefix_description(namespace: str, description: str | None) -> str | None:
    """给描述补 namespace 前缀，避免多个下游说明文字混淆。"""

    if not description:
        return description
    return f"[{namespace}] {description}"


def _to_helper_content(content_item: types.TextResourceContents | types.BlobResourceContents) -> ReadResourceContents:
    """把 SDK 资源内容对象转换成 lowlevel handler 能直接返回的 helper 对象。"""

    meta = getattr(content_item, "meta", None)
    if isinstance(content_item, types.TextResourceContents):
        return ReadResourceContents(
            content=content_item.text,
            mime_type=content_item.mimeType,
            meta=meta,
        )
    if isinstance(content_item, types.BlobResourceContents):
        # Blob 在协议层是 base64 文本，这里解码成原始 bytes 交给上游。
        return ReadResourceContents(
            content=base64.b64decode(content_item.blob),
            mime_type=content_item.mimeType,
            meta=meta,
        )
    raise TypeError(f"Unsupported resource content type: {type(content_item).__name__}")


def _detect_caller(request: Request) -> str:
    """从显式 header 或 user-agent 中识别调用方名称。"""

    explicit = request.headers.get("x-shared-gateway-caller") or request.headers.get("x-mcp-caller")
    if explicit:
        return explicit

    user_agent = (request.headers.get("user-agent") or "").lower()
    for client_name in ("claude-code", "openclaw", "opencode", "codex"):
        if client_name in user_agent:
            return client_name

    client_host = request.client.host if request.client else "unknown"
    return f"http:{client_host}"


def _get_request_context() -> RequestContext:
    """读取当前协程绑定的请求上下文；若无则返回默认占位对象。"""

    return REQUEST_CONTEXT.get() or RequestContext()


def _summarize_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """只提炼参数结构，不直接打出原文，避免日志过大或泄漏敏感内容。"""

    summary: dict[str, Any] = {
        "keys": sorted(arguments.keys()),
        "count": len(arguments),
    }
    types_summary: dict[str, str] = {}
    for key, value in arguments.items():
        value_type = type(value).__name__
        if isinstance(value, (str, bytes, bytearray)):
            value_type = f"{value_type}[len={len(value)}]"
        elif isinstance(value, (list, tuple, set)):
            value_type = f"{value_type}[len={len(value)}]"
        elif isinstance(value, dict):
            value_type = f"dict[len={len(value)}]"
        types_summary[key] = value_type
    if types_summary:
        summary["types"] = types_summary
    return summary


def _summarize_tool_result(result: types.CallToolResult) -> dict[str, Any]:
    """提炼工具返回结果的结构摘要，用于日志。"""

    return {
        "isError": getattr(result, "isError", False),
        "contentItems": len(getattr(result, "content", []) or []),
        "hasStructuredContent": getattr(result, "structuredContent", None) is not None,
    }


def _format_top_counter(counter: Counter[str], limit: int = 5) -> list[dict[str, Any]]:
    """把 Counter 转成稳定、可 JSON 序列化的 TopN 结构。"""

    return [{"name": name, "count": count} for name, count in counter.most_common(limit)]


def _now_string() -> str:
    """返回统一格式的本地时间字符串。"""

    return time.strftime("%Y-%m-%d %H:%M:%S%z")


def _summarize_exception(exc: BaseException) -> str:
    """压缩异常文本，避免日志被超长堆栈刷屏。"""

    message = str(exc).strip() or exc.__class__.__name__
    return _truncate_text(message, 500)


def _traceback_summary_from_exception(exc: BaseException, *, max_lines: int = 8) -> str | None:
    """从异常对象提炼最后几行 traceback 摘要。"""

    tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
    if not tb:
        return None
    lines = [line.strip() for chunk in tb for line in chunk.splitlines() if line.strip()]
    if not lines:
        return None
    return _truncate_text(" | ".join(lines[-max_lines:]), 1200)


def _truncate_text(value: str | None, limit: int) -> str | None:
    """通用截断函数，给日志与 healthz 都复用。"""

    if value is None:
        return None
    if len(value) <= limit:
        return value
    return f"{value[: limit - 3]}..."


def _extract_error_code(value: Any) -> str | None:
    """尽量从异常、字典、对象或文本中提取错误码。"""

    if value is None:
        return None
    if isinstance(value, str):
        return _extract_error_code_from_text(value)
    if isinstance(value, dict):
        for key in ("error_code", "errorCode", "code", "status_code", "status", "errno"):
            candidate = value.get(key)
            if candidate not in (None, ""):
                return str(candidate)
        nested = value.get("error")
        if nested is not None:
            nested_code = _extract_error_code(nested)
            if nested_code:
                return nested_code
        return None
    for attr in ("error_code", "errorCode", "code", "status_code", "status", "errno"):
        candidate = getattr(value, attr, None)
        if candidate not in (None, ""):
            return str(candidate)
    return None


def _extract_tool_error_details(result: types.CallToolResult) -> dict[str, str | None]:
    """从 tool error result 的多个载体中归并出错误摘要。"""

    structured = getattr(result, "structuredContent", None)
    meta = getattr(result, "meta", None)
    texts: list[str] = []
    error_code = _extract_error_code(structured) or _extract_error_code(meta)
    error_summary = _extract_error_summary(structured) or _extract_error_summary(meta)

    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            texts.append(str(text))
            maybe_json = _parse_json_like_text(text)
            if maybe_json is not None:
                error_code = error_code or _extract_error_code(maybe_json)
                error_summary = error_summary or _extract_error_summary(maybe_json)
        item_meta = getattr(item, "meta", None)
        error_code = error_code or _extract_error_code(item_meta)
        error_summary = error_summary or _extract_error_summary(item_meta)

    joined_text = "\n".join(texts).strip() if texts else None
    error_code = error_code or _extract_error_code_from_text(joined_text)
    traceback_summary = _traceback_summary_from_text(joined_text)
    error_summary = error_summary or _extract_error_summary(joined_text) or _truncate_text(joined_text, 500)

    return {
        "errorCode": error_code,
        "errorSummary": error_summary,
        "tracebackSummary": traceback_summary,
    }


def _extract_error_summary(value: Any) -> str | None:
    """递归提取最适合展示给日志/健康页的错误文案。"""

    if value is None:
        return None
    if isinstance(value, str):
        candidate = value.strip()
        return _truncate_text(candidate, 500) if candidate else None
    if isinstance(value, dict):
        for key in ("message", "error", "detail", "description", "title"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return _truncate_text(candidate.strip(), 500)
            if isinstance(candidate, dict):
                nested = _extract_error_summary(candidate)
                if nested:
                    return nested
        return None
    return None


def _parse_json_like_text(value: str) -> dict[str, Any] | None:
    """尝试把文本结果解析成 JSON 对象，用于进一步提取 error 信息。"""

    value = value.strip()
    if not value or value[0] not in "{[":
        return None
    try:
        parsed = json.loads(value)
    except Exception:  # noqa: BLE001
        return None
    return parsed if isinstance(parsed, dict) else None


def _traceback_summary_from_text(value: str | None, *, max_lines: int = 8) -> str | None:
    """从纯文本里识别 traceback 片段，适合处理 tool 的 text 输出。"""

    if not value:
        return None
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    traceback_lines = [
        line for line in lines if "Traceback" in line or line.startswith("File ") or "Error:" in line or "Exception:" in line
    ]
    if not traceback_lines and lines:
        traceback_lines = lines[-max_lines:]
    if not traceback_lines:
        return None
    return _truncate_text(" | ".join(traceback_lines[-max_lines:]), 1200)


def _extract_error_code_from_text(value: str | None) -> str | None:
    """通过正则从文本里匹配常见 error code 写法。"""

    if not value:
        return None

    patterns = (
        r"\berror[_ -]?code\s*[:=]\s*([A-Za-z0-9_.-]+)",
        r"\bcode\s*[:=]\s*([A-Za-z0-9_.-]+)",
        r"\bstatus(?:_code)?\s*[:=]\s*([A-Za-z0-9_.-]+)",
        r"\berrno\s*[:=]\s*([A-Za-z0-9_.-]+)",
        r"\[type=([A-Za-z0-9_.-]+)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, value, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _should_trip_circuit_breaker(exc: BaseException) -> bool:
    """判定异常是否属于传输层/运行态故障，只有这类错误才触发熔断。"""

    if not isinstance(exc, McpError):
        return True

    error_code = (_extract_error_code(exc) or "").lower()
    if error_code in {"timeout", "transport", "unavailable", "connection_error", "broken_pipe", "closed"}:
        return True

    text = f"{type(exc).__name__}: {exc}".lower()
    return any(token in text for token in TRANSPORT_ERROR_TOKENS)


def _circuit_open_message(connection: DownstreamConnection, breaker: CircuitBreakerState) -> str:
    """构造统一的熔断提示文案。"""

    return (
        f"Downstream {connection.config.key} is temporarily isolated by circuit breaker; "
        f"retry after about {breaker.remaining_seconds()}s"
    )


def _build_circuit_open_call_tool_result(
    connection: DownstreamConnection,
    breaker: CircuitBreakerState,
) -> types.CallToolResult:
    """把熔断状态包装成标准 MCP tool error result。"""

    message = _circuit_open_message(connection, breaker)
    return types.CallToolResult(
        isError=True,
        content=[types.TextContent(type="text", text=message)],
        structuredContent={
            "error": message,
            "error_code": "circuit_open",
            "downstream": connection.config.key,
            "open_until": breaker.open_until,
            "remaining_seconds": breaker.remaining_seconds(),
        },
    )


class RequestLoggingMiddleware:
    """为每个 HTTP 请求注入上下文、请求 ID 与结构化访问日志。"""

    def __init__(self, app: ASGIApp, gateway: SharedMcpGateway):
        """保存下游 ASGI app 与 gateway 实例。"""

        self.app = app
        self.gateway = gateway

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """包装 HTTP 请求生命周期并输出统一访问日志。"""

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        if request.url.path == "/healthz":
            await self.app(scope, receive, send)
            return

        context = RequestContext(
            caller=_detect_caller(request),
            request_id=request.headers.get("x-request-id") or uuid.uuid4().hex[:12],
            client_ip=request.client.host if request.client else "-",
            user_agent=request.headers.get("user-agent") or "-",
            method=request.method,
            path=request.url.path,
        )
        # 用 contextvar 绑定请求上下文，让更深层 handler 无需层层透传参数。
        token = REQUEST_CONTEXT.set(context)
        self.gateway.metrics.mark_http_request(context.caller)
        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 500))
                headers = list(message.get("headers", []))
                # 把 request_id 回写到响应头，方便客户端与服务端日志对齐。
                headers.append((b"x-shared-gateway-request-id", context.request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            log_event(
                LOGGER,
                logging.ERROR,
                "http_request_failed",
                caller=context.caller,
                request_id=context.request_id,
                method=context.method,
                path=context.path,
                client_ip=context.client_ip,
                user_agent=context.user_agent,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                error_code=_extract_error_code(exc),
                error_summary=_summarize_exception(exc),
                traceback_summary=_traceback_summary_from_exception(exc),
            )
            raise
        else:
            log_event(
                LOGGER,
                logging.INFO,
                "http_request",
                caller=context.caller,
                request_id=context.request_id,
                method=context.method,
                path=context.path,
                status=status_code,
                client_ip=context.client_ip,
                user_agent=context.user_agent,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )
        finally:
            REQUEST_CONTEXT.reset(token)


def build_mcp_server(gateway: SharedMcpGateway) -> Server:
    """把网关能力注册成标准 lowlevel MCP Server。"""

    server = Server(
        name=gateway.registry.gateway.name,
        instructions=gateway.registry.gateway.description,
    )

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        return gateway.list_tools()

    @server.call_tool(validate_input=False)
    async def handle_call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        return await gateway.call_tool(name, arguments)

    @server.list_resources()
    async def handle_list_resources() -> list[types.Resource]:
        return gateway.list_resources()

    @server.read_resource()
    async def handle_read_resource(uri) -> list[ReadResourceContents]:
        return await gateway.read_resource(str(uri))

    @server.list_resource_templates()
    async def handle_list_resource_templates() -> list[types.ResourceTemplate]:
        return gateway.list_resource_templates()

    @server.list_prompts()
    async def handle_list_prompts() -> list[types.Prompt]:
        return gateway.list_prompts()

    @server.get_prompt()
    async def handle_get_prompt(name: str, arguments: dict[str, str] | None) -> types.GetPromptResult:
        return await gateway.get_prompt(name, arguments)

    return server


async def create_app(registry: Registry) -> Starlette:
    """创建 Starlette 应用，并挂上 MCP endpoint、healthz 与生命周期管理。"""

    gateway = SharedMcpGateway(registry)
    server = build_mcp_server(gateway)
    session_manager = StreamableHTTPSessionManager(
        app=server,
        json_response=False,
        stateless=False,
    )
    mcp_app = StreamableHTTPASGIApp(session_manager)

    async def healthz(_request):
        return JSONResponse(gateway.health_snapshot())

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        """启动网关与 session manager，并托管后台 heartbeat 任务。"""

        async with gateway.run(), session_manager.run():
            heartbeat_task = asyncio.create_task(gateway.heartbeat_loop())
            try:
                yield
            finally:
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task

    app = Starlette(
        routes=[
            Route(registry.listen.path, endpoint=mcp_app),
            Route("/healthz", endpoint=healthz, methods=["GET"]),
        ],
        lifespan=lifespan,
    )
    app.add_middleware(RequestLoggingMiddleware, gateway=gateway)

    return app


async def serve(registry: Registry, log_level: str) -> None:
    """使用 uvicorn 启动 HTTP 网关。"""

    app = await create_app(registry)
    config = uvicorn.Config(
        app,
        host=registry.listen.host,
        port=registry.listen.port,
        log_level=log_level.lower(),
        access_log=False,
        log_config=None,
    )
    server = uvicorn.Server(config)
    await server.serve()


def parse_args() -> argparse.Namespace:
    """解析网关 CLI 参数。"""

    parser = argparse.ArgumentParser(description="Shared MCP gateway")
    parser.add_argument("--registry", default=str(project_root / "registry.toml"))
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def main() -> None:
    """网关 CLI 入口。"""

    args = parse_args()
    configure_structured_logging(args.log_level)
    registry = load_registry(args.registry)
    try:
        asyncio.run(serve(registry, args.log_level))
    except KeyboardInterrupt:
        log_event(LOGGER, logging.INFO, "gateway_stopped_by_user")


if __name__ == "__main__":
    main()
