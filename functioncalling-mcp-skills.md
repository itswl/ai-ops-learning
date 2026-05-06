# Function Calling、MCP、Skills 详解

> 创建时间: 2026-05-07

---

## 为什么这三个概念重要

对于运维工程师，这三者构成了「让 AI 从聊天机器人变成能干活的操作员」的核心链路：

```
Function Calling → 模型能"说"要调什么函数（基础能力）
      ↓
MCP              → 统一工具接口标准，对接运维系统（连接层）
      ↓
Skill            → 把运维知识+工具打包成可复用能力（封装层）
```

没这条链路，AI 就只能聊天，不能帮你查日志、查 Pod、执行运维操作。

---

## 一、Function Calling（函数调用）

### 是什么

大模型最底层的「行动能力」——让模型输出结构化 JSON 来描述要调哪个函数、传什么参数，而不是只能输出自然语言文字。

### 没有 Function Calling 的 LLM

```
你：「生产环境有哪些 Pod 在 CrashLoopBackOff？」

LLM 输出（文字，无法执行）：
  「你可以用 kubectl get pods -n prod | grep CrashLoopBackOff 来查看」
  
问题：它只是告诉你命令，没有真正执行。数据是编的，命令也可能有错。
```

### 有 Function Calling 的 LLM

```
你：「生产环境有哪些 Pod 在 CrashLoopBackOff？」

1. 模型分析意图 → 返回结构化 JSON（不面向用户）：
   {
     "function": "list_pods",
     "parameters": {
       "namespace": "prod",
       "status_filter": "CrashLoopBackOff"
     }
   }

2. 你的程序收到这个 JSON → 执行真正的 kubectl 命令：
   $ kubectl get pods -n prod --field-selector=status.phase!=Running
   
3. 命令结果喂回给模型：
   "PodA: CrashLoopBackOff (OOMKilled)
    PodB: CrashLoopBackOff (ImagePullBackOff)"

4. 模型基于真实数据生成回答：
   「当前生产环境有 2 个异常 Pod：
    - PodA：OOMKilled，建议检查内存限制
    - PodB：ImagePullBackOff，建议检查镜像仓库连通性」
```

### 技术本质

就是让 LLM 多了一种输出格式——它不再只输出文字，而是能输出一个「函数调用指令」。

```python
# OpenAI 兼容 API 的 Function Calling 示例
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="not-needed")

# 定义可用工具
tools = [
    {
        "type": "function",
        "function": {
            "name": "list_pods",
            "description": "列出指定命名空间中状态异常的 Pod",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {
                        "type": "string",
                        "description": "K8s 命名空间，如 prod、staging"
                    },
                    "status_filter": {
                        "type": "string",
                        "enum": ["CrashLoopBackOff", "Pending", "OOMKilled", "ImagePullBackOff"]
                    }
                },
                "required": ["namespace"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_pod_logs",
            "description": "获取指定 Pod 的最近日志",
            "parameters": {
                "type": "object",
                "properties": {
                    "pod_name": {"type": "string"},
                    "tail_lines": {"type": "integer", "default": 100}
                },
                "required": ["pod_name"]
            }
        }
    }
]

# 模型决定是否调用工具
response = client.chat.completions.create(
    model="qwen3-8b",
    messages=[
        {"role": "system", "content": "你是一个 K8s 运维助手，可以调用工具查询集群状态"},
        {"role": "user", "content": "查看一下生产环境有没有异常的 Pod"}
    ],
    tools=tools,
    tool_choice="auto",  # 模型自己判断是否需要调工具
)

# 模型返回的不是文字，而是函数调用指令
tool_call = response.choices[0].message.tool_calls[0]
print(tool_call.function.name)        # list_pods
print(tool_call.function.arguments)   # {"namespace": "prod", "status_filter": "CrashLoopBackOff"}

# 你的代码执行真正的 kubectl
import subprocess, json
result = subprocess.run(
    ["kubectl", "get", "pods", "-n", json.loads(tool_call.function.arguments)["namespace"]],
    capture_output=True, text=True
)

# 把执行结果喂回给模型，让它基于真实数据回答
response = client.chat.completions.create(
    model="qwen3-8b",
    messages=[
        {"role": "user", "content": "查看一下生产环境有没有异常的 Pod"},
        {"role": "assistant", "tool_calls": [tool_call]},
        {"role": "tool", "tool_call_id": tool_call.id, "content": result.stdout},
    ],
)
print(response.choices[0].message.content)
# 「当前 prod 命名空间有 2 个异常 Pod：PodA (CrashLoopBackOff)，PodB (Pending)...」
```

### 运维视角的关键认知

