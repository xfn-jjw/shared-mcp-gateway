from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession, types
from mcp.client.streamable_http import streamablehttp_client

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from shared_mcp_gateway.config import Registry, load_registry
from shared_mcp_gateway.logging_utils import to_logfmt


DEFAULT_MCP_URL = "http://127.0.0.1:8787/mcp"
DEFAULT_HEALTHZ_URL = "http://127.0.0.1:8787/healthz"
SAFE_TOOL_CALLS: dict[str, dict[str, Any]] = {
    "mempalace.mempalace_status": {},
    "mysql_db.ping_db": {},
}
PRESENCE_ONLY_TOOLS = {
    "obsidian_kb.kb_search_notes",
    "tencent_cls.cls_login",
}


@dataclass(slots=True)
class CheckResult:
    """单个检查项的标准化结果。"""

    name: str
    ok: bool
    severity: str
    duration_ms: float
    details: dict[str, Any] = field(default_factory=dict)


class StructuredPrinter:
    """统一输出自检事件与最终汇总。

    - 默认输出 logfmt，适合人工排障；
    - `--json` 模式输出单份 JSON，适合 CI / 脚本消费。
    """

    def __init__(self, *, as_json: bool):
        self.as_json = as_json
        self.events: list[dict[str, Any]] = []

    def emit(self, event: str, **fields: Any) -> None:
        """记录并按当前模式打印一条结构化事件。"""

        payload = {"event": event, **fields}
        self.events.append(payload)
        if not self.as_json:
            print(to_logfmt(event, **fields))

    def flush_summary(self, summary: dict[str, Any]) -> None:
        """在 JSON 模式下统一输出最终汇总。"""

        if self.as_json:
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


