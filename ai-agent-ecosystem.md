# AI Agent 生态全景：Agent、记忆、安全、可观测性

> 创建时间: 2026-05-07

---

## 概念全景

围绕「让 AI 能干运维活」这条主线，以下是超过 Function Calling / MCP / Skills 之外的完整概念图谱：

```
┌─────────────────────────────────────────────────────────┐
│  用户交互层                                               │
│  Chat UI / API / 企业微信/飞书/钉钉机器人                   │
├─────────────────────────────────────────────────────────┤
│  能力封装层                                               │
│  Skill / Plugin / GPTs                                  │
├─────────────────────────────────────────────────────────┤
│  智能体层（Agent）                                        │
│  单Agent范式：ReAct / Plan-Execute / Tool-Use Loop       │
│  Multi-Agent：AutoGen / CrewAI / Swarm                  │
│  记忆系统：短期记忆 / 长期记忆 / 工作记忆                   │
├─────────────────────────────────────────────────────────┤
│  编排层（Orchestration）                                  │
│  LangGraph / Dify工作流 / Temporal                       │
│  状态管理 / 循环分支 / 人机协同 / 断点续跑                   │
├─────────────────────────────────────────────────────────┤
│  工具层                                                   │
│  MCP / Function Calling / Tool Use / 结构化输出           │
├─────────────────────────────────────────────────────────┤
│  知识层                                                   │
│  RAG / Vector DB / GraphRAG / 知识图谱                    │
├─────────────────────────────────────────────────────────┤
│  安全与控制层                                             │
│  Guardrails / AI Gateway / 审计日志 / 沙箱                │
│  输入过滤 / 输出校验 / 权限控制 / 成本管控                   │
├─────────────────────────────────────────────────────────┤
│  可观测性层                                               │
│  LangFuse / LangSmith / MLflow                           │
│  链路追踪 / Token用量 / 效果评估 / 回归测试 / Prompt版本管理  │
├─────────────────────────────────────────────────────────┤
│  模型层                                                   │
│  LLM / Embedding / Reranker / 微调 / 多模态               │
└─────────────────────────────────────────────────────────┘
```

---

## 一、Agent（智能体）

### 1.1 Agent 是什么

**一句话**：Agent 是能自主规划、多步执行、循环推理的 AI 实体。它不只是回答一个问题，而是能自己拆解任务、调用工具、检查结果、调整策略。

**和普通 LLM 调用的区别**：

```
普通 LLM 调用：
  你问 → 模型答 → 结束
  单回合，无自主行动

Agent：
  你给目标 → Agent 自己拆解步骤
    → 步骤1: 调工具查信息 → 得到结果
    → 步骤2: 分析结果，发现不够，换工具再查
    → 步骤3: 信息够了，汇总成答案
    → 结束
  多回合，有自主推理和纠错
```

### 1.2 Agent 三大核心范式

#### 范式一：ReAct（Reasoning + Acting）

最经典的 Agent 范式——思考和行动交替进行。

```
ReAct 循环：
  Thought（思考）：我接下来该做什么？
    → Action（行动）：调 kubectl get pods
    → Observation（观察）：得到 2 个异常 Pod
  Thought（再思考）：这 2 个 Pod 的状态意味着什么？需要查日志吗？
    → Action：调 kubectl logs PodA
    → Observation：日志显示 OOMKilled
  Thought：根因是 OOM，给用户建议
    → Final Answer
```

**运维场景——故障排查**：

```python
# ReAct Agent 伪代码
def react_agent(user_query: str):
    messages = [{"role": "user", "content": user_query}]
    
    while True:
        response = llm.chat(messages, tools=available_tools)
        
        if response.is_final_answer:
            return response.content
        
        if response.has_tool_call:
            # 执行工具调用
            result = execute_tool(response.tool_call)
            messages.append({"role": "tool", "content": result})
            # 模型观察结果后继续思考 → 循环
        
        if loop_count > max_loops:
            return "推理轮次超限"
```

#### 范式二：Plan-Execute（先规划再执行）

先制定完整计划，再按计划逐步执行，每步执行后检查结果。

