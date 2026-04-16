from __future__ import annotations

import json
import logging
import sys
from typing import Any, TextIO


def _render_scalar(value: Any) -> str:
    """把任意 Python 值规整为 logfmt 可序列化的标量字符串。"""

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _needs_quotes(text: str) -> bool:
    """判断字段值是否需要加引号，避免空格或 `=` 破坏 logfmt 结构。"""

    if text == "":
        return True
    return any(char.isspace() or char in {'"', '='} for char in text)


def _quote(text: str) -> str:
    """按 logfmt 规则转义危险字符。"""

    escaped = text.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
    return f'"{escaped}"'


def to_logfmt(event: str, /, **fields: Any) -> str:
    """把事件名与字段渲染成统一的 logfmt 文本。"""

    parts = [f"event={_quote(event) if _needs_quotes(event) else event}"]
    for key, value in fields.items():
        rendered = _render_scalar(value)
        parts.append(f"{key}={_quote(rendered) if _needs_quotes(rendered) else rendered}")
    return " ".join(parts)


def configure_structured_logging(log_level: str, *, stream: TextIO | None = None) -> None:
    """初始化标准库 logging，并统一使用 logfmt 风格输出。"""

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="ts=%(asctime)s level=%(levelname)s logger=%(name)s %(message)s",
        stream=stream or sys.stderr,
    )


def log_event(logger: logging.Logger, level: int, event: str, /, **fields: Any) -> None:
    """统一事件日志入口，业务代码只负责填充结构化字段。"""

    logger.log(level, to_logfmt(event, **fields))
