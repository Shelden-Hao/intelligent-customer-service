# -*- coding: utf-8 -*-
"""把真实 Agent 的轨迹重新过一遍会诊，把每轮的诊断结论抓下来。

为什么重新提交：上报是异步的，SDK 的设计目标是「绝不阻塞业务」，
所以它拿到 200 就走了，不解析响应。要拿到诊断结论，就再同步提交一次 ——
同一份轨迹是幂等的，诊断只看内容。

输入：会诊室落盘的 runtime/stream.jsonl（真实上报留痕）
输出：real_eval/report.json + 终端表格
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent.parent
STREAM = Path(r"C:\Codefield\1024-project\runtime\stream.jsonl")
CLINIC = os.environ.get("CLINIC_ENDPOINT", "http://127.0.0.1:3131")
KEY = os.environ.get("CLINIC_KEY", "dev")

SESSION = requests.Session()
SESSION.trust_env = False  # 本地地址不能走系统代理


def load_real_traces() -> list[dict]:
    """只取本项目真实 Agent 上报的轨迹（imported_from=real-agent）。

    同一个任务可能因为换模型/重跑上报过多次：文件是 append 顺序，
    后写的覆盖先写的，保证一个任务只留最新一次，否则统计口径全乱。
    """
    if not STREAM.exists():
        print(f"找不到 {STREAM}", file=sys.stderr)
        return []
    latest: dict[str, dict] = {}
    for line in STREAM.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("imported_from") != "real-agent":
            continue
        if not d.get("steps"):
            continue
        latest[d["task_id"]] = d
    return [latest[k] for k in sorted(latest)]


def diagnose(traces: list[dict]) -> list[dict]:
    r = SESSION.post(
        f"{CLINIC}/api/stream/trace",
        headers={"X-Clinic-Key": KEY, "Content-Type": "application/json"},
        json=traces,
        timeout=120,
    )
    r.raise_for_status()
    return r.json().get("results", [])


def main() -> None:
    traces = load_real_traces()
    if not traces:
        print("没有找到真实 Agent 轨迹")
        return
    print(f"取到 {len(traces)} 条真实轨迹，提交会诊 ...\n")

    results = diagnose(traces)
    by_id = {t["run_id"]: t for t in traces}

    rows = []
    for r in results:
        t = by_id.get(r["run_id"], {})
        ev = r.get("evidence") or []
        if isinstance(ev, str):
            ev_list = [ev]
        elif isinstance(ev, list):
            ev_list = ev
        else:
            ev_list = [str(ev)]
        rows.append({
            "task_id": t.get("task_id"),
            "model": t.get("model"),
            "success": r.get("success"),
            "kind": r.get("kind"),
            "kind_label": r.get("kind_label"),
            "confidence": r.get("confidence"),
            "steps": r.get("steps"),
            "evidence": ev_list[:3],
            "minimal_steps": r.get("minimal_steps"),
            "score": (r.get("evaluation") or {}).get("overall"),
            "fail_reason": t.get("fail_reason"),
            "tools": [s.get("tool") for s in (t.get("steps") or [])
                      if s.get("kind") == "tool_call"],
        })

    w = max((len(x["task_id"] or "") for x in rows), default=10)
    print(f'{"任务":<{w}}  成败  病种            置信  步数  评分')
    print("-" * (w + 40))
    for x in rows:
        ok = "✓" if x["success"] else "✗"
        print(f'{x["task_id"]:<{w}}  {ok}    {x["kind_label"] or "-":<14}  '
              f'{x["confidence"] or 0:<4}  {x["steps"] or 0:<4}  {x["score"] or "-"}')

    print("\n=== 失败轮的证据 ===")
    for x in rows:
        if x["success"]:
            continue
        print(f'\n· {x["task_id"]}  [{x["kind_label"]}] 置信 {x["confidence"]}')
        print(f'  工具链: {" → ".join(x["tools"]) or "（无工具调用）"}')
        print(f'  判定: {x["fail_reason"]}')
        for e in x["evidence"]:
            if isinstance(e, dict):
                print(f'  证据: {e.get("note") or e.get("text") or json.dumps(e, ensure_ascii=False)[:160]}')
            else:
                print(f"  证据: {str(e)[:160]}")
        if x["minimal_steps"]:
            print(f'  最小复现: {x["minimal_steps"]} 步')

    passed = sum(1 for x in rows if x["success"])
    print(f'\n总计 {passed}/{len(rows)} 通过')
    (BASE / "real_eval" / "report.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("已写 real_eval/report.json")


if __name__ == "__main__":
    main()