```
用户：「把昨天所有异常 Pod 重启一下，并确认恢复」

Plan-Execute 过程：
  Plan（规划阶段）：
    1. 查询昨天有哪些 Pod 异常
    2. 对每个异常 Pod 确认当前状态
    3. 重启仍在异常的 Pod
    4. 验证重启后的 Pod 健康状态
    5. 汇总报告
  
  Execute（执行阶段）：
    步骤1: kubectl get events --since=24h → 得到异常 Pod 列表
    步骤2: 逐个 kubectl get pod -o yaml → 确认状态
    步骤3: kubectl rollout restart → 执行重启
    步骤4: kubectl get pods -w → 等待 Running
    步骤5: 生成报告
```

#### 范式三：Tool-Use Loop（工具调用循环）

简化版 Agent——没有显式的推理步骤，就是「调工具→看结果→调工具→看结果」的循环。

```python
# 工具调用循环
def tool_use_loop(query):
    messages = [{"role": "user", "content": query}]
    
    while True:
        response = llm.chat(messages, tools=tools, tool_choice="auto")
        
        if response.no_tool_calls:
            return response.content  # 模型认为不需要调工具了
        
        for tool_call in response.tool_calls:
            result = execute(tool_call)
            messages.append({"role": "tool", "result": result, "id": tool_call.id})
```

### 1.3 Multi-Agent（多智能体）

多个 Agent 像团队一样分工协作，每个 Agent 专注于一个子任务。

**运维场景——完整的故障响应团队**：

```
┌─────────────────────────────────────────┐
│         指挥 Agent（Leader）              │
│  接收告警 → 分配任务 → 汇总报告           │
└──────────┬──────────────────────────────┘
           │
    ┌──────┼──────┬──────────┐
    ↓      ↓      ↓          ↓
┌──────┐ ┌────┐ ┌──────┐ ┌────────┐
│诊断   │ │日志 │ │指标   │ │公告    │
│Agent  │ │Agent│ │Agent  │ │Agent   │
│       │ │     │ │       │ │        │
│查事件  │ │查ELK│ │查普罗  │ │写故障   │
│查Pod   │ │分析 │ │查Go  │ │公告    │
│状态    │ │报错 │ │rfana │ │通知相关 │
│       │ │     │ │       │ │人      │
└──────┘ └────┘ └──────┘ └────────┘
```

#### 主流 Multi-Agent 框架

| 框架               | 特点                 | 适用              |
| ---------------- | ------------------ | --------------- |
| **AutoGen**      | 微软出品，多 Agent 对话式协作 | 企业级多角色协作        |
| **CrewAI**       | 轻量，角色定义清晰，上手快      | 中小团队快速搭建        |
| **LangGraph**    | 用图定义 Agent 间流转     | 需要精细控制 Agent 交互 |
| **OpenAI Swarm** | OpenAI 实验性框架，极简    | 学习和实验           |
| **Dify Agent**   | 低代码拖拽多 Agent       | 非技术人员也能搭        |

#### CrewAI 示例：运维故障响应

```python
from crewai import Agent, Task, Crew, Process

# 1. 定义 Agent
diagnostics_agent = Agent(
    role="故障诊断工程师",
    goal="根据告警信息快速定位故障根因",
    backstory="你有 10 年运维经验，擅长从现象推断根因",
    tools=[kubectl_tool, prometheus_tool],
    llm=llm,
)

logs_agent = Agent(
    role="日志分析工程师",
    goal="从海量日志中提取和故障相关的关键信息",
    backstory="你擅长用正则和模式匹配在日志中定位异常",
    tools=[elk_tool, grep_tool],
    llm=llm,
)

comm_agent = Agent(
    role="故障公告发布员",
    goal="根据故障情况撰写清晰准确的公告",
    backstory="你擅长把技术问题用业务语言描述",
    tools=[slack_tool, email_tool],
    llm=llm,
)

# 2. 定义任务
diagnose_task = Task(
    description="分析 API 服务 502 告警，确定根因",
    expected_output="故障根因分析报告，含可能性排序",
    agent=diagnostics_agent,
)

logs_task = Task(
    description="提取 502 错误发生前后 15 分钟的异常日志",
    expected_output="关键日志摘要，按时间线排列",
    agent=logs_agent,
    depends_on=[diagnose_task],  # 等诊断完成后查日志
)

comm_task = Task(
    description="根据根因和日志分析，撰写故障公告",
    expected_output="故障公告草稿，含影响范围和预计恢复时间",
    agent=comm_agent,
    depends_on=[diagnose_task, logs_task],
)

# 3. 组建 Crew
crew = Crew(
    agents=[diagnostics_agent, logs_agent, comm_agent],
    tasks=[diagnose_task, logs_task, comm_task],
    process=Process.sequential,
)

result = crew.kickoff()
```

