# AI + Ops 学习笔记

从运维工程师视角学习大模型与 AI Infra 的一套笔记，覆盖「懂原理 → 会部署 → 能落地运维智能体」三层。

> 内容最初由 AI 辅助生成，2026-08 做过一轮系统性人工纠错：修正了编造的模型型号/API/仓库地址、错误的显存与量化数据、过时的工具用法，并重写了与真实项目不符的章节（见各文件内的勘误标注）。技术细节仍建议以官方文档为准。

## 目录

### 基础认知

| 文件 | 内容 |
|---|---|
| [ops-ai-career.md](ops-ai-career.md) | 运维 × AI 的职业方向、大模型基础概念（Transformer/训练/GPU）、开源闭源模型选型 |
| [llm-hardware-guide.md](llm-hardware-guide.md) | 硬件选型：显存估算、量化、KV Cache、典型预算方案与企业场景 |

### 部署与运维

| 文件 | 内容 |
|---|---|
| [llm-ops-guide.md](llm-ops-guide.md) | 大模型服务运维：GPU/vLLM 监控指标与告警、量化、蒸馏、压测（EvalScope/Locust）、安全 |
| [llm-fine-tuning-guide.md](llm-fine-tuning-guide.md) | 微调实战：LoRA/QLoRA 原理、数据集制作、超参、LLaMA-Factory 与 Unsloth 全流程 |

### 深水区（进阶）

| 文件 | 内容 |
|---|---|
| [llm-inference-internals.md](llm-inference-internals.md) | 推理系统内部机制（SRE 视角）：调度器与 chunked prefill、KV cache/抢占/前缀缓存、roofline 性能模型、量化 runtime、投机解码、KV 感知路由、PD 分离生态（Dynamo/llm-d/Mooncake） |
| [gpu-cluster-ops.md](gpu-cluster-ops.md) | GPU 集群运维：软件栈分层排障、Xid/ECC 故障处置手册与自愈流水线、NCCL/IB/RoCE、K8s 调度 2026 版图（DRA GA/MIG/Kueue/KAI）、真实利用率（MFU/MBU）、训练容错与 goodput |

### 可运行原型

| 目录 | 内容 |
|---|---|
| [ai-sre-prototype/](ai-sre-prototype/) | 把第八阶段深水区落成能跑的代码：RCA 评估流水线（含"证据不足"样本，演示改坏 prompt→指标下降）+ 五层防御的 L1 修复动作。后端本地 vLLM；零依赖 mock 模式离线可跑 |

### RAG

| 文件 | 内容 |
|---|---|
| [rag-guide.md](rag-guide.md) | RAG 原理：切块、Embedding、向量库选型、高级检索技术、评估 |
| [rag-implementation-guide.md](rag-implementation-guide.md) | RAG 落地：Milvus、FastGPT、RAGFlow、开发框架（LangChain/LlamaIndex/DSPy）、GraphRAG |

### Agent 与运维智能体

| 文件 | 内容 |
|---|---|
| [functioncalling-mcp-skills.md](functioncalling-mcp-skills.md) | Function Calling / MCP / Skills 三层概念与关系 |
| [ai-agent-ecosystem.md](ai-agent-ecosystem.md) | Agent 生态全景：范式、Multi-Agent、记忆、Guardrails、AI Gateway、LangFuse 可观测性 |
| [coze-aiops-agent.md](coze-aiops-agent.md) | 运维智能体实战：Coze+阿里云/Ansible、Dify+JumpServer/K8s（MCP）、n8n+Prometheus/Jenkins |
| [openclaw-aiops-guide.md](openclaw-aiops-guide.md) | OpenClaw（个人 AI 助手网关）用于 ChatOps：真实部署方式、IM 接入、权限边界设计 |

## 建议阅读路径

```
ops-ai-career（建立全景认知）
    ↓
llm-hardware-guide + llm-ops-guide（把模型跑起来、管起来）
    ↓
rag-guide → rag-implementation-guide（知识库问答落地）
    ↓
functioncalling-mcp-skills → ai-agent-ecosystem（Agent 原理与工程化）
    ↓
coze-aiops-agent / openclaw-aiops-guide（挑一条路线做出自己的运维智能体）
    ↓
llm-inference-internals + gpu-cluster-ops（深水区：从"会用"到"能定量归因"）
```

## 与 cloud-native-ai-sre-roadmap 的关系

本仓库是"AI 方向的技术笔记"；[cloud-native-ai-sre-roadmap](https://github.com/itswl/cloud-native-ai-sre-roadmap) 是"按阶段推进的学习路线（含实验和验收标准）"。路线仓库的第七、八阶段（AI Infra / AIOps）可配合本仓库对应笔记使用。
