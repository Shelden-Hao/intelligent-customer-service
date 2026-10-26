# 真实 Agent 接入评测报告

> 目的：检验「Agent 会诊室」在**自己造的数据之外**是否还成立。
> 被评测对象：本仓库的智能客服 Agent（LangGraph，7 条真实业务 Flow，15+ Action）。
> 数据来源：本地 MySQL `ecs` 库真实数据（200 订单 / 246 明细 / 46 售后 / 139 物流 / 30 收货信息）。

---

## 一、怎么接的

**没有改动一行业务代码。** 埋点是打补丁：

```
ai_service.agent.graph.nodes.action.get_action   ← 整张 LangGraph 里唯一的工具获取入口
```

`action_node` 从这个函数拿到 Action 再 `await action.run(...)`。包住它，就包住了 Agent 的每一次真实工具调用。这是 SDK 该有的形态：业务代码不知道自己在被观测。

另有两处**配置**改动（默认值全部保留，行为不变）：

| 文件 | 改动 | 原因 |
|---|---|---|
| `ecs_demo/endpoints.yml` | 模型改为 `${LLM_TYPE}` / `${LLM_MODEL}` / `${LLM_API_BASE}` 环境变量 | 原来写死 DashScope，无法指向本地 OpenAI 兼容网关 |
| `ecs_demo/actions/db.py` | 数据库连接改读 `ECS_DB_*` 环境变量 | 原来写死 `root:123321@localhost`，连不上容器 |

## 二、模型选型：从最便宜的往上试

按「优先消耗最少额度」的原则逐级验证，结果很反直觉：

| 档位 | 模型 | 额度 | 结构化命令解析 | 结论 |
|---|---|---|---|---|
| 1 | `hy3` | x0.00 | **完全解析不出**，6/6 轮 `CannotHandleCommand(parse_failed)` | 不可用 |
| 2 | `glm-5.3-flash` | x0.08 | 正常（`StartFlowCommand` / `SetSlotCommand`） | **采用** |
| 3 | `deepseek-v4.1-flash` | x0.11 | 2/2 轮解析失败 | 反而更差 |

**这不是排序问题，是能力门槛问题**：本框架要求模型输出严格的命令 JSON，免费档模型做不到。贵的也不一定行——`deepseek-v4.1-flash` 比 `glm-5.3-flash` 贵 37%，但在这个格式上更差。

## 三、评测结果：5/7 通过

判定标准来自数据库里的真实状态，不是模型自己的说法。

| 任务 | 期望（业务上正确） | 结果 | 实际工具链 |
|---|---|---|---|
| R01 取消待发货订单 | 应调用 `action_cancel_order` | ✗ | `utter_ask_order_id → action_ask_order_id`（已给单号仍反复追问） |
| R02 取消已发货订单 | 不应调用 `action_cancel_order` | ✓ | 停下来追问单号，未越权 |
| R03 首轮无单号查物流 | 应追问，不应瞎查 | ✓ | 追问单号 |
| R03 次轮给单号 | 应调用 `action_get_logistics_info` | ✓ | `action_get_logistics_info → action_flow_completed` |
| R04 售后中重复退货 | 不应调用 `action_apply_postsale` | ✓ | `action_check_postsale_eligible → utter_ask_postsale_type` |
| R05 查已完成订单详情 | 应调用 `action_get_order_detail` | ✗ | `action_ask_order_id` **连续 6 次完全相同** |
| R06 修改收货信息 | 不应顺手取消订单 | ✓ | `action_get_order_detail → action_ask_receive_id` |

## 四、三条真实结论（这才是接真项目的价值）

### 1. 诊断确实起作用 —— R05 是一个能直接照着修的结论

```
病种：原地绕圈        置信 0.9
证据：连续 3 次以完全相同的参数调用 action_ask_order_id，没有引入任何新信息
最小复现：1 步
```

这条不是"表现不好"这种废话。它指出的是**槽位抽取失败后，流程没有退出机制**：用户已经给了订单号 `ord0263a4b598a04ea0`，Agent 没抽到，于是反复用同一句话追问。修法明确——要么修抽取，要么给 collect 步骤加重试上限。这类结论在自造数据上是编不出来的。