```
Function Calling 解决的是：模型「想做什么」到「真正执行」的桥梁

传统方式：
  模型输出文字 "建议你执行 kubectl get pods"
  → 你看文字 → 自己去执行 → 自己分析结果
  → LLM 就是一个「写提示文档的顾问」

Function Calling 方式：
  模型输出函数调用 → 程序自动执行 → 结果自动喂回
  → 模型基于真实数据给出分析
  → LLM 变成了「能亲自动手的工程师」
```

---

## 二、MCP（Model Context Protocol）

### 是什么

MCP 是 Anthropic 提出的一套**开放协议**，定义了 AI 应用和外部工具之间的标准通信方式。

**一句话**：MCP 之于 AI 工具，就像 HTTP 之于 Web，USB 之于外设——统一标准，插上就能用。

### 没有 MCP 的问题

```
每个 AI 应用都要为每个工具单独写对接代码：

  Claude Code 接 GitHub    → 写一套
  Claude Code 接 K8s       → 写一套
  Claude Code 接 Prometheus → 写一套
  ChatGPT 接 GitHub        → 又写一套
  ChatGPT 接 K8s           → 又写一套
  ChatGPT 接 Prometheus    → 又写一套

N 个 AI 应用 × M 个工具 = N×M 次对接
```

### 有了 MCP

```
每个工具只需要实现一次 MCP Server，任何支持 MCP 的 AI 应用都能用：

  GitHub    → 实现一个 MCP Server
  K8s       → 实现一个 MCP Server
  Prometheus → 实现一个 MCP Server

  Claude Code / ChatGPT / Cursor / Cline → 实现 MCP Client

N 个 AI 应用 + M 个工具 = N+M 次对接
```

### MCP 协议架构

```
┌─────────────────────────────────────────────┐
│                MCP Client（AI 应用侧）        │
│  Claude Code / ChatGPT / Cursor / IDE       │
│                                              │
│  职责：发现可用工具 → 调用 → 处理结果          │
└──────────────────┬──────────────────────────┘
                   │  JSON-RPC over stdio/HTTP
                   │
┌──────────────────┴──────────────────────────┐
│            MCP Server（工具侧）               │
│                                              │
│  每个 Server 暴露三类能力：                    │
│  ├── Resources（资源）：可读取的数据           │
│  │   如：集群配置、文档、Prometheus 指标        │
│  ├── Tools（工具）：可执行的操作               │
│  │   如：kubectl apply、重启服务、创建工单      │
│  └── Prompts（提示模板）：预定义的 Prompt      │
│      如：「帮我排查这个告警」                  │
└─────────────────────────────────────────────┘
```

### 已有的 MCP Server（运维相关）

| MCP Server | 能做什么 |
|---|---|
| **Kubernetes MCP** | 管理 Pod/Deploy/Service，查看日志，执行 kubectl |
| **Prometheus MCP** | 查询指标、查看告警、获取时间序列数据 |
| **GitHub MCP** | 管理 PR/Issue/Repo，查看代码 |
| **PostgreSQL MCP** | 执行 SQL 查询，管理数据库 |
| **Slack MCP** | 发送消息，读取频道，管理通知 |
| **Docker MCP** | 管理容器和镜像 |
| **AWS MCP** | 管理 EC2/S3/RDS 等 AWS 资源 |
| **Terraform MCP** | 管理 IaC 资源 |
| **Jira MCP** | 创建/查询工单 |

### MCP 对接示例（运维视角）

```json
// MCP Server 的 tool 定义（K8s 为例）
{
  "name": "rollback_deployment",
  "description": "回滚指定 Deployment 到上一个版本",
  "inputSchema": {
    "type": "object",
    "properties": {
      "name": {"type": "string", "description": "Deployment 名称"},
      "namespace": {"type": "string", "description": "命名空间"},
      "reason": {"type": "string", "description": "回滚原因（会记录到审计日志）"}
    },
    "required": ["name", "namespace", "reason"]
  }
}
```

LLM 通过 MCP 调用时：
1. MCP Client 发现 Server 提供了 `rollback_deployment` 工具
2. 用户说「把刚才那个出问题的 api-gateway 回滚一下」→ LLM 决定调用
3. MCP Client 通过 JSON-RPC 调 Server → Server 执行 `kubectl rollout undo`
4. 结果返回给 LLM → LLM 告诉用户回滚结果

### MCP 和 Function Calling 的关系

```
Function Calling = 模型的「发音器官」——能说出要调什么函数
MCP              = 统一的「通信协议」——定义了函数调用的标准格式

历史关系：
  Function Calling 先出现（OpenAI 2023）
  → 各厂商自己定义自己的格式（OpenAI 格式、Anthropic 格式…）
  → MCP 出现（Anthropic 2024）→ 统一了这个格式
  
现在：
  MCP 不仅统一了工具格式，还统一了「发现工具」「读取资源」的全套协议
  成为 AI 工具生态的 HTTP 协议
```

---

## 三、Skills（技能）

