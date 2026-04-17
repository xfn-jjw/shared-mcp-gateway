from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from typing import BinaryIO

from shared_mcp_gateway.logging_utils import to_logfmt


# 下游 MCP 常见的“协议级噪音日志”关键字。
# 这些日志通常只表示 MCP SDK 正在处理 List/Call 请求，
# 对排障帮助有限，却会把 docker logs 刷得非常碎。
NOISY_STDERR_PATTERNS = (
    re.compile(r"Processing request of type"),
)

# 某些下游使用 rich / console 输出时，会把请求类型单独拆成下一行。
# 当上一行已经命中噪音模式时，下一行如果只是这些请求名，也一并吞掉。
NOISY_STDERR_CONTINUATIONS = {
    "ListToolsRequest",
    "ListResourcesRequest",
    "ListResourceTemplatesRequest",
    "ListPromptsRequest",
    "CallToolRequest",
    "ReadResourceRequest",
    "GetPromptRequest",
}


def _emit(event: str, **fields) -> None:
    prefix = time.strftime("ts=%Y-%m-%d %H:%M:%S%z level=INFO logger=shared_mcp_gateway.stdio_supervisor ")
    sys.stderr.write(prefix + to_logfmt(event, **fields) + "\n")
    sys.stderr.flush()


def _emit_error(event: str, **fields) -> None:
    prefix = time.strftime("ts=%Y-%m-%d %H:%M:%S%z level=ERROR logger=shared_mcp_gateway.stdio_supervisor ")
    sys.stderr.write(prefix + to_logfmt(event, **fields) + "\n")
    sys.stderr.flush()


def _is_noisy_stderr_line(line: str) -> bool:
    """判断当前 stderr 行是否属于可安全忽略的高频噪音。"""

    return any(pattern.search(line) for pattern in NOISY_STDERR_PATTERNS)


def _is_noisy_stderr_continuation(line: str) -> bool:
    """判断当前 stderr 行是否只是上一条噪音日志的续行。"""

    normalized = line.strip()
    return normalized in NOISY_STDERR_CONTINUATIONS


# 透明转发 stdin/stdout，保证 MCP stdio 协议不被破坏。
def _pipe_stream(reader: BinaryIO, writer: BinaryIO, *, close_writer: bool = False) -> None:
    try:
        while True:
            chunk = os.read(reader.fileno(), 65536)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(writer.fileno(), view)
                view = view[written:]
    except BrokenPipeError:
        pass
    finally:
        if close_writer:
            try:
                writer.close()
            except Exception:
                pass


# 下游 stderr 单独转成结构化日志，避免把容器日志搞成多种格式混杂。
def _relay_stderr(stderr_pipe: BinaryIO, downstream: str) -> None:
    suppress_next_continuation = False
    for raw_line in iter(stderr_pipe.readline, b""):
        line = raw_line.decode("utf-8", errors="replace").rstrip()
        if not line:
            continue
        # 过滤 MCP SDK 高频协议日志，避免 docker logs 被 ListTools / CallTool 之类噪音刷屏。
        if _is_noisy_stderr_line(line):
            suppress_next_continuation = True
            continue
        if suppress_next_continuation and _is_noisy_stderr_continuation(line):
            suppress_next_continuation = False
            continue
        suppress_next_continuation = False
        _emit("downstream_stderr", downstream=downstream, line=line)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Supervise downstream stdio MCP process")
    parser.add_argument("--downstream", required=True)
    parser.add_argument("--command", required=True)
    parser.add_argument("--args-json", default="[]")
    parser.add_argument("--env-json", default="{}")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    child_args = json.loads(args.args_json)
    extra_env = json.loads(args.env_json)
    env = os.environ.copy()
    env.update({str(key): str(value) for key, value in extra_env.items()})

    _emit(
        "downstream_supervisor_start",
        downstream=args.downstream,
        command=args.command,
        args=child_args,
    )

    process = subprocess.Popen(
        [args.command, *child_args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        bufsize=0,
    )

    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None

    threads = [
        threading.Thread(
            target=_pipe_stream,
            args=(sys.stdin.buffer, process.stdin),
            kwargs={"close_writer": True},
            daemon=True,
        ),
        threading.Thread(
            target=_pipe_stream,
            args=(process.stdout, sys.stdout.buffer),
            daemon=True,
        ),
        threading.Thread(
            target=_relay_stderr,
            args=(process.stderr, args.downstream),
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()

    return_code = process.wait()
    for thread in threads:
        thread.join(timeout=1)

    if return_code == 0:
        _emit("downstream_supervisor_exit", downstream=args.downstream, return_code=return_code)
    else:
        _emit_error("downstream_supervisor_exit", downstream=args.downstream, return_code=return_code)
    raise SystemExit(return_code)


if __name__ == "__main__":
    main()
