from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, types
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server


LOGGER = logging.getLogger("shared_mcp_gateway.stdio_bridge")
project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from shared_mcp_gateway.logging_utils import configure_structured_logging, log_event


class RemoteGatewayBridge:
    """把本地 stdio 客户端桥接到共享 HTTP MCP 网关，并附带 caller 标识。

    这个类本质上是一个“协议转接器”：
    1. 上游对它说 stdio MCP；
    2. 它再把请求转发给远端 streamable-http MCP；
    3. 同时补齐 caller / user-agent 等头，便于网关侧做观测与隔离。
    """

    def __init__(self, url: str, caller: str):
        """缓存网关地址与调用方身份，并提前准备好连接资源。"""

        self.url = url
        self.caller = caller
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None
        # 所有 MCP 请求串行经过同一个 session，避免并发写读打乱底层流状态。
        self._lock = anyio.Lock()
        self._headers = {
            "x-shared-gateway-caller": caller,
            "user-agent": f"shared-mcp-stdio-bridge/{caller}",
        }

    async def start(self) -> None:
        """建立到共享网关的 HTTP MCP 会话，并完成 initialize 握手。"""

        try:
            read_stream, write_stream, _session_id_cb = await self._stack.enter_async_context(
                streamablehttp_client(self.url, headers=self._headers)
            )
            session = await self._stack.enter_async_context(ClientSession(read_stream, write_stream))
            init_result = await session.initialize()
            self._session = session
            log_event(
                LOGGER,
                logging.INFO,
                "stdio_bridge_connected",
                caller=self.caller,
                gateway_name=init_result.serverInfo.name,
                url=self.url,
            )
        except BaseException as exc:
            log_event(
                LOGGER,
                logging.ERROR,
                "stdio_bridge_connect_failed",
                caller=self.caller,
                url=self.url,
                error_type=type(exc).__name__,
                error_summary=str(exc),
            )
            await self.close()
            raise

    async def close(self) -> None:
        """关闭所有异步上下文，确保网络连接与后台任务被正确回收。"""

        try:
            await self._stack.aclose()
        except BaseException as exc:  # noqa: BLE001
            log_event(
                LOGGER,
                logging.DEBUG,
                "stdio_bridge_cleanup_ignored_error",
                caller=self.caller,
                error_type=type(exc).__name__,
                error_summary=str(exc),
            )
        finally:
            self._session = None

    def _require_session(self) -> ClientSession:
        """在真正发请求前校验会话是否已初始化完成。"""

        if self._session is None:
            raise RuntimeError("Bridge session is not initialized")
        return self._session

    async def list_tools(self) -> list[types.Tool]:
        """透传工具列表，并复用串行锁保证 session 安全。"""

        async with self._lock:
            result = await self._require_session().list_tools()
        return result.tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        """转发工具调用。"""

        async with self._lock:
            return await self._require_session().call_tool(name, arguments)

    async def list_resources(self) -> list[types.Resource]:
        """转发资源列表查询。"""

        async with self._lock:
            result = await self._require_session().list_resources()
        return result.resources

    async def read_resource(self, uri: str) -> list[ReadResourceContents]:
        """读取资源，并把返回内容转换成 lowlevel helper 所需格式。"""

        async with self._lock:
            result = await self._require_session().read_resource(uri)
        return [_to_helper_content(item) for item in result.contents]

    async def list_resource_templates(self) -> list[types.ResourceTemplate]:
        """转发资源模板列表查询。"""

        async with self._lock:
            result = await self._require_session().list_resource_templates()
        return result.resourceTemplates

    async def list_prompts(self) -> list[types.Prompt]:
        """转发 prompt 列表查询。"""

        async with self._lock:
            result = await self._require_session().list_prompts()
        return result.prompts

    async def get_prompt(self, name: str, arguments: dict[str, str] | None) -> types.GetPromptResult:
        """转发 prompt 获取请求。"""

        async with self._lock:
            return await self._require_session().get_prompt(name, arguments)


def _to_helper_content(content_item: types.TextResourceContents | types.BlobResourceContents) -> ReadResourceContents:
    """把 SDK 层资源对象转换成 lowlevel Server 需要的 helper 内容对象。"""

    meta = getattr(content_item, "meta", None)
    if isinstance(content_item, types.TextResourceContents):
        return ReadResourceContents(
            content=content_item.text,
            mime_type=content_item.mimeType,
            meta=meta,
        )
    if isinstance(content_item, types.BlobResourceContents):
        # Blob 在 MCP 协议里通常是 base64 文本，这里解码回 bytes 再交给上游 stdio client。
        return ReadResourceContents(
            content=base64.b64decode(content_item.blob),
            mime_type=content_item.mimeType,
            meta=meta,
        )
    raise TypeError(f"Unsupported resource content type: {type(content_item).__name__}")


def build_server(bridge: RemoteGatewayBridge) -> Server:
    """构造本地 stdio MCP Server，并把所有 handler 绑定到远端 bridge。"""

    server = Server(
        name="shared-gateway-stdio-bridge",
        instructions="StdIO bridge to the shared MCP gateway.",
    )

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        return await bridge.list_tools()

    @server.call_tool(validate_input=False)
    async def handle_call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        return await bridge.call_tool(name, arguments)

    @server.list_resources()
    async def handle_list_resources() -> list[types.Resource]:
        return await bridge.list_resources()

    @server.read_resource()
    async def handle_read_resource(uri) -> list[ReadResourceContents]:
        return await bridge.read_resource(str(uri))

    @server.list_resource_templates()
    async def handle_list_resource_templates() -> list[types.ResourceTemplate]:
        return await bridge.list_resource_templates()

    @server.list_prompts()
    async def handle_list_prompts() -> list[types.Prompt]:
        return await bridge.list_prompts()

    @server.get_prompt()
    async def handle_get_prompt(name: str, arguments: dict[str, str] | None) -> types.GetPromptResult:
        return await bridge.get_prompt(name, arguments)

    return server


async def serve_stdio(url: str, caller: str) -> None:
    """启动 stdio bridge 的完整生命周期。"""

    bridge = RemoteGatewayBridge(url, caller)
    await bridge.start()
    server = build_server(bridge)
    initialization_options = server.create_initialization_options(
        notification_options=NotificationOptions(),
        experimental_capabilities={},
    )

    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                initialization_options,
                raise_exceptions=False,
            )
    finally:
        await bridge.close()


def parse_args() -> argparse.Namespace:
    """解析 bridge 命令行参数。"""

    parser = argparse.ArgumentParser(description="Shared MCP stdio bridge")
    parser.add_argument("--url", required=True)
    parser.add_argument("--caller", default=os.environ.get("SHARED_MCP_CALLER", "stdio-client"))
    parser.add_argument("--log-level", default="WARNING")
    return parser.parse_args()


def main() -> None:
    """bridge CLI 入口。"""

    args = parse_args()
    configure_structured_logging(args.log_level)
    try:
        asyncio.run(serve_stdio(args.url, args.caller))
    except KeyboardInterrupt:
        log_event(LOGGER, logging.INFO, "stdio_bridge_stopped_by_user", caller=args.caller)


if __name__ == "__main__":
    main()