---

## 二、记忆系统（Memory）

Agent 如果没有记忆，每次对话都是「失忆」状态——不知道上下文、不记得之前的操作、无法积累经验。

### 三种记忆类型

```
┌────────────────────────────────────────────────────┐
│              短期记忆（Short-term Memory）           │
│  当前对话的上下文窗口                                │
│  记住「刚才做了什么」→ 指导下一步                    │
│  实现：对话历史 messages 列表                        │
├────────────────────────────────────────────────────┤
│              长期记忆（Long-term Memory）            │
│  跨对话的持久化记忆                                  │
│  「上次 Redis 故障排查的经验」→ 下次直接复用         │
│  实现：向量数据库 + RAG                              │
├────────────────────────────────────────────────────┤
│              工作记忆（Working Memory）              │
│  当前任务中的中间状态                                │
│  「已查了 Pod A，还需要查 Pod B」→ 记住进度          │
│  实现：LangGraph State / Redis Cache                │
└────────────────────────────────────────────────────┘
```

### 运维 Agent 的记忆示例

```python
# Agent 记忆系统示意
class OpsAgent:
    def __init__(self):
        self.short_term = []           # 本轮对话历史
        self.long_term = VectorDB()    # 历史故障经验库
        self.working = {}              # 当前任务中间状态
    
    def handle_incident(self, alert: str):
        # 1. 短期记忆：当前对话上下文
        self.short_term.append({"role": "user", "content": alert})
        
        # 2. 长期记忆：检索相似历史故障
        similar_cases = self.long_term.search(alert, top_k=3)
        
        # 3. 结合当前上下文 + 历史经验做判断
        prompt = f"""
        历史相似故障：{similar_cases}
        当前对话：{self.short_term}
        任务状态：{self.working}
        
        请判断下一步该做什么
        """
        action = llm.chat(prompt)
        
        # 4. 更新工作记忆
        self.working["last_check"] = action
        
        # 5. 故障解决后存入长期记忆
        if action == "resolved":
            self.long_term.insert({
                "alert": alert,
                "resolution": self.working["resolution"],
                "timestamp": now()
            })
```

### 记忆实现方案

| 类型   | 实现方式              | 工具                      |
| ---- | ----------------- | ----------------------- |
| 短期记忆 | 对话窗口（messages 数组） | 框架自带                    |
| 长期记忆 | 向量数据库 + RAG       | Milvus + BGE-M3         |
| 工作记忆 | KV 存储 / State 对象  | Redis / LangGraph State |
| 摘要记忆 | 对长对话做摘要压缩后保留      | LLM summarize           |

---

## 三、安全与控制层

### 3.1 Guardrails（安全护栏）

在模型输入之前和输出之后各加一道安全检查。

```
用户输入 → [输入护栏] → LLM 推理 → [输出护栏] → 最终输出

输入护栏检查：
  ├── 是否包含越狱/注入攻击？→ 拒绝
  ├── 是否包含敏感信息（密码/密钥）？→ 脱敏
  └── 是否超出权限范围？→ 限制

输出护栏检查：
  ├── 是否包含敏感信息泄露？→ 过滤
  ├── 是否符合格式要求？→ 重试
  ├── 是否存在幻觉/矛盾？→ 标注不确定性
  └── 命令是否危险（如 rm -rf /）？→ 拦截
```

#### Guardrails-AI 实战

```bash
pip install guardrails-ai
```

```python
from guardrails import Guard
from guardrails.hub import (
    ToxicLanguage,      # 毒性语言过滤
    ValidJson,          # JSON 格式校验
    RegexMatch,         # 正则匹配（脱敏）
    CompetitorCheck,    # 竞品信息过滤
)

# 定义输出护栏
guard = Guard().use_many(
    ToxicLanguage(threshold=0.8),
    RegexMatch(
        regex=r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
        match_type="search",
        on_fail="fix",  # 自动替换为 [IP地址]
    ),
)

# 使用
result = guard(
    llm_api=llm.chat,
    prompt="分析这个 Nginx 日志...",
    messages=messages,
)

# 如果输出包含 IP，自动脱敏；包含不当内容，自动拦截
```

