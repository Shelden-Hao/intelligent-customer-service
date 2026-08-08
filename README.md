# Intelligent Customer Service — LLM 驱动的企业级智能对话系统

<div align="center">

**基于 LLM + Flow 编排 + 可插拔动作体系的智能客服对话框架**

`LLM 负责理解 · 框架负责控制 · 开发者负责业务`

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![LangGraph](https://img.shields.io/badge/LangGraph-%E2%89%A50.2.0-purple)
![FastAPI](https://img.shields.io/badge/FastAPI-%E2%89%A50.100-blue)

</div>

---

## 项目简介

**Intelligent Customer Service** 是一套 **LLM 驱动的任务型对话系统框架**，专为企业级智能客服场景设计。与传统基于"意图分类 + 状态机"的 Pipeline 架构不同，本框架让大语言模型（LLM）直接承担自然语言理解工作，将用户输入翻译为**结构化命令**，再由 **Flow 流程引擎** 精确控制对话走向，最终由**可插拔的 Action 动作体系**执行业务逻辑。

框架自带完整的电商客服参考实现（`ecs_demo/`），覆盖订单查询、物流跟踪、售后修改收货信息等真实场景，开箱即用。

---

## 核心特性

| 特性 | 说明 |
|------|------|
| 🧠 **LLM 原生理解** | 无需意图分类模型，LLM 直接输出结构化命令（`StartFlow` / `SetSlots` / `KnowledgeAnswer` 等），自动提取槽位 |
| 🛤️ **Flow 流程编排** | YAML 声明式定义对话流程，支持 7 种步骤类型（ACTION / COLLECT / CONDITION / LINK / CALL / SET_SLOT / END），**改业务只改 YAML 不改代码** |
| 🔄 **对话栈管理** | LIFO 对话栈原生支持 **Flow 嵌套、中断恢复**——用户在任务中途插入提问，回答后自动回到原流程 |
| 🧩 **可插拔动作体系** | 15+ 内置 Action + 注册表机制，自定义 Action 放入 `actions/` 目录**自动发现注册**，零配置接入 |
| 🎯 **多策略决策** | `PolicyEnsemble` 按优先级调度（FlowPolicy → EnterpriseSearchPolicy），带 `abstain` 放弃机制与**降级兜底链**（Flow → 检索 → 闲聊 → 默认话术） |
| 📚 **RAG 检索增强** | 极简检索器接口（`connect` + `search`），支持向量库 / Neo4j GraphRAG / 自定义检索源 |
| 🚪 **多渠道接入** | REST API（FastAPI）/ WebSocket（Socket.IO）/ 命令行（Shell）三种通道，同一 Agent 零改动切换 |
| 💾 **状态持久化** | TrackerStore 支持内存 / JSON / MySQL 三种存储，会话可跨请求恢复 |
| 🎨 **NLG 两层架构** | TemplateNLG（模板 + 槽位替换）+ ResponseRephraser（LLM 重述润色，内置长度与槽位保留校验） |

---

## 架构设计

### 整体架构

```mermaid
graph TB
    subgraph "接入层 Channels"
        REST[RestChannel<br/>FastAPI]
        WS[SocketIOChannel<br/>WebSocket]
        CLI[ConsoleChannel<br/>命令行]
    end

    subgraph "核心引擎 Agent"
        G[LangGraph 消息处理图]
        U[understand<br/>LLM命令生成]
        P[policy<br/>策略决策]
        A[action<br/>动作执行]
        GD[guard<br/>循环守卫]
        R[response<br/>响应输出]
    end

    subgraph "状态层"
        TR[DialogueStateTracker]
        ST[对话栈 DialogueStack]
        TS[TrackerStore<br/>memory/JSON/MySQL]
    end

    subgraph "业务层"
        FL[Flow 流程定义 YAML]
        ACT[Action 动作注册表]
        PL[PolicyEnsemble 策略集]
        NLG[NLG 生成器]
        RAG[检索器 InformationRetrieval]
    end

    REST --> G
    WS --> G
    CLI --> G
    U --> P --> A --> GD --> P
    G --> R
    U --> TR
    A --> TR
    TR --> ST
    TR --> TS
    P --> PL
    A --> ACT
    A --> NLG
    P --> RAG
    PL --> FL
```

### 单条消息处理流程

```mermaid
sequenceDiagram
    participant U as 用户
    participant AG as Agent
    participant LG as LangGraph图
    participant UN as understand节点
    participant PP as PolicyEnsemble
    participant AC as Action
    participant TR as Tracker

    U->>AG: 用户消息
    AG->>TR: update_with_message()
    AG->>LG: graph.invoke(state)
    LG->>UN: LLM生成命令 → 解析 → 执行命令
    UN->>TR: 更新状态（压栈帧/设槽位）
    UN->>PP: 命令处理结果
    PP->>PP: 按优先级选择策略，产出 PolicyPrediction
    PP->>AC: action 名称 → 注册表取实例 → run()
    AC->>TR: 写回事件（槽位、消息）
    AC-->>UN: ActionResult
    UN->>LG: guard 判断是否继续循环
    LG-->>AG: final_responses
    AG-->>U: 回复文本
```

---

## 核心概念

### Command 与 Action 的边界

| 维度 | Command（命令） | Action（动作） |
|------|----------------|----------------|
| 来源 | **LLM** 根据用户输入生成 | **Policy** 根据栈帧状态选择 |
| 职责 | 解析用户意图，**更新对话状态** | 执行具体操作，**生成响应** |
| 输出 | 直接改 Tracker（压栈帧、设槽位） | 返回 ActionResult（事件、响应） |

> **Command 是"LLM 说的话"，Action 是"系统做的事"**。两者通过对话栈衔接——Command 压入栈帧，Policy 看到帧选 Action。

### 七种 Flow 步骤类型

| 类型 | 作用 | 示例 |
|------|------|------|
| `ACTION` | 执行动作 | `action: action_query_order` |
| `COLLECT` | 收集槽位（唯一"等用户"的步骤，多轮的来源） | `collect: order_id` |
| `CONDITION` | 条件分支 | `if: slots.balance >= total_price` |
| `LINK` | 切换 Flow（不返回） | `link: query_logistics` |
| `CALL` | 调用子 Flow（完成后返回） | `call: collect_address` |
| `SET_SLOT` | 程序化设置槽位 | `set_slot: {order_status: processing}` |
| `END` | 结束 | `next: END` |

### 策略降级链

```
用户消息 → Flow 匹配? → 是 → FlowPolicy（优先级 100）
                   → 否 → 知识库检索? → 找到 → RAG 回答
                                     → 未找到 → 闲聊识别? → 是 → 闲聊回复
                                                           → 否 → 默认降级话术
```

---

## 目录结构

```
intelligent_customer_service/
├── setup.py                  # 安装配置（pip install -e .）
├── requirements-ai.txt       # 依赖清单
├── ai_service/               # 核心 Python 包
│   ├── agent/                # Agent 核心 + LangGraph 图编排
│   │   ├── agent.py          # Agent 总指挥官（装配所有组件）
│   │   ├── actions.py        # 15+ 内置 Action + 注册表
│   │   ├── message_processor.py  # 消息处理封装
│   │   └── graph/            # LangGraph 5 节点图（understand/policy/action/guard/response）
│   ├── api/                  # FastAPI 服务（REST 接口）
│   ├── channels/             # 多渠道（REST / WebSocket / Console）
│   ├── cli/                  # 命令行工具（init/train/run/shell/inspect/export）
│   ├── core/                 # 核心数据结构
│   │   ├── tracker.py        # DialogueStateTracker（对话状态追踪器）
│   │   ├── domain.py         # Domain 定义
│   │   ├── events.py         # 事件系统
│   │   └── stores/           # TrackerStore（memory/JSON/MySQL）
│   ├── dialogue_understanding/  # 对话理解层
│   │   ├── commands/         # 命令系统（StartFlow/SetSlots/KnowledgeAnswer…）
│   │   ├── flow/             # Flow 定义 / 加载器 / 执行器
│   │   ├── generator/        # LLMCommandGenerator（Prompt 构建 + 命令解析）
│   │   ├── processor/        # CommandProcessor（命令执行）
│   │   └── stack/            # 对话栈（6 种栈帧 + 状态机）
│   ├── nlg/                  # 自然语言生成（TemplateNLG + ResponseRephraser）
│   ├── policies/             # 策略系统（FlowPolicy / EnterpriseSearchPolicy / Ensemble）
│   ├── retrieval/            # RAG 检索（InformationRetrieval / Embedder）
│   ├── shared/               # 共享工具（LLM 客户端、常量、配置）
│   └── training/             # 训练模块
├── ecs_demo/                 # 电商客服示例项目
│   ├── config.yml            # Pipeline / 策略配置
│   ├── endpoints.yml         # LLM / Neo4j / MySQL / 存储配置
│   ├── domain/               # Domain 定义（订单/物流/售后/模式）
│   ├── data/flows/           # Flow 定义（订单/物流/售后）
│   ├── actions/              # 自定义 Action（自动发现加载）
│   ├── addons/               # 扩展模块（GraphRAG 检索器）
│   └── gen_data.py           # 测试数据生成脚本
└── 讲解笔记/                  # 项目完整讲解文档（01-07）
```

---

## 快速开始

### 环境要求

| 项目 | 版本 |
|------|------|
| Python | 3.10+ |
| 包管理 | pip / conda |
| 可选依赖 | MySQL 8.x（持久化）、Neo4j 5.x（GraphRAG） |

### 1. 安装

```bash
# 进入项目根目录
cd intelligent_customer_service

# 创建虚拟环境（推荐）
conda create -n ai_service python=3.10 -y
conda activate ai_service

# 开发模式安装（修改源码即时生效）
pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple

# 或普通安装
pip install . -i https://pypi.tuna.tsinghua.edu.cn/simple
```

> 💡 **注意**：`torch`（PyTorch）安装体积较大，若不使用本地嵌入模型（`sentence-transformers`），可注释掉 `requirements-ai.txt` 中的 `torch>=2.0.0`。

### 2. 配置环境变量

```bash
# 编辑 ecs_demo/endpoints.yml，或设置环境变量
export DASHSCOPE_API_KEY=your_key      # 通义千问（示例项目默认使用）
export NEO4J_PASSWORD=your_password     # GraphRAG（Neo4j 数据库密码）
export MYSQL_PASSWORD=your_password     # MySQL 持久化
export EMBEDDING_MODEL=./models/bge-base-zh-v1.5   # 本地嵌入模型路径（Neo4j 索引构建用）
```

### 3. 导入业务数据（必需）

示例项目依赖 MySQL（业务数据）与 Neo4j（知识图谱）两个数据源，`data_import/` 目录已提供现成的数据文件：

```
data_import/
├── ecs.sql        # MySQL 建库建表脚本（含基础数据）
└── neo4j.dump     # Neo4j 知识图谱数据备份
```

#### 3.1 导入 MySQL 数据

```bash
# ① 连接 MySQL，创建数据库并导入表结构与基础数据
mysql -u root -p < data_import/ecs.sql

# ② 修改业务代码中的数据库连接信息（默认 localhost:3306/ecs/root/123321）
#    文件：ecs_demo/actions/db.py 和 ecs_demo/gen_data.py
#    db_host / db_port / db_name / db_user_name / db_password 改为你自己的配置

# ③ 生成模拟业务数据（订单、物流、收货信息等）
cd ecs_demo
python gen_data.py
cd ..
```

> 💡 `ecs.sql` 已包含建库（`ecs`）、建表与基础数据（区域表、物流公司表、投诉对照表等）；`gen_data.py` 负责生成随机订单/物流/用户模拟数据并写入数据库，供 Flow 与 Action 查询使用。

#### 3.2 导入 Neo4j 知识图谱数据

**① 安装 APOC 插件**（Neo4j 图谱导入与查询依赖）：

```bash
# 将 Neo4j 安装目录下 labs/ 中的 apoc*.jar 拷贝到同级 plugins/ 目录
# 在 conf/neo4j.conf 中添加：
#   dbms.security.procedures.unrestricted=apoc.*
#   dbms.security.procedures.allowlist=apoc.*
neo4j restart
```

**② 导入图谱数据**（需停止 Neo4j 后执行）：

```bash
neo4j stop

# 将 data_import/neo4j.dump 导入 Neo4j（注意 --from-path 指向 dump 文件所在目录）
neo4j-admin database load --from-path="C:/Codefield/intelligent_customer_service/data_import" \
  --overwrite-destination=true neo4j --verbose

neo4j start
```

**③ 构建向量 / 全文索引**（可选，GraphRAG 检索需要）：

```bash
# 修改 ecs_demo/addons/create_indexing.py 中的 Neo4j 连接信息后执行
cd ecs_demo
python addons/create_indexing.py
```

### 4. 启动交互式对话（推荐先行验证）

```bash
ai shell ecs_demo/
```

### 5. 启动 REST API 服务

```bash
ai run ecs_demo/ --port 5005
# 访问 http://localhost:5005/docs 查看 Swagger 文档
```

---

## 配置说明

### config.yml（Pipeline 与策略）

```yaml
recipe: default.v1
language: zh

# 理解层：LLM 命令生成器
pipeline:
  - name: LLMCommandGenerator
    llm: default          # 引用 endpoints.yml 中的模型配置

# 决策层：策略（按 priority 从高到低）
policies:
  - name: FlowPolicy               # 优先级 100：执行 Flow 流程
  - name: EnterpriseSearchPolicy   # 优先级 50：检索/闲聊/降级
    llm: default
    vector_store: addons.information_retrieval.GraphRAG
```

### endpoints.yml（外部资源）

```yaml
# LLM 模型
models:
  default:
    type: qwen             # 支持 qwen / openai / azure / anthropic
    model: qwen-plus
    api_key: ${DASHSCOPE_API_KEY}   # 使用环境变量，绝不硬编码密钥
    temperature: 0.1

# 向量存储（Neo4j）
vector_store:
  uri: bolt://localhost:7687
  user: neo4j
  password: ${NEO4J_PASSWORD}

# 关系数据库（MySQL）
database:
  url: mysql+pymysql://root:${MYSQL_PASSWORD}@localhost:3306/ecommerce

# 会话状态存储
tracker_store:
  type: memory             # memory / json / mysql

# NLG 配置
nlg:
  rephrase_enabled: false  # 是否启用 LLM 重述润色
```

---

## 命令行工具

安装后提供 `ai` 命令：

```bash
ai init ecs_demo/            # 初始化项目脚手架
ai train --config ecs_demo/config.yml --validate   # 训练模型
ai shell ecs_demo/           # 交互式对话测试（开发调试）
ai run ecs_demo/ --port 5005 # 启动 REST API 服务（生产）
ai inspect ecs_demo/         # 启动调试页面
ai export ecs_demo/ -o models/   # 导出模型/配置
```

---

## REST API

服务默认运行在 `http://localhost:5005`：

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 |
| `GET` | `/` | 服务信息 |
| `POST` | `/api/messages` | 发送消息（核心接口） |
| `GET` | `/api/sessions/{session_id}` | 获取会话状态 |
| `POST` | `/api/sessions/{session_id}/reset` | 重置会话 |
| `GET` | `/api/domain` | 获取 Domain 定义 |
| `GET` | `/api/flows` | 获取 Flow 列表 |
| `GET` | `/api/tracker/{session_id}/full` | 获取完整 Tracker 状态（调试用） |

**发送消息示例：**

```bash
curl -X POST http://localhost:5005/api/messages \
  -H "Content-Type: application/json" \
  -d '{"message": "我想查一下我的订单", "sender_id": "user_001"}'
```

---

## 开发指南

### 1. 自定义 Flow

在项目的 `data/flows/` 目录新建 YAML 文件：

```yaml
# data/flows/flow_coupon.yml
flows:
  query_coupon:
    name: 查询优惠券
    description: 查询用户可用优惠券
    steps:
      - collect: coupon_status
        ask_before_filling: true
        next:
          - if: slots.coupon_status != "false"
            then:
              - action: action_query_coupons
                next: END
          - else: END
```

### 2. 自定义 Action

在项目的 `actions/` 目录新建 Python 文件——**无需手动注册**，Agent 启动时自动发现并注册：

```python
# actions/coupon_actions.py
from ai_service.agent.actions import Action


class ActionQueryCoupons(Action):
    """查询用户优惠券。"""

    @property
    def name(self) -> str:
        return "action_query_coupons"

    async def run(self, tracker, domain=None, **kwargs):
        from ai_service.core.events import BotUttered

        user_id = tracker.get_slot("user_id")
        coupons = await self._fetch_coupons(user_id)  # 你的业务逻辑

        if not coupons:
            return [BotUttered(text="您当前没有可用优惠券")]
        return [BotUttered(text=f"您有 {len(coupons)} 张可用优惠券")]
```

> 接口约定：`run()` 返回事件列表（如 `BotUttered` 发消息、`SlotSet` 写槽位）；方法为 `async`，可异步执行数据库/API 调用。

### 3. 自定义检索器

继承 `InformationRetrieval`，实现两个方法即可：

```python
# addons/information_retrieval/my_retriever.py
from ai_service.retrieval.base_retriever import InformationRetrieval, SearchResult


class MyRetriever(InformationRetrieval):
    def connect(self, config=None):
        # 初始化向量索引 / 数据库连接
        ...

    async def search(self, query, top_k=5, tracker_state=None):
        # tracker_state 含 slots / 对话历史，可做个性化检索
        return [SearchResult(text=..., score=..., metadata={"source": ...})]
```

---

## 示例项目（电商客服）

`ecs_demo/` 提供完整的电商客服参考实现：

| 模块 | 文件 | 能力 |
|------|------|------|
| 订单 | `flow_order.yml` + `action_order.py` | 订单详情查询、状态跟踪 |
| 物流 | `flow_logistics.yml` + `action_logistics.py` | 物流信息查询 |
| 售后 | `flow_postsale.yml` + `action_postsale.py` | 修改收货信息（姓名/电话/地址） |
| 知识检索 | `addons/information_retrieval.py` | Neo4j GraphRAG 知识图谱检索 |
| 数据 | `gen_data.py` | 生成模拟订单/物流数据 |

修改收货信息是展示 **Flow 嵌套分支**的最佳案例：`collect: modify_content` 后按"姓名/电话/地址"三分支，改地址时连续收集省/市/区/街道四个槽位，全程 YAML 声明、零代码。

---

## 常见问题

**Q1: LLM 生成的命令解析失败（日志显示 "No commands parsed from LLM output"）**
- 检查 Prompt 模板，增加 few-shot 示例
- 降低 `temperature`（如 0.1）
- 增加 `max_tokens` 限制

**Q2: Flow 执行死循环（日志显示 "Max steps exceeded in flow"）**
- 检查 Flow 的 `next` 字段是否具备终止条件
- 为条件分支补充 `else` 分支
- 使用 `END` 显式结束流程
- 框架内置保险丝：`max_steps_per_turn=20`、`max_actions=10`

**Q3: 会话状态丢失**
- 确认 `tracker_store.type` 配置为 `json` 或 `mysql`（`memory` 类型重启即失）

**Q4: 检索不到结果**
- 检查向量索引是否已构建（Neo4j 需先运行建索引脚本）
- 降低 `similarity_threshold` 阈值
- 确认 `top_k` 参数合理

---

## 许可证

[MIT](LICENSE)