class SelfChecker:
    """一次性串起来 healthz、网关连通性和关键工具探活，输出结构化结果。"""

    def __init__(
        self,
        *,
        mcp_url: str,
        healthz_url: str,
        timeout_seconds: float,
        printer: StructuredPrinter,
        registry: Registry | None,
    ):
        """保存一次自检运行所需的全部上下文。"""

        self.mcp_url = mcp_url
        self.healthz_url = healthz_url
        self.timeout_seconds = timeout_seconds
        self.printer = printer
        self.registry = registry
        self.results: list[CheckResult] = []
        self.health_payload: dict[str, Any] | None = None

    @property
    def expected_namespaces(self) -> list[str]:
        """根据注册表推导出本环境应该接入共享网关的 namespace。"""

        if self.registry is None:
            return ["mempalace", "mysql_db", "obsidian_kb", "tencent_cls"]
        return [server.namespace for server in self.registry.enabled_servers]

    @property
    def expected_tools(self) -> set[str]:
        """过滤出当前环境应该存在的关键工具集合。"""

        tools = set(SAFE_TOOL_CALLS) | set(PRESENCE_ONLY_TOOLS)
        if self.registry is None:
            return tools
        registry_namespaces = {server.namespace for server in self.registry.enabled_servers}
        return {tool for tool in tools if tool.split(".", 1)[0] in registry_namespaces}

    async def run(self) -> int:
        """串行执行所有检查项，并给出最终退出码。"""

        topology = self._topology_snapshot()
        self.printer.emit("self_check_scope", mcp_url=self.mcp_url, healthz_url=self.healthz_url, topology=topology)

        await self._run_check("healthz", self.check_healthz)
        await self._run_check("gateway_tools", self.check_gateway_tools)

        status = "ok" if all(result.ok for result in self.results) else "failed"
        summary = {
            "status": status,
            "checkedAt": time.strftime("%Y-%m-%d %H:%M:%S%z"),
            "mcpUrl": self.mcp_url,
            "healthzUrl": self.healthz_url,
            "results": [asdict(result) for result in self.results],
            "topology": topology,
            "health": self.health_payload,
        }
        self.printer.emit(
            "self_check_summary",
            status=status,
            total=len(self.results),
            passed=sum(1 for result in self.results if result.ok),
            failed=sum(1 for result in self.results if not result.ok),
        )
        self.printer.flush_summary(summary)
        return 0 if status == "ok" else 1

    async def _run_check(self, name: str, func) -> None:
        """统一封装单个检查项的计时、异常捕获与结果打印。"""

        started = time.perf_counter()
        try:
            details = await func()
        except Exception as exc:  # noqa: BLE001
            result = CheckResult(
                name=name,
                ok=False,
                severity="error",
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                details={
                    "errorType": type(exc).__name__,
                    "errorSummary": str(exc),
                },
            )
        else:
            result = CheckResult(
                name=name,
                ok=bool(details.get("ok", True)),
                severity=str(details.get("severity", "info")),
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                details=details,
            )
        self.results.append(result)
        self.printer.emit(
            "self_check_result",
            name=result.name,
            ok=result.ok,
            severity=result.severity,
            duration_ms=result.duration_ms,
            details=result.details,
        )

    async def check_healthz(self) -> dict[str, Any]:
        """检查 `/healthz` 是否正常返回，并校验下游连接/熔断状态。"""

        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = await client.get(self.healthz_url)
        payload = response.json()
        self.health_payload = payload

        gateway = payload.get("gateway", {})
        connected = set(gateway.get("connectedServers", []))
        expected = set(self.expected_namespaces)
        missing = sorted(expected - connected)
        failed_servers = gateway.get("failedServers", {}) or {}
        breakers = payload.get("circuitBreakers", {}) or {}
        open_breakers = {
            key: snapshot
            for key, snapshot in breakers.items()
            if snapshot.get("state") == "open"
        }
        half_open_breakers = {
            key: snapshot
            for key, snapshot in breakers.items()
            if snapshot.get("state") == "half-open"
        }

        ok = response.status_code == 200 and not missing and not failed_servers and not open_breakers
        severity = "info" if ok else "error"
        if ok and half_open_breakers:
            severity = "warning"

        return {
            "ok": ok,
            "severity": severity,
            "httpStatus": response.status_code,
            "connectedServers": sorted(connected),
            "expectedServers": sorted(expected),
            "missingServers": missing,
            "failedServers": failed_servers,
            "openCircuitBreakers": open_breakers,
            "halfOpenCircuitBreakers": half_open_breakers,
            "localExceptions": payload.get("topology", {}).get("localExceptions", {}),
        }

    async def check_gateway_tools(self) -> dict[str, Any]:
        """直接用 MCP 协议连网关，验证工具清单和安全探活调用。"""

        headers = {
            "x-shared-gateway-caller": "self-check",
            "user-agent": "shared-gateway-self-check/1.0",
        }
        async with streamablehttp_client(self.mcp_url, headers=headers) as streams:
            read_stream, write_stream, _session_id_cb = streams
            async with ClientSession(read_stream, write_stream) as session:
                init_result = await session.initialize()
                tools_result = await session.list_tools()
                tool_names = {tool.name for tool in tools_result.tools}
                missing_tools = sorted(self.expected_tools - tool_names)
                safe_call_results: dict[str, Any] = {}

                for tool_name, arguments in SAFE_TOOL_CALLS.items():
                    if tool_name not in tool_names:
                        safe_call_results[tool_name] = {
                            "ok": False,
                            "reason": "missing_tool",
                        }
                        continue

                    # 这里只调用预定义的“安全工具”，避免自检过程修改真实数据。
                    result = await session.call_tool(tool_name, arguments)
                    safe_call_results[tool_name] = self._summarize_call_result(result)

        ok = not missing_tools and all(item.get("ok", False) for item in safe_call_results.values())
        return {
            "ok": ok,
            "severity": "info" if ok else "error",
            "gatewayName": init_result.serverInfo.name,
            "toolCount": len(tool_names),
            "missingTools": missing_tools,
            "safeCallResults": safe_call_results,
            "presenceOnlyTools": sorted(PRESENCE_ONLY_TOOLS & tool_names),
        }

    def _topology_snapshot(self) -> dict[str, Any]:
        """输出本次自检所依据的拓扑快照，便于排查环境差异。"""

        if self.registry is None:
            return {
                "sharedGatewayNamespaces": self.expected_namespaces,
                "localExceptions": {},
            }
        return {
            "sharedGatewayNamespaces": [server.namespace for server in self.registry.enabled_servers],
            "localExceptions": self.registry.local_exceptions,
            "registryPath": str(self.registry.source_path),
        }

    @staticmethod
    def _summarize_call_result(result: types.CallToolResult) -> dict[str, Any]:
        """提炼工具调用结果，避免完整输出把终端日志刷满。"""

        texts: list[str] = []
        for item in getattr(result, "content", []) or []:
            text = getattr(item, "text", None)
            if text:
                texts.append(str(text))
        text_preview = "\n".join(texts).strip() if texts else None
        return {
            "ok": not getattr(result, "isError", False),
            "isError": getattr(result, "isError", False),
            "contentItems": len(getattr(result, "content", []) or []),
            "structuredContent": getattr(result, "structuredContent", None),
            "textPreview": text_preview[:500] if text_preview else None,
        }


def _load_registry_if_exists(path: str | None) -> Registry | None:
    """按宿主机/容器双环境顺序查找可用注册表。"""

    candidates: list[Path] = []
    if path:
        candidates.append(Path(path).expanduser().resolve())
    # 宿主机优先 registry.toml，容器内则自动回退到 registry.compose.toml。
    candidates.extend([
        project_root / "registry.toml",
        project_root / "registry.compose.toml",
    ])
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.exists():
            return load_registry(resolved)
    return None


def parse_args() -> argparse.Namespace:
    """解析自检命令行参数。"""

    parser = argparse.ArgumentParser(description="Run shared-gateway self-check")
    parser.add_argument("--url", default=DEFAULT_MCP_URL)
    parser.add_argument("--healthz-url", default=DEFAULT_HEALTHZ_URL)
    parser.add_argument("--timeout", default=10.0, type=float)
    parser.add_argument("--registry", default=str(project_root / "registry.toml"))
    parser.add_argument("--json", action="store_true", help="Print a single JSON summary")
    return parser.parse_args()


async def async_main() -> int:
    """异步主流程：加载配置、执行自检、返回退出码。"""

    args = parse_args()
    printer = StructuredPrinter(as_json=args.json)
    registry = _load_registry_if_exists(args.registry)
    checker = SelfChecker(
        mcp_url=args.url,
        healthz_url=args.healthz_url,
        timeout_seconds=args.timeout,
        printer=printer,
        registry=registry,
    )
    return await checker.run()


def main() -> None:
    """脚本入口。"""

    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