#### 运维场景的特定护栏

```python
# 运维操作 Guardrails
DANGEROUS_COMMANDS = [
    r"rm\s+-rf\s+/",           # 删除根目录
    r"kubectl\s+delete\s+ns",  # 删除命名空间
    r"DROP\s+(TABLE|DATABASE)",# 删库
    r"shutdown\s+-h\s+now",    # 关机
]

def ops_input_guard(user_input: str) -> bool:
    """检查用户是否在让 AI 执行危险操作"""
    for pattern in DANGEROUS_COMMANDS:
        if re.search(pattern, user_input, re.IGNORECASE):
            return False, f"拦截：检测到危险操作模式 '{pattern}'"
    return True, "安全"

def ops_output_guard(model_output: str) -> str:
    """过滤模型输出中的危险命令"""
    for pattern in DANGEROUS_COMMANDS:
        model_output = re.sub(
            pattern,
            "[此命令已被安全策略拦截]",
            model_output,
            flags=re.IGNORECASE
        )
    return model_output
```

### 3.2 AI Gateway（AI 服务网关）

AI Gateway 之于 AI 服务，就像 Nginx/Kong 之于 Web 服务——统一的入口，负责限流、路由、认证、成本控制。

```
用户/应用
    ↓
┌──────────────────────┐
│    AI Gateway         │
│                      │
│  功能：              │
│  ├── 统一认证 + API Key 管理         │
│  ├── 限流（按用户、按模型、按 Token）  │
│  ├── 路由（便宜任务→小模型，复杂→大模型）│
│  ├── 成本追踪（每个调用花了多少钱）    │
│  ├── 请求/响应日志（审计用）          │
│  ├── 缓存（相同问题返回缓存结果）      │
│  ├── 降级（主模型挂了切备模型）        │
│  └── 请求改写/脱敏                    │
└──────┬───────────────┘
       │
  ┌────┼────┬──────────┐
  ↓    ↓    ↓          ↓
GPT-4 Claude DeepSeek  vLLM本地
```

#### 主流 AI Gateway

| 方案 | 特点 | 适用 |
|---|---|---|
| **LiteLLM** | 开源，统一 100+ LLM 的 API 格式 | **推荐，运维最友好** |
| **Portkey** | 企业级，带可观测性 | 生产环境 |
| **Kong AI Gateway** | 基于 Kong，接了 AI 插件 | 已有 Kong 基础设施 |
| **Cloudflare AI Gateway** | 免费额度大，全球节点 | 小型项目 |
| **自建 Nginx + Lua** | 灵活性最高 | 有定制需求的团队 |

#### LiteLLM 实战（推荐）

```bash
pip install litellm
```

```yaml
# litellm_config.yaml
model_list:
  - model_name: gpt-4o
    litellm_params:
      model: azure/gpt-4o
      api_key: os.environ/AZURE_API_KEY
      rpm: 100          # 每分钟请求限制
      tpm: 100000       # 每分钟 Token 限制
  
  - model_name: qwen3-8b
    litellm_params:
      model: openai/qwen3-8b
      api_base: http://gpu-server:8000/v1
      api_key: not-needed
      rpm: 500
  
  - model_name: deepseek-v3
    litellm_params:
      model: deepseek/deepseek-chat
      api_key: os.environ/DEEPSEEK_API_KEY

# 路由规则：简单问题走小模型，复杂问题走大模型
router_settings:
  routing_strategy: "cost-based"  # 或 latency-based
  allowed_fails: 3                # 重试次数
  fallbacks:
    - gpt-4o: deepseek-v3         # GPT 挂了切 DeepSeek
```

```bash
# 启动 Gateway
litellm --config litellm_config.yaml --port 4000

# 客户端只需调 Gateway，不用关心后端
curl http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer sk-xxx" \
  -d '{"model": "qwen3-8b", "messages": [...]}'
```