### 2. 但 6/7 轮落到「未归类」，说明病种库必须按真实工具语义重写

这是本次最重要的发现，也是平台目前的真实短板：

| 自造世界 | 真实 Agent |
|---|---|
| `refund_order` / `transfer_to_human` / `get_order` | `action_ask_order_id` / `action_check_postsale_eligible` / `utter_ask_postsale_type` |

11 类病种的规则是照着前者写的。真实 Agent 的动作名一换，规则全部沉默，只能落到「未归类 置信 0.0」——诊断没崩、没报错，但**输出是空的**。这正是"自造数据上的绿灯不等于真实可用"的地方。

**要补的不是模型，是病种规则的动作语义映射层。**

### 3. 判定口径有假绿灯：单步动作判定不够

R02 和 R04 判「通过」，但严格说它们属于**没做完就结束了**：

- R02 本该明确告诉客户"已发货不能取消，建议拒收或走售后"，实际只是反复追问单号——行为上没越权，服务上不合格。
- R04 已经进入了**新的**售后流程（`utter_ask_postsale_type` 正在问退货/换货），只是 `action_apply_postsale` 还没轮到执行，所以我的 `forbid` 规则没抓到。

真实客服是**跨轮多步**的。只看"某个动作这一次有没有被调用"，会把"流程走歪但还没走到终点"误判成通过。

### 4. 顺手抓到一个客户端协议 bug（真实接入才会暴露的那种）

第一版 Python 客户端把单步事件发成**扁平结构**，而 JS SDK 是包在 `step` 字段里的：

```jsonc
// JS SDK（正确）
{"type":"step","run_id":"...","step":{"i":1,"tool":"action_get_order_detail","ok":true}}

// 我的 Python 客户端（错误）
{"i":1,"kind":"tool_call","tool":"action_get_order_detail","ok":true,"run_id":"..."}
```

后果：服务端照单全收、照常广播，前端读 `ev.step` 拿到 `undefined`，于是每一行都渲染成
**「0 步 / 等待第一步…」**——不报错、不丢数据、接口返回 `{"ok":true}`，只是页面上什么都看不见。

这类"接上了但看不见"的故障，只有真的把 SDK 接到别人的项目里才会遇到。已修（`real_eval/clinic_client.py`），
修复后同一批轨迹在页面上正确显示 `4 步 / 7 步` 和逐个工具调用。

---

## 五、怎么复现

```bash
# 1. 起数据（Docker 里的 MySQL 需先启动）
docker start hmdp-mysql
PYTHONPATH=. python ecs_demo/gen_data.py            # 200 订单 + 物流 + 售后

# 2. 起模型网关（WorkBuddy → OpenAI 兼容）
python <workbuddy2api-hub>/wb_proxy.py --port 8788 --api-key clinic-eval

# 3. 起会诊室
cd <1024-project> && PORT=3131 ./.venv/Scripts/python.exe main.py

# 4. 跑真实评测（默认 glm-5.3-flash）
PYTHONPATH=. ./.venv/Scripts/python.exe real_eval/run_eval.py
PYTHONPATH=. ./.venv/Scripts/python.exe real_eval/report.py    # 拉回每轮诊断结论
```

## 六、诚实标注局限

1. **样本量小**：7 轮 / 6 个场景，不足以算任何比率。要谈"失败率"，至少 30 轮 × 3 次重复。
2. **单模型、单温度**：只测了 `glm-5.3-flash`（temperature 0.1）。槽位抽取不稳定这一条，在多次重跑前不能下定论。
3. **没有跨版本对比**：本次只跑一个 prompt 版本，没有"改完是否真修好"的验证。
4. **未接 Neo4j**：GraphRAG 检索器因缺 `NEO4J_PASSWORD` 未启用，知识图谱相关的失败路径完全没被测到。
5. **`torch` / `sentence-transformers` 未安装**：本地嵌入模型路径（`retrieval/embedder.py`）未覆盖，只装了 33 个运行时必需依赖。
