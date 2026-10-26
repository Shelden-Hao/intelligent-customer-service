"""Agent 会诊室 · Python 上报客户端（与 JS SDK 同一套协议）。

为什么不是直接引 sdk/agent-clinic.js：
本项目是 Python 服务端，没有浏览器/Node 运行时。但上报协议本身与语言无关，
所以这里用同一组端点、同一套事件字段重写了一遍。这同时也是对「协议是否
真的与语言无关」的一次检验 —— 如果换个语言就要改服务端，那协议设计是失败的。

三条设计原则照抄 JS 版：
1. 绝不阻塞业务：上报走后台线程，失败只记数不抛异常。
2. wrap() 比 step() 重要：实际接入时人只会包一层，不会记得逐步埋点。
3. 不替你判断成败：success 必须调用方显式给。

用法：
    from real_eval.clinic_client import Trace
    tr = Trace(task_id="T01", task_prompt="...", model="hy3")
    tr.step(tool="get_order", args={...}, observation={...}, ok=True, latency_ms=42)
    tr.end(success=False, fail_reason="应当退款却转人工")
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from uuid import uuid4

DEFAULT_ENDPOINT = os.environ.get("CLINIC_ENDPOINT", "http://127.0.0.1:3131")
DEFAULT_KEY = os.environ.get("CLINIC_KEY", "dev")


def _opener():
    """本地/内网地址要绕开系统代理。

    这台机器的 HTTP_PROXY 指向一个本地代理，走它访问 127.0.0.1 会拿到 502。
    之前排查 SDK 那个「静默 403」时踩过同一个坑，这里显式禁掉。
    """
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


class _Sender:
    """后台批量上报。队列满了丢最旧的，绝不阻塞调用方。"""

    def __init__(self, endpoint: str, key: str, max_queue: int = 400):
        self.base = endpoint.rstrip("/")
        self.key = key
        self.max_queue = max_queue
        self._q: list[tuple[str, dict]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.sent = 0
        self.failed = 0
        self.dropped = 0

    def push(self, kind: str, payload: dict) -> None:
        with self._lock:
            if len(self._q) >= self.max_queue:
                self._q.pop(0)
                self.dropped += 1
            self._q.append((kind, payload))

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._drain()
            self._stop.wait(0.4)

    def _drain(self) -> None:
        with self._lock:
            batch, self._q = self._q, []
        if not batch:
            return
        # 同一批只发一个端点：混着发会两边都少数据
        steps = [p for k, p in batch if k == "step"]
        traces = [p for k, p in batch if k == "trace"]
        if steps:
            self._post("step", steps)
        for t in traces:
            self._post("trace", [t])

    def _url(self, path: str) -> str:
        """endpoint 允许带查询串（平台分享码）。

        直接 `${base}/api/...` 会把查询串顶掉，请求打到站点根 —— 静默 403。
        这条坑在 JS SDK 里已经修过一次，这里保持一致。
        """
        q = self.base.find("?")
        root = self.base if q == -1 else self.base[:q]
        qs = "" if q == -1 else self.base[q:]
        return f"{root}/api/stream/{path}{qs}"

    def _post(self, path: str, items: list[dict]) -> None:
        body = json.dumps(items, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self._url(path),
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Clinic-Key": self.key,
            },
        )
        try:
            with _opener().open(req, timeout=4) as r:
                r.read()
            self.sent += len(items)
        except (urllib.error.URLError, OSError, TimeoutError):
            # 观测数据不值得重试到影响业务
            self.failed += len(items)

    def close(self) -> None:
        self._drain()
        self._stop.set()


@dataclass
class Trace:
    """一条轨迹 = 一个任务的一次执行。"""

    task_id: str
    task_prompt: str
    model: str = "unknown"
    prompt_version: str = "v1"
    run_id: str = field(default_factory=lambda: f"{uuid4().hex[:12]}")
    expected: dict = field(default_factory=dict)
    endpoint: str = DEFAULT_ENDPOINT
    key: str = DEFAULT_KEY
    _steps: list[dict] = field(default_factory=list, init=False)
    _t0: float = field(default_factory=time.perf_counter, init=False)
    _sender: Optional[_Sender] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_sender", _Sender(self.endpoint, self.key))

    # ---- 上报 ----
    def step(
        self,
        tool: Optional[str] = None,
        args: Optional[dict] = None,
        observation: Any = None,
        ok: bool = True,
        latency_ms: int = 0,
        kind: str = "tool_call",
        content: Optional[str] = None,
        error: Optional[str] = None,
    ) -> dict:
        i = len(self._steps) + 1
        item = {
            "i": i,
            "kind": kind,
            "tool": tool,
            "args": args or {},
            "observation": observation,
            "ok": bool(ok),
            "latency_ms": int(latency_ms),
            "content": content,
            "error": error,
        }
        self._steps.append(item)
        assert self._sender is not None
        # 协议要点：单步事件必须把步骤包在 `step` 字段里，且带 run_id/task_id。
        # 直接发扁平结构服务端照样广播，但前端读的是 ev.step，会静默渲染成
        # 「0 步 / 等待第一步」—— 不报错、不丢数据，只是页面上什么都看不见。
        self._sender.push("step", {
            "type": "step",
            "run_id": self.run_id,
            "task_id": self.task_id,
            "version": self.prompt_version,
            "model": self.model,
            "step": {
                "i": item["i"],
                "kind": item["kind"],
                "tool": item["tool"],
                "args": item["args"],
                "ok": item["ok"],
                "error": item["error"],
                "content": (item["content"] or "")[:400] or None,
                "latency_ms": item["latency_ms"],
            },
            "at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        })
        return item

    def thought(self, text: str, latency_ms: int = 0) -> dict:
        return self.step(kind="thought", content=text, latency_ms=latency_ms)

    def end(
        self,
        success: bool,
        fail_reason: Optional[str] = None,
        actual: Optional[dict] = None,
        usage: Optional[dict] = None,
    ) -> dict:
        payload = {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "prompt_version": self.prompt_version,
            "task_prompt": self.task_prompt,
            "steps": self._steps,
            "success": bool(success),
            "success_known": True,
            "expected": self.expected or {},
            "actual": actual or {},
            "fail_reason": fail_reason,
            "model": self.model,
            "started_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "usage": usage or {"total_tokens": 0},
            "imported_from": "real-agent",
            "wall_ms": int((time.perf_counter() - self._t0) * 1000),
        }
        assert self._sender is not None
        self._sender.push("trace", payload)
        return payload

    def close(self) -> None:
        if self._sender:
            self._sender.close()

    # ---- 给 wrap() 用 ----
    def wrap(self, fns: dict[str, Callable]) -> dict[str, Callable]:
        """把工具函数包一层，之后每次调用自动上报。

        与 JS SDK 的 wrap() 语义一致：出异常照常抛出，只多记一条失败步骤。
        """
        out: dict[str, Callable] = {}
        for name, fn in fns.items():
            out[name] = self._wrap_one(name, fn)
        return out

    def _wrap_one(self, name: str, fn: Callable) -> Callable:
        def inner(*a: Any, **kw: Any):
            t0 = time.perf_counter()
            try:
                res = fn(*a, **kw)
            except Exception as e:  # noqa: BLE001
                self.step(
                    tool=name,
                    args={"args": list(a), "kwargs": kw},
                    ok=False,
                    error=str(e)[:300],
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                )
                raise
            self.step(
                tool=name,
                args={"args": list(a), "kwargs": kw},
                observation=_shrink(res),
                ok=True,
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )
            return res

        return inner


def _shrink(v: Any, limit: int = 600) -> Any:
    """观测值可能很大（订单详情/物流轨迹），截断后再上报。"""
    try:
        s = json.dumps(v, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(v)[:limit]
    if len(s) <= limit:
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return s
    return s[:limit] + "…(截断)"