```
LiteLLM 给运维的价值：
  ├── 所有 AI 调用统一入口，一处管理限流和认证
  ├── 成本可视化：哪个团队花了最多 Token？
  ├── 模型切换透明：GPT 涨价了？一行配置切到 DeepSeek
  ├── 降级保护：模型挂了自动 fallback
  └── 内置 /metrics → Prometheus 采集
```

---

## 四、可观测性层

传统可观测性（`llm-ops-guide.md` 里的 Prometheus+Grafana）监控的是基础设施。这里说的是**AI 应用层的可观测性**——能看到 Agent 的每一步在做什么。

### 4.1 核心工具

| 工具 | 特点 | 适用 |
|---|---|---|
| **LangFuse** | 开源，最推荐，支持自部署 | ★ 首选 |
| **LangSmith** | LangChain 官方，商业产品 | LangChain 深度用户 |
| **MLflow** | 老牌ML平台，新增 LLM Tracing | 已有 MLflow 的团队 |
| **Weights & Biases** | 训练跟踪强，新增 LLM 支持 | 有模型训练需求 |
| **Phoenix (Arize)** | 开源，注重 Embedding 分析 | RAG 效果调优 |

### 4.2 LangFuse 实战

```bash
# 自部署
docker run -d -p 3000:3000 \
  -e DATABASE_URL=postgresql://... \
  -e NEXTAUTH_SECRET=mysecret \
  langfuse/langfuse:latest

# 访问 http://localhost:3000
```

```python
# 在 Agent 代码中集成 LangFuse
from langfuse import Langfuse
from langfuse.decorators import observe, langfuse_context

langfuse = Langfuse(
    public_key="pk-xxx",
    secret_key="sk-xxx",
    host="http://localhost:3000",
)

# 方式一：装饰器自动追踪
@observe(name="故障诊断Agent")
def diagnose_incident(alert: str):
    # LangFuse 自动追踪这个函数的输入、输出、耗时
    result = agent.run(alert)
    
    # 记录元数据
    langfuse_context.update_current_trace(
        tags=["production", "incident"],
        metadata={"alert_type": "502", "severity": "P1"},
    )
    
    return result

# 方式二：手动创建 Span（精细控制）
trace = langfuse.trace(name="故障排查完整流程")

# Span 1: 查询 Prometheus
span1 = trace.span(name="查询指标", input={"query": "up{job='api'}"})
metrics = prometheus_client.query("up{job='api'}")
span1.end(output=metrics)

# Span 2: 查询日志
span2 = trace.span(name="查询日志", input={"time_range": "15min"})
logs = elk_client.search("ERROR", time_range="15min")
span2.end(output={"log_count": len(logs)})

# Span 3: LLM 推理
span3 = trace.span(name="LLM分析")
llm_response = llm.chat(f"根据指标 {metrics} 和日志 {logs} 分析根因")
span3.end(output=llm_response, metadata={"tokens": 1500, "cost": 0.003})
```

### LangFuse 能看到什么

```
LangFuse Dashboard：
├── Trace 列表：每次 Agent 执行的完整时间线
│   故障排查 Agent (2.3s)
│   ├── 查询 Prometheus  (0.3s)
│   ├── 查询 ELK 日志     (1.2s)
│   ├── LLM 分析          (0.7s, 1500 tokens, ¥0.003)
│   └── 发布公告到 Slack  (0.1s)
│
├── 效果评估：
│   ├── 用户评分（👍/👎）
│   ├── 自动评估指标（忠实度、准确性）
│   └── 幻觉率趋势
│
├── 成本追踪：
│   ├── 按模型：GPT-4o ¥120/天, Qwen3-8B ¥8/天
│   ├── 按用户/团队
│   └── Token 用量趋势
│
└── Prompt 管理：
    ├── 版本历史
    ├── A/B 测试
    └── 效果对比
```

### 4.3 Prompt 版本管理

运维 AI 系统里，Prompt 就是「配置」。像管理 Nginx 配置一样管理 Prompt：

