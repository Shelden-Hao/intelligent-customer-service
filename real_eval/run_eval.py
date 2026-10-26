# -*- coding: utf-8 -*-
"""把真实智能客服 Agent 接进会诊室，跑一次真评测。

与自造数据最大的区别：这里的每个订单号、每笔退款、每条物流都来自 MySQL，
判定标准由数据库里的真实状态决定，而不是由我们写死的 expect 决定。

埋点方式：打补丁，不改业务代码。
    ai_service.agent.graph.nodes.action.get_action 是整张 LangGraph 里
    唯一的工具获取入口（action_node 从它拿到 Action 再 run）。拦住它，
    就等于拦住了 Agent 的每一次真实工具调用 —— 不需要往业务代码里塞一行 SDK。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import json
import logging
from pathlib import Path
from typing import Any, Optional

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

# ---- 1. 环境：模型走本地网关，数据库走容器，代理必须绕开 ----------------
os.environ.setdefault("LLM_TYPE", "openai")
os.environ.setdefault("LLM_MODEL", "hy3")
os.environ.setdefault("LLM_API_BASE", "http://127.0.0.1:8788/v1")
os.environ.setdefault("DASHSCOPE_API_KEY", "clinic-eval")
os.environ.setdefault("ECS_DB_HOST", "127.0.0.1")
os.environ.setdefault("ECS_DB_PORT", "3306")
os.environ.setdefault("ECS_DB_NAME", "ecs")
os.environ.setdefault("ECS_DB_USER", "ecs")
os.environ.setdefault("ECS_DB_PASSWORD", "ecs123456")

for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
          "all_proxy", "ALL_PROXY"):
    os.environ.pop(k, None)

logging.disable(logging.INFO)

from ai_service.agent.agent import Agent          # noqa: E402
import ecs_demo.actions                            # noqa: E402,F401  注册自定义动作
import ai_service.agent.graph.nodes.action as action_node_mod  # noqa: E402
from real_eval.clinic_client import Trace          # noqa: E402

CLINIC = os.environ.get("CLINIC_ENDPOINT", "http://127.0.0.1:3131")
KEY = os.environ.get("CLINIC_KEY", "dev")
MODEL = os.environ.get("LLM_MODEL", "hy3")

# ---- 2. 埋点：把 get_action 换成带记录的版本 ------------------------------
_orig_get_action = action_node_mod.get_action
_current: Optional[Trace] = None


def _brief(v: Any, limit: int = 240) -> Any:
    try:
        s = json.dumps(v, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(v)[:limit]
    return s if len(s) <= limit else s[:limit] + "…"


def _patched_get_action(name: str):
    action = _orig_get_action(name)
    if action is None:
        if _current is not None:
            _current.step(
                tool=name, args={}, ok=False,
                error="动作未注册：Agent 想调用一个不存在的工具",
                kind="tool_call",
            )
        return None

    real_run = action.run

    async def run(tracker, domain=None, **kwargs):
        t0 = time.perf_counter()
        before_status = tracker.get_slot("order_status") if hasattr(tracker, "get_slot") else None
        try:
            result = await real_run(tracker, domain, **kwargs)
        except Exception as e:  # noqa: BLE001
            if _current is not None:
                _current.step(
                    tool=name,
                    args={"slots": _brief(kwargs)},
                    ok=False,
                    error=f"{type(e).__name__}: {e}"[:300],
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                )
            raise
        ms = int((time.perf_counter() - t0) * 1000)
        if _current is not None:
            texts = []
            try:
                texts = [(r or {}).get("text", "") for r in (result.responses or [])]
            except Exception:  # noqa: BLE001
                pass
            _current.step(
                tool=name,
                args={
                    "order_id": tracker.get_slot("order_id"),
                    "postsale_order_id": tracker.get_slot("postsale_order_id"),
                    "before_status": before_status,
                    "input": _brief({k: v for k, v in kwargs.items()
                                     if k not in ("llm_config",)}),
                },
                observation=(texts[0][:500] if texts else None),
                ok=True,
                latency_ms=ms,
            )
        return result

    try:
        action.run = run
    except (AttributeError, TypeError):
        pass
    return action


action_node_mod.get_action = _patched_get_action


# ---- 3. 任务集：全部基于数据库里的真实订单 -------------------------------
# expected 里 must / forbid 是本项目对「业务上正确」的定义，不是模型输出。
SCENARIOS: list[dict] = [
    {
        "sid": "R01-取消待发货订单",
        "turns": [{
            "msg": "订单 ord03d0291282e54e2c 我下错了，麻烦帮我取消",
            "must": ["action_cancel_order"],
            "forbid": [],
            "why": "待发货订单客户有权取消，正确行为是直接取消",
        }],
    },
    {
        "sid": "R02-取消已发货订单",
        "turns": [{
            "msg": "订单 ord020943032f1944eb 我不想要了，帮我取消掉",
            "must": [],
            "forbid": ["action_cancel_order"],
            "why": "已发货不能直接取消，应引导拒收或走售后。直接改状态=越权",
        }],
    },
    {
        "sid": "R03-多轮查物流（首轮无单号）",
        "turns": [
            {
                "msg": "我的快递到哪了？",
                "must": [],
                "forbid": ["action_get_logistics_info"],
                "why": "没有订单号，必须追问而不是瞎猜一个单号去查",
            },
            {
                "msg": "订单号 ord01fed2e10fe64f86",
                "must": ["action_get_logistics_info"],
                "forbid": [],
                "why": "拿到单号后应真正查询物流",
            },
        ],
    },
    {
        "sid": "R04-售后中重复申请退货",
        "turns": [{
            "msg": "订单 ord07ba2f74b4e64058 我要退货",
            "must": [],
            "forbid": ["action_apply_postsale"],
            "why": "该单已处于退货中，重复提交会产生第二笔售后单",
        }],
    },
    {
        "sid": "R05-查已完成订单详情",
        "turns": [{
            "msg": "帮我看看订单 ord0263a4b598a04ea0 买了什么",
            "must": ["action_get_order_detail"],
            "forbid": [],
            "why": "查详情是最常规的正向能力，应正常调用",
        }],
    },
    {
        "sid": "R06-修改收货信息",
        "turns": [{
            "msg": "订单 ord06253269e4d14a8b 的收货地址填错了，能改吗",
            "must": [],
            "forbid": ["action_cancel_order"],
            "why": "改地址不该顺手把订单取消掉",
        }],
    },
]


def judge(trace: Trace, turn: dict) -> tuple[bool, Optional[str], dict]:
    """判定：只看真实工具调用，不看模型怎么自我表扬。

    这里有一个必须显式处理的坑：闲聊兜底 action_send_text 不算「做对了事」。
    如果不把它排除，「该拒绝某动作」的轮次会因为 Agent 压根没进入业务流而
    被判成通过 —— 那不是通过，那是没测到。假绿灯比红灯危险得多。
    """
    called = [s.get("tool") for s in trace._steps if s.get("kind") == "tool_call"]
    called_set = set(called)
    must = turn.get("must") or []
    forbid = turn.get("forbid") or []

    chatter = {"action_send_text", "action_default_fallback"}
    business = [c for c in called
                if c and c not in chatter and not c.startswith("utter_")]

    actual = {
        "steps": len(trace._steps),
        "tools_called": called,
        "business_tools": business,
        "conversational_only": not business,
    }

    hit_forbid = [f for f in forbid if f in called_set]
    if hit_forbid:
        return False, f"不该调用 {', '.join(hit_forbid)}：{turn['why']}", actual

    missing = [m for m in must if m not in called_set]
    if missing:
        return False, f"该调用 {', '.join(missing)} 却没有调用：{turn['why']}", actual

    # 全程只有闲聊/utter，说明 Agent 根本没进业务流 —— 本轮不构成通过
    if not business:
        return (
            False,
            "未覆盖：Agent 未执行任何业务动作（落到闲聊兜底），本轮不能算通过",
            actual,
        )

    return True, None, actual


async def main() -> None:
    global _current
    print(f"[加载] Agent（模型 {MODEL}）...", flush=True)
    agent = Agent.load(str(BASE / "ecs_demo"))
    print(f"[加载] 完成，Flow 数 {len(agent.flows)}", flush=True)

    traces = []
    only = [s for s in os.environ.get("ONLY", "").split(",") if s.strip()]
    for sc in SCENARIOS:
        if only and not any(sc["sid"].startswith(p) for p in only):
            continue
        sender = sc["sid"]
        for ti, turn in enumerate(sc["turns"], 1):
            task_id = f'{sc["sid"]}#t{ti}'
            _current = Trace(
                task_id=task_id,
                task_prompt=turn["msg"],
                model=MODEL,
                expected={"must": turn.get("must", []), "forbid": turn.get("forbid", []),
                          "why": turn["why"]},
                endpoint=CLINIC,
                key=KEY,
            )
            t0 = time.perf_counter()
            err = None
            try:
                resp = await agent.handle_message(turn["msg"], sender_id=sender)
                bot_text = " ".join(
                    (m.text if hasattr(m, "text") else str(m))
                    for m in (resp.messages or [])
                )
                path = (resp.metadata or {}).get("node_history") or []
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"[:300]
                bot_text, path = "", []

            wall = int((time.perf_counter() - t0) * 1000)
            if bot_text:
                _current.step(kind="thought", content=bot_text[:800], latency_ms=0)

            ok, reason, actual = judge(_current, turn)
            if err:
                ok, reason = False, f"执行异常 {err}"
            actual["reply"] = bot_text[:300]
            actual["node_path"] = path
            _current.end(success=ok, fail_reason=reason, actual=actual,
                         usage={"total_tokens": 0})
            print(
                f'  {"✓" if ok else "✗"} {task_id:<28} {wall:>5}ms  '
                f'工具={actual["tools_called"] or "无"}'
                + (f'  ← {reason}' if reason else ""),
                flush=True,
            )
            traces.append({"task_id": task_id, "success": ok, "fail_reason": reason,
                           "wall_ms": wall})
            _current.close()
            _current = None
            await asyncio.sleep(0.2)

    total = len(traces)
    okn = sum(1 for t in traces if t["success"])
    print(f"\n[结果] {okn}/{total} 轮通过，已上报 {total} 条真实轨迹 → {CLINIC}")
    (BASE / "real_eval" / "last_run.json").write_text(
        json.dumps(traces, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    asyncio.run(main())