### 是什么

Skill 是**最上层的封装**——把「专业 Prompt + 可用工具 + 执行流程 + 输出规范」打包成一个可复用的能力单元。

### 运维类比

```
Function Calling = Shell 的内置命令（echo、ls、cat）
                   → 原子操作
MCP              = 标准输入输出协议（stdin/stdout/stderr）
                   → 通信标准
Skill            = Shell 脚本（ops-health-check.sh）
                   → 封装了流程、经验、最佳实践
```

### Skill 里面有什么

一个完整的 Skill 包含：

```
Skill: "生产环境健康检查"
├── 专业知识（Prompt 模板）
│   "你是运维专家，请按以下清单逐项检查生产环境健康状态…"
│
├── 工具清单（通过 MCP 调用）
│   ├── Prometheus MCP → 查 CPU/内存/QPS 指标
│   ├── K8s MCP → 查 Pod 状态、事件
│   ├── PostgreSQL MCP → 查数据库连接数、慢查询
│   └── Slack MCP → 发送检查报告
│
├── 执行流程
│   1. 查 K8s 集群资源使用率
│   2. 查所有命名空间的异常 Pod
│   3. 查数据库连接池状态
│   4. 查最近 1 小时告警
│   5. 汇总生成报告
│   6. 发送到运维频道
│
└── 输出规范
    ├── 异常项用红色标注
    ├── 指标偏离超过 20% 需要高亮
    └── 无异常时只发送摘要
```

### 在 Claude Code 中的 Skill

你在 Claude Code 里看到的 `/review`、`/security-review` 就是 Skill：

```
/review        → 封装了 PR 审查的完整流程
/security-review → 封装了安全审计的检查清单
/init          → 封装了项目初始化的文档生成流程
```

### 为什么 Skill 对运维重要

运维的工作本质就是「经验 + 工具 + 流程」：

```
没有 Skill 时：
  每次故障都要：
    1. 手写 prompt 告诉模型你的职责
    2. 手动指定要用哪些工具
    3. 自己组织排查流程
    4. 每次都要重复这些步骤

有了 Skill：
  → 一键调用"故障排查 Skill"
  → Skill 自动加载运维专家的角色设定
  → Skill 自动按标准流程调用 Prometheus、K8s、日志等工具
  → Skill 自动按规范格式输出排查报告
```

---

## 三者关系总结

```
┌──────────────────────────────────────────────────────────┐
│                      Skill（技能层）                       │
│          「帮我做一次生产环境健康检查」                       │
│          封装：专业知识 + 工具组合 + 执行流程 + 输出规范      │
│                                                          │
│   ┌─────────────────────────────────────────────────┐   │
│   │               MCP（协议层）                       │   │
│   │        统一的工具接口标准和通信协议                  │   │
│   │    K8s MCP / Prometheus MCP / Slack MCP …       │   │
│   │                                                  │   │
│   │  ┌──────────────────────────────────────────┐   │   │
│   │  │       Function Calling（能力层）           │   │   │
│   │  │     让模型输出结构化工具调用指令              │   │   │
│   │  │     {"function": "list_pods", ...}        │   │   │
│   │  └──────────────────────────────────────────┘   │   │
│   └─────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

| 维度 | Function Calling | MCP | Skill |
|---|---|---|---|
| **是什么** | 模型的能力 | 通信协议标准 | 能力封装包 |
| **类比** | 系统调用（syscall） | HTTP 协议 | Shell 脚本 |
| **谁定义** | 模型厂商 | Anthropic（开放标准） | 用户/社区 |
| **解决什么问题** | 让模型能"说要做什么" | 让工具能被任意模型调用 | 让经验可以被复用 |
| **依赖关系** | 底层基础 | 依赖 FC 机制 | 依赖 MCP 工具 |
| **运维视角** | 原子操作（查 Pod） | 对接标准（K8s MCP） | 自动化流程（健康检查） |

### 一句话区分

- **Function Calling**：让模型有了「手」，能说要执行什么操作
- **MCP**：定义了「手」和「工具」之间的握手机制，统一了工具标准
- **Skill**：把「专业经验 + 正确工具 + 标准流程」打包，一键调用

---

## 实践路径建议

对于运维工程师，建议按这个顺序上手：

```
第 1 步：理解 Function Calling
  用一个简单 Python 脚本，给 LLM 挂一个 kubectl 工具
  体验「模型自动决定调哪个函数」的完整链路

第 2 步：接入 MCP
  部署一个 K8s MCP Server
  让 Claude Code 或支持 MCP 的 IDE 连上
  用自然语言查询 K8s 集群状态

第 3 步：编写 Skill
  把最常见的一个运维流程（如故障排查），封装成 Skill
  包含：检查清单 + 工具调用链 + 输出模板
  之后每次故障一键调用
```