```python
# LangFuse Prompt 管理
from langfuse import Langfuse

langfuse = Langfuse()

# 获取生产版本 Prompt（类似获取 nginx.conf）
prompt = langfuse.get_prompt("ops-incident-analyzer", version="production")
system_msg = prompt.compile(severity="P1", service="api-gateway")

# A/B 测试：尝试新版本 Prompt
prompt_v2 = langfuse.get_prompt("ops-incident-analyzer", version="v2-beta")
system_msg_v2 = prompt_v2.compile(severity="P1")

# 两个版本的答案效果在 LangFuse Dashboard 中对比
# 确定 v2 更好 → 发布 v2 为新的 production 版本
```

```
运维 Prompt 管理最佳实践：
├── Git 管理 Prompt 模板（像管理代码一样）✅
├── 改 Prompt 前先在测试集上跑一遍 ✅
├── A/B 测试对比新旧 Prompt 效果 ✅
├── 记录每次 Prompt 变更的上线时间和效果变化 ✅
└── Prompt 回滚像配置回滚一样快 ✅
```

---

## 五、结构化输出

### 是什么

Function Calling 返回的是「建议调用的函数」，结构化输出更进一步——强制模型按 JSON Schema 输出合法的结构化数据。

```
Function Calling：
  模型输出：{"function": "list_pods", "parameters": {...}}
  语义："我建议调用 list_pods 这个函数"

结构化输出：
  模型输出：{"pods": [...], "summary": "...", "severity": "high"}
  语义："我给你一份结构化的回答，Pod列表、摘要、严重性都有"
```

### 为什么重要

运维系统之间的通信都是结构化的——JSON、YAML、API。如果让同事「看」AI 的大段文字再人工处理，就没自动化了。

**场景对比**：

```
场景：AI 自动巡检后更新 CMDB 状态

没有结构化输出（✗ 不可靠）：
  LLM 文字输出："我认为 api-gateway 服务需要被标记为 degraded 状态"
  → 你写正则去解析这段文字 → 正则匹配错了 → CMDB 状态更新错误

有结构化输出（✓ 可靠）：
  LLM JSON 输出：
  {
    "service": "api-gateway",
    "status": "degradated",
    "reason": "P95 延迟从 200ms 升到 2s",
    "evidence": "Prometheus 查询结果: ...",
    "confidence": 0.92
  }
  → 程序直接 json.loads() → 精准更新 CMDB
```

### 实现方式

```python
from openai import OpenAI
from pydantic import BaseModel

# 1. 定义输出结构（Pydantic 模型）
class IncidentReport(BaseModel):
    service: str
    status: str          # "healthy" | "degradated" | "down"
    severity: str        # "P0" | "P1" | "P2"
    root_cause: str
    affected_services: list[str]
    suggested_actions: list[str]
    confidence: float    # 0-1

client = OpenAI()

# 2. 调用时指定 response_format
response = client.chat.completions.create(
    model="qwen3-8b",
    messages=[{
        "role": "user",
        "content": "分析 Prometheus 告警：api-gateway P95 延迟 > 2s"
    }],
    response_format={"type": "json_schema", "json_schema": IncidentReport.model_json_schema()},
)
# 输出保证是合法的 IncidentReport JSON

report = IncidentReport.model_validate_json(response.choices[0].message.content)
print(report.status)      # "degradated"
print(report.confidence)  # 0.92
# 可以直接写入 CMDB
```

### 和 Function Calling 的区别

| | Function Calling | 结构化输出 |
|---|---|---|
| 模型输出 | 函数名 + 参数 | 任意 JSON 结构 |
| 用途 | 触发外部工具执行 | 返回结构化的响应 |
| 典型场景 | 调 API、查数据库、执行命令 | 数据提取、报告生成、状态更新 |

---

## 六、多模态

### 是什么

让模型不仅能读文字，还能看图、听声音。对于运维来说，最有价值的是**图片理解能力**。

### 运维场景

| 场景 | 传统方式 | 多模态 AI |
|---|---|---|
| 告警截图分析 | 人工看 Grafana 截图 | AI 直接读截图，提取关键指标 |
| 架构图理解 | 手动维护架构文档 | 拍一张架构图 → AI 生成结构化描述 |
| 监控大屏异常 | 人工盯着看 | AI 定时截屏分析，异常自动报警 |
| 错误截屏 | 截屏发给同事问 | AI 直接识别报错信息 |
| 硬件指示灯 | 摄像头监控 | AI 识别异常指示灯颜色 |

### 多模态模型

| 模型 | 能力 |
|---|---|
| GPT-4o / GPT-4.1 | 图+文输入，综合最强 |
| Claude Sonnet/Opus | 图+文，长图/文档截图识别强 |
| Gemini 2.5 Pro | 图+文+视频+音频 |
| Qwen-VL | 国产开源，图文理解 |
| Llama 4 Scout/Maverick | 开源多模态 |

### 运维实战：告警截图自动分析

```python
import base64
from openai import OpenAI

def analyze_monitoring_screenshot(image_path: str) -> dict:
    """分析 Grafana 截图，提取异常指标"""
    
    # 读取图片并 base64 编码
    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode()

    client = OpenAI()
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": """请分析这张 Grafana 监控截图，提取：
1. 有哪些指标出现异常？
2. 异常发生的时间点
3. 异常程度（轻微/中等/严重）
4. 建议排查方向"""
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_b64}"}
                }
            ]
        }]
    )
    
    return response.choices[0].message.content
```

---

## 七、能力层级全景图

把前面几篇笔记的知识串起来，形成完整能力分层：

```
┌──────────────────────────────────────────────────────────┐
│  Level 5: 全自动运维                                       │
│  Multi-Agent 团队自主响应 + 故障自愈                        │
│  技术：AutoGen/CrewAI + MCP 工具链 + 记忆系统              │
├──────────────────────────────────────────────────────────┤
│  Level 4: Agent 驱动运维                                   │
│  单 Agent 自主推理 + 工具调用 + 故障排查                     │
│  技术：LangGraph/ReAct + Function Calling + RAG           │
├──────────────────────────────────────────────────────────┤
│  Level 3: 结构化 AI 运维                                   │
│  AI 输出结构化数据 → 直接对接 CMDB/监控/工单系统              │
│  技术：结构化输出 + Function Calling + MCP                │
├──────────────────────────────────────────────────────────┤
│  Level 2: 知识增强运维                                     │
│  RAG 知识库 + 模型推理 → 辅助决策                          │
│  技术：RAG + 向量数据库 + Reranker                        │
├──────────────────────────────────────────────────────────┤
│  Level 1: 基础 AI 助力                                     │
│  用 LLM 写脚本、分析日志、生成配置                          │
│  技术：直接调 LLM API / ChatGPT                           │
└──────────────────────────────────────────────────────────┘
```

### 从运维到 AI 运维的爬梯路线

```
大部分传统运维起点 → Level 1 → Level 2 → Level 3
                                        ↓
                              这就是「AI 运维工程师」
                              市场上最稀缺的交叉技能
```

---

## 八、工具速查总表

| 类别 | 工具 | 一句话 | 推荐度 |
|---|---|---|---|
| **Agent框架** | LangGraph | 图编排，状态管理 | ★★★★★ |
| | CrewAI | 多Agent协作 | ★★★★ |
| | AutoGen | 微软多Agent | ★★★★ |
| | Dify Agent | 低代码Agent | ★★★★ |
| **记忆** | LangGraph State | 工作记忆 | ★★★★ |
| | Mem0 | 长期记忆，开箱即用 | ★★★★ |
| | Zep | 企业级记忆平台 | ★★★ |
| **安全护栏** | Guardrails-AI | 输入输出校验 | ★★★★★ |
| | NVIDIA NeMo Guardrails | 企业级护栏 | ★★★★ |
| **AI Gateway** | LiteLLM | 统一API代理 | ★★★★★ |
| | Portkey | 企业网关+观测 | ★★★★ |
| | Kong AI | 已有Kong | ★★★ |
| **可观测性** | LangFuse | 开源，最强 | ★★★★★ |
| | LangSmith | LangChain官方 | ★★★★ |
| | MLflow | 老牌ML平台 | ★★★ |
| **Prompt管理** | LangFuse Prompt | 版本管理 | ★★★★★ |
| | Agenta | 开源Prompt实验 | ★★★ |
| **结构化输出** | Instructor | Pydantic+LLM | ★★★★★ |
| | Outlines | 约束生成 | ★★★★ |
| **多模态** | GPT-4o | 综合最强 | ★★★★★ |
| | Claude Sonnet | 文档/截图强 | ★★★★★ |
| | Qwen-VL | 国产开源 | ★★★★ |
