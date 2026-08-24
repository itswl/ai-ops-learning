# RAG 落地实战：Milvus + FastGPT + RAGFlow

> 创建时间: 2026-05-06

---

## 6.1 RAG 基础

### 回顾 RAG 核心流程

```
建库（离线）：文档 → 解析 → 切块 → Embedding → 向量数据库
问答（在线）：问题 → Embedding → 检索 → 拼接 Prompt → LLM 生成
```

前文 `rag-guide.md` 已详细拆解了 RAG 的原理、Embedding 选型、高级技术等，这里不再重复。本节聚焦于**用成熟工具将 RAG 真正落地到生产**。

### 落地 RAG 的三种路径

| 路径          | 代表工具                        | 适合谁                  |
| ----------- | --------------------------- | -------------------- |
| **手写代码**    | LangChain + Chroma + OpenAI | 需要极致定制、有开发团队         |
| **低代码平台**   | FastGPT / Dify              | 快速搭建、非技术人员也能维护       |
| **深度文档解析型** | RAGFlow                     | 复杂文档（PDF 表格/扫描件）、企业级 |

本章覆盖后两种路径——FastGPT 和 RAGFlow，以及它们底层依赖的核心组件 Milvus。

---

## 6.2 向量数据库 Milvus

### 6.2.1 了解向量数据库

#### 向量数据库解决什么问题

传统数据库做的是精确匹配：

```sql
SELECT * FROM docs WHERE title = 'Redis故障处理手册';
```

向量数据库做的是**语义相似**：

```
查询: "Redis 主从切换失败怎么办"
  ↓ Embedding
向量: [0.03, -0.41, 0.78, ...]
  ↓ ANN 检索
返回:
  1. "Redis sentinel 故障切换流程" (相似度 0.93)
  2. "Redis 主节点宕机处理方案" (相似度 0.89)
  3. "Redis Cluster 故障恢复手册" (相似度 0.82)
```

#### 为什么选 Milvus

| 特性        | 说明                                      |
| --------- | --------------------------------------- |
| **云原生架构** | 计算存储分离，存储/计算/索引可独立扩缩                    |
| **十亿级向量** | 工业验证支持 10B+ 向量，真正企业级                    |
| **多种索引**  | IVF_FLAT / IVF_PQ / HNSW / DiskANN 等近十种 |
| **元数据过滤** | 标量过滤 + 向量检索混合查询                         |
| **多租户**   | Partition Key / Collection 天然支持多租户      |
| **生态丰富**  | LangChain / LlamaIndex / FastGPT 全部内置支持 |
| **社区活跃**  | LF AI 基金会毕业项目，全球最流行的向量数据库之一             |

#### 核心概念

```
Milvus
├── Collection（集合）= 数据库中的「表」
│   ├── Schema（结构定义）
│   │   ├── id: int64 (主键)
│   │   ├── vector: float_vector(1024)  ← 向量字段
│   │   ├── text: varchar               ← 原文
│   │   └── metadata: json              ← 元数据（来源、时间等）
│   └── Index（索引类型：IVF_FLAT / HNSW 等）
│
├── Partition（分区）= 按条件逻辑分隔数据
│   可以按时间、来源、业务线等分区
│
└── Search / Query
    先标量过滤缩小范围，再向量检索
```

#### Milvus 部署模式对比

| 模式                    | 适用规模           | 部署复杂度          | 说明         |
| --------------------- | -------------- | -------------- | ---------- |
| **Milvus Lite**       | < 1M 向量        | 一行 pip install | 嵌入模式，开发测试用 |
| **Milvus Standalone** | 1M - 100M 向量   | 单 Docker 容器    | 小团队生产      |
| **Milvus Cluster**    | 100M - 10B+ 向量 | K8s 集群         | 大规模企业级     |
| **Zilliz Cloud**      | 不限             | 零运维，全托管        | 商业托管版，不想运维 |

---

### 6.2.2 快速入门 Milvus

#### 方式一：Docker 快速启动（推荐开发/测试）

```bash
# 单机版，官方脚本（注意参数是 start，不是版本号）
curl -sfL https://raw.githubusercontent.com/milvus-io/milvus/master/scripts/standalone_embed.sh -o standalone_embed.sh
bash standalone_embed.sh start

# 验证：这个脚本只起一个容器（etcd 以嵌入模式跑在里面，存储用本地盘）
docker ps | grep milvus
# milvus-standalone       — Milvus 主服务（端口 19530, 9091）

# 如果想要经典的三容器架构（standalone + etcd + minio），
# 用官方 docker-compose.yml：
# wget https://github.com/milvus-io/milvus/releases/latest/download/milvus-standalone-docker-compose.yml -O docker-compose.yml
# docker compose up -d
```

#### 方式二：Milvus Lite（最轻量）

```bash
pip install pymilvus
```

```python
from pymilvus import MilvusClient

# 自动启动一个轻量 Milvus，数据存本地文件
client = MilvusClient("milvus_demo.db")

# 创建集合
client.create_collection(
    collection_name="ops_knowledge",
    dimension=1024,  # BGE-M3 输出 1024 维
)

# 后续用法和完整 Milvus 完全一样
```

#### 插入数据

```python
from pymilvus import MilvusClient

client = MilvusClient("milvus_demo.db")

# 准备数据：text 先 Embedding 得到 1024 维向量
data = [
    {
        "id": 1,
        "vector": [0.023, -0.451, ...],  # BGE-M3 Embedding 结果, 1024 维
        "text": "Redis 主从切换故障排查：检查 sentinel 日志...",
        "source": "故障复盘/2024-03.md",
        "tags": ["Redis", "故障", "主从切换"],
    },
    {
        "id": 2,
        "vector": [-0.112, 0.334, ...],
        "text": "Kubernetes Pod CrashLoopBackOff 排查步骤：1. kubectl describe pod...",
        "source": "运维手册/k8s.md",
        "tags": ["K8s", "Pod", "故障"],
    },
    # ... 更多数据
]

client.insert(collection_name="ops_knowledge", data=data)
```

#### 向量检索

```python
# 1. 先把问题向量化（用 BGE-M3）
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("BAAI/bge-m3")
question = "Redis 主节点挂了怎么恢复"
query_vector = model.encode(question).tolist()  # 1024 维

# 2. Milvus 检索
results = client.search(
    collection_name="ops_knowledge",
    data=[query_vector],
    limit=5,                    # 返回 Top-5
    output_fields=["text", "source", "tags"],  # 同时返回这些字段
)

for hits in results:
    for hit in hits:
        print(f"相似度: {hit['distance']:.3f}")
        print(f"内容: {hit['entity']['text'][:200]}...")
        print(f"来源: {hit['entity']['source']}")
        print("---")
```

#### 元数据过滤

```python
# 只查「故障类」文档
# 注意：tags 是数组，数组包含要用 json_contains；
# `tags in ["故障"]` 是判断整个 tags 值等于"故障"，对数组字段查不出结果，这是常见坑
results = client.search(
    collection_name="ops_knowledge",
    data=[query_vector],
    limit=5,
    filter='json_contains(tags, "故障")',      # 标量过滤
    output_fields=["text", "source", "tags"],
)

# 组合过滤：只看 2024-03 之后的故障文档
results = client.search(
    collection_name="ops_knowledge",
    data=[query_vector],
    limit=5,
    filter='json_contains(tags, "故障") and created_at > "2024-03-01"',
    output_fields=["text", "source", "tags", "created_at"],
)
```

#### 索引优化

```python
# 准备索引参数（没有索引时用暴力搜索，数据多了会很慢）
index_params = MilvusClient.prepare_index_params()

index_params.add_index(
    field_name="vector",
    index_type="HNSW",   # 最常用：高召回 + 快速
    metric_type="COSINE",
    params={
        "M": 16,          # 每个节点连接数（越大越准但越占内存）
        "efConstruction": 200,  # 构建时搜索宽度
    },
)

client.create_index(
    collection_name="ops_knowledge",
    index_params=index_params,
)

# 索引类型选择指南：
# HNSW：    精度最高，内存占用较大，推荐 100M 以内
# IVF_FLAT：内存友好，精度略低于 HNSW，推荐 100M-1B
# DiskANN： 磁盘索引，精度尚可但内存极省，推荐 1B+ 级别
```

#### 完整端到端示例

```python
# full_rag_milvus.py — Milvus 驱动的完整 RAG
from pymilvus import MilvusClient
from sentence_transformers import SentenceTransformer
from openai import OpenAI

# 1. 初始化
embed_model = SentenceTransformer("BAAI/bge-m3")
vector_db = MilvusClient("ops_knowledge.db")
llm = OpenAI(base_url="http://localhost:8000/v1", api_key="not-needed")

# 2. 建库（只跑一次）
docs = [
    {"id": i, "text": doc, "source": f"doc_{i}"}
    for i, doc in enumerate(load_documents())
]
for d in docs:
    d["vector"] = embed_model.encode(d["text"]).tolist()

vector_db.create_collection("ops_kb", dimension=1024)
vector_db.insert(collection_name="ops_kb", data=docs)

# 3. 问答
def ask(question: str) -> str:
    # 检索
    qv = embed_model.encode(question).tolist()
    hits = vector_db.search(
        collection_name="ops_kb",
        data=[qv],
        limit=5,
        output_fields=["text", "source"],
    )[0]

    context = "\n\n".join(
        f"[来源: {h['entity']['source']}]\n{h['entity']['text']}"
        for h in hits
    )

    # 生成
    response = llm.chat.completions.create(
        model="qwen3-8b",
        messages=[{
            "role": "user",
            "content": f"参考以下资料回答问题，无法确定请说明。\n\n资料：\n{context}\n\n问题：{question}"
        }],
    )
    return response.choices[0].message.content

print(ask("Redis 主从切换失败怎么排查？"))
```

---

## 6.3 基于 FastGPT 实现 RAG 落地

### 6.3.1 FastGPT 介绍与安装

#### 是什么

FastGPT 是一个开源的**知识库问答平台**，定位是「让不懂代码的人也能搭 RAG 应用」。带 Web UI，可视化配置知识库、工作流、对话调试。

```
FastGPT 的核心能力：
├── 知识库管理：上传文档 → 自动解析/切块/向量化
├── 可视化工作流：拖拽编排 RAG 流程
├── 多模型支持：OpenAI / Claude / 本地 vLLM / 国产模型
├── 对话调试：实时看检索到了什么、prompt 长什么样
└── API 输出：可一键导出为 API 供外部调用
```

#### 架构组件

```
FastGPT
├── FastGPT 主服务           — Web UI + API
├── MongoDB                  — 业务主库（用户、应用、对话记录）
├── PostgreSQL(pgvector) 或 Milvus/Zilliz — 知识库向量存储
└── LLM + Embedding 模型     — 可接 API 或本地服务

（注意：Mongo 是业务主库、PG 只负责向量，很多资料把这两个写反）
```

#### 安装部署

**方式一：Docker Compose（推荐）**

```bash
# 1. 创建目录
mkdir fastgpt && cd fastgpt

# 2. 下载部署文件（按向量库选 compose 变体：pgvector / milvus / zilliz）
#    注意：老教程里的 projects/app/data/deploy/ 路径不存在，正确路径是 deploy/docker/
curl -o docker-compose.yml \
  https://raw.githubusercontent.com/labring/FastGPT/main/deploy/docker/docker-compose-pgvector.yml
curl -o config.json \
  https://raw.githubusercontent.com/labring/FastGPT/main/projects/app/data/config.json

# 3. 编辑 docker-compose.yml 里的密码等环境变量

# 4. 启动
docker compose up -d

# 5. 访问 http://localhost:3000（默认账号 root，密码在 compose 的
#    DEFAULT_ROOT_PSW 环境变量里）
```

**模型接入**：新版 FastGPT（4.8.20+）推荐在**管理后台的"模型提供商"页面**里配置 LLM 和 Embedding（走内置 AI Proxy / OneAPI），不再需要手改 config.json。接本地 vLLM 就是加一个 OpenAI 兼容渠道：

```
渠道地址: http://host.docker.internal:8000/v1
模型名:   qwen3-8b
密钥:     任意占位

Embedding 同理，指向 http://host.docker.internal:8001/v1（部署的 BGE-M3 服务）
```

**方式二：阿里云/腾讯云一键部署**

FastGPT 在阿里云计算巢、腾讯云应用商店均有镜像，搜索「FastGPT」就能一键部署，省去运维。

---

### 6.3.2 快速上手 FastGPT

#### Step 1：创建知识库

```
FastGPT 控制台 → 知识库 → 新建
  ├── 名称：运维知识库
  ├── 向量模型：BGE-M3
  ├── 索引模型：HNSW
  └── 分块参数：
       块大小：512 token
       重叠：50 token
```

上传文档后，FastGPT 会自动完成：
1. 文档解析（PDF/Word/Markdown/TXT）
2. 文本切块
3. Embedding 向量化
4. 存入 Milvus

#### Step 2：创建应用

```
应用 → 新建 → 选择类型：
  ├── 简单对话：知识库 + LLM，最基础的 RAG
  ├── 工作流编排：拖拽自定义流程（推荐进阶使用）
  └── API 访问：生成 API Key 供外部调用
```

#### Step 3：配置应用

进入应用 → 应用设置：

| 配置项           | 说明                | 推荐值        |
| ------------- | ----------------- | ---------- |
| **关联知识库**     | 选择刚创建的运维知识库       | -          |
| **检索相似度**     | 低于此值的结果丢弃         | 0.4-0.6    |
| **检索数量**      | Top-K 返回几条        | 3-5        |
| **Prompt 模板** | 决定 LLM 怎么使用检索到的内容 | 见下方        |
| **温度**        | 回答的随机性            | 0（知识问答要稳定） |
| **对话限制**      | 单次携带的历史轮数         | 5-10       |

**推荐的 Prompt 模板**：

```markdown
你是一个运维专家助手。你的回答**必须基于以下参考资料**。
如果资料中没有相关信息，请明确说「根据现有知识库，我无法确定」，严禁编造。

## 参考资料
{{quote}}

## 对话历史
{{history}}

## 当前问题
{{question}}

## 你的回答
（请引用资料中的具体内容，标注出处）
```

#### Step 4：调试

FastGPT 的调试面板会显示：
- 用户问题
- 检索到了哪些文档片段（含相似度分数和来源）
- 最终拼出的完整 prompt
- 模型的完整输出

这是排查 RAG 效果的核心——能看到**到底检索对了没有、prompt 长什么样**。

#### Step 5：导出 API

```
应用 → API 访问 → 生成 API Key

curl http://localhost:3000/api/v1/chat/completions \
  -H "Authorization: Bearer fastgpt-xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "chatId": "xxx",
    "stream": false,
    "messages": [
      {"role": "user", "content": "Redis 主从切换失败怎么办"}
    ]
  }'
```

---

### 6.3.3 项目实战：搭建运维故障知识库

#### 场景描述

虚构练习场景：某公司运维团队积累了 500+ 篇故障复盘文档，分布在 Confluence、语雀、GitLab Wiki 中。每次新人值班遇到故障都要翻半天文档。目标是搭建一个**内部运维故障知识库问答系统**。

#### 实施步骤

**第 1 步：数据收集与清洗**

```bash
# 从 Confluence 导出（Python 脚本）
# 从语雀导出
# 从 GitLab Wiki clone 下来
# 统一转换为 Markdown 格式

# 文档命名规范：
# YYYY-MM-DD_服务名_故障简述.md
# 如：2024-03-15_Redis_主从切换导致服务不可用.md
```

```
文档模板建议：

# [故障] YYYY-MM-DD 服务名 - 故障简述

## 故障时间
2024-03-15 14:32 - 15:10

## 影响范围
生产环境 API 服务全部 502

## 根因
Redis 主节点 RDB 持久化导致 fork 子进程 OOM...

## 处理过程
1. 14:35 收到告警
2. 14:38 确认 Redis 不可达
3. 14:42 手动触发 sentinel failover
4. 14:50 服务恢复

## 解决方案
调整 maxmemory 策略，关闭 RDB 改用 AOF...

## 预防措施
增加内存告警，RDB 改为低峰期执行
```

**第 2 步：知识库配置**

```
FastGPT → 知识库设置：
  块大小：1024 token（故障文档通常较长）
  重叠：100 token
  检索数量：5
  相似度阈值：0.5
  元数据过滤：启用（可按服务、日期范围筛选）
```

**第 3 步：应用配置**

```
Prompt 模板（针对故障场景优化）：

你是运维团队的故障排查助手。请根据参考资料中的历史故障记录，
帮助值班工程师快速定位问题。

## 要求
1. 优先返回与当前问题最相似的历史故障及解决方案
2. 如果有多个匹配，列出相似度排序
3. 如果历史记录中有相关预防措施，一并提供
4. 无法确定时明确告知

## 相似历史故障
{{quote}}

## 当前故障现象
{{question}}

## 分析建议
```

**第 4 步：效果评估**

选 20 个真实的历史故障，把故障描述丢进去，看 RAG 能不能检索出当时那篇复盘文档：

| 评估项 | 目标 | 实际 |
|---|---|---|
| Top-1 命中率 | > 70% | 需实测 |
| Top-5 命中率 | > 90% | 需实测 |
| 答案可用率 | > 80% | 需实测 |
| 幻觉率 | < 5% | 需实测 |

**第 5 步：集成到运维流程**

```
值班工程师遇到故障
    │
    ├── 直接打开 FastGPT 网页查询
    │
    ├── 或在企业微信/飞书/钉钉中 @机器人 提问
    │   （FastGPT 提供 API → 对接 IM 机器人）
    │
    └── 故障解决后，将复盘文档上传知识库
        形成「越用越好用」的正向循环
```

---

## 6.4 基于 RAGFlow 实现 RAG 落地

### 6.4.1 认识 RAGFlow

#### FastGPT vs RAGFlow

| 维度 | FastGPT | RAGFlow |
|---|---|---|
| **核心优势** | 易用性，低代码，快速上手 | 文档解析能力（尤其复杂 PDF） |
| **文档解析** | 基础解析 | 深度解析：表格、扫描件 OCR、双栏排版 |
| **适用文档** | 结构清晰的 Markdown/Word/TXT | 复杂 PDF、扫描件、合同、财报 |
| **RAG 策略** | 基础检索 + Prompt 模板 | 内置多种分块策略、RAPTOR 层级索引 |
| **部署复杂度** | Docker Compose 一套搞定 | 略复杂，需要 Elasticsearch 等组件 |
| **社区** | 国内活跃 | 国内快速增长 |
| **选型建议** | 文档格式规范、追求快速上线 | 大量复杂 PDF、需要深度文档理解 |

#### RAGFlow 的核心能力

```
RAGFlow 的独有优势：
├── DeepDoc：自研文档解析引擎
│   ├── 表格提取并保留结构
│   ├── 扫描版 PDF OCR（集成 PaddleOCR / Tesseract）
│   ├── 双栏排版正确识别
│   └── 图片中的文字提取
├── 多种分块策略
│   ├── 按标题层级（H1/H2/H3）
│   ├── 按表格边界
│   ├── 按语义边界
│   └── 知识图谱分块
├── RAPTOR：层级摘要索引
│   对文档建立多层抽象摘要，检索时逐层下钻
└── 引用溯源：每个回答都能回溯到源文档的精确位置
```

---

### 6.4.2 在 Linux 机器上部署 RAGFlow

#### 环境要求

```
CPU: 4 核+
内存: 16GB+
磁盘: 50GB+
Docker: 20.10+
Docker Compose: 2.0+

注：RAGFlow 本身不依赖 GPU，但对接的 LLM/Embedding 服务可能需要
```

#### 部署步骤

```bash
# 1. 克隆仓库
git clone https://github.com/infiniflow/ragflow.git
cd ragflow

# 2. 检查配置（一般用默认即可）
vim docker/.env
# 关键配置：
#   RAGFLOW_IMAGE=infiniflow/ragflow:<最新版本号>
#   MYSQL_PASSWORD=infini_rag_flow
#   ELASTIC_PASSWORD=infini_rag_flow   # 变量名是 ELASTIC_PASSWORD，不是 ES_PASSWORD
#   REDIS_PASSWORD=infini_rag_flow
#   （另可用 DOC_ENGINE=infinity 把 ES 换成自研的 Infinity 引擎）

# 3. 启动所有服务
docker-compose -f docker/docker-compose.yml up -d

# 检查服务状态
docker-compose -f docker/docker-compose.yml ps

# 正常情况下会启动：
#   ragflow-server    — 主服务（端口 80/443）
#   ragflow-mysql     — 业务数据库
#   elasticsearch     — 全文检索 + 向量存储
#   redis             — 缓存 + 消息队列
#   minio             — 文件存储
```

```bash
# 4. 访问 http://<服务器IP>
# 首次登录需注册管理员账号

# 5. 登录后进入「模型供应商」配置 LLM 和 Embedding 模型
```

#### 配置模型

```
RAGFlow 控制台 → 模型供应商：
  ├── OpenAI 兼容 API：
  │     API URL: http://vllm-server:8000/v1
  │     API Key: not-needed
  │
  ├── Embedding 模型：
  │     模型名: BAAI/bge-m3
  │     API URL: http://embedding-server:8001/v1
  │
  └── 或直接对接商用 API：
        OpenAI / Claude / 百度千帆 / 阿里百炼 / DeepSeek
```

---

### 6.4.3 快速体验 RAGFlow

#### Step 1：创建知识库

```
知识库 → 新建知识库
  名称：运维文档库
  语言：中文
  分块方法：General（通用，默认）；复杂文档按类型选专用模板
           （Paper / Book / Laws / Table / Q&A / Presentation 等）
  嵌入模型：BGE-M3
```

#### Step 2：上传文档

```
知识库 → 上传文件
  支持格式：PDF / Word / Excel / PPT / TXT / Markdown / HTML / 图片
  
  上传后 RAGFlow 会自动：
  1. 解析文档结构（DeepDoc 引擎）
  2. 识别并提取表格
  3. OCR 处理扫描件
  4. 智能分块
  5. 向量化存储
```

上传后可以在「分块预览」中看到每个 chunk 的内容和切分边界，能直观判断分块是否合理。

#### Step 3：创建聊天助手

```
聊天 → 新建聊天助手
  关联知识库：选择「运维文档库」
  Prompt 引擎：默认
  模型：选择已配置的 LLM
  
  高级设置：
  ├── 检索数量 Top-K：5
  ├── 相似度阈值：0.3
  ├── 融合关键词检索：开启（Hybrid Search）
  ├── 引用：开启
  └── 重排序：可选（需要 Rerank 模型）
```

#### Step 4：测试问答

在聊天界面输入问题，RAGFlow 的返回会包含：
- 生成的答案
- 引用的源文档（可点击跳转到原文精确位置）
- 每个引用片段的相关性分数

---

### 6.4.4 项目实战：合同/标书合规审查

RAGFlow 擅长处理复杂 PDF，很适合这个场景。

#### 场景描述

虚构练习场景：某公司每天收到几十份合同、标书、供应商资质文件，需要人工逐条核对合规条款。目标是用 RAGFlow 搭建一个**合同合规审查助手**，自动检查文档中的风险条款。

#### 实施步骤

**第 1 步：知识库准备**

```
上传两类文档：

A. 公司合规标准（作为 RAG 检索的「参考标准」）：
   - 合同审查 Checklist.pdf
   - 合规条款模板.docx
   - 常见风险条款库.xlsx

B. 待审查文档（作为用户问题附带的上下文）：
   - 每份需要审查的合同/标书
   - 通过 RAGFlow API 批量上传并解析
   （或利用 RAGFlow 的「临时文档」功能，不存入知识库，仅在当前对话中分析）
```

**第 2 步：Prompt 设计**

```markdown
你是一个专业的合同合规审查助手。请根据参考标准逐项审查待审合同。

## 审查标准
{{quote}}

## 审查要求
1. 逐条对照审查标准，标注「合规」「不合规」「不适用」
2. 对不合规项，标注风险等级（高/中/低）并说明原因
3. 给出修改建议
4. 如果合同中缺少审查标准要求的条款，单独列出「缺失项」

## 输出格式
| 审查项 | 判定 | 风险等级 | 原因 | 修改建议 |
|--------|------|----------|------|----------|
| 违约金条款 | 不合规 | 高 | 违约金超过合同总额 20% | 建议修改为不超过 10% |
| ... | | | | |

## 待审合同内容
{待审合同}
```

**第 3 步：批量审查流程**

```python
# batch_review.py — 用 RAGFlow API 批量审查合同
import requests
import glob

RAGFLOW_URL = "http://ragflow-server/api/v1"
API_KEY = "your-api-key"
KNOWLEDGE_BASE_ID = "kb-compliance-standards"

def review_contract(contract_path: str) -> dict:
    """审查单份合同"""

    # 1. 上传合同文件
    with open(contract_path, "rb") as f:
        resp = requests.post(
            f"{RAGFLOW_URL}/datasets/{KNOWLEDGE_BASE_ID}/documents",
            headers={"Authorization": f"Bearer {API_KEY}"},
            files={"file": f},
        )
    doc_id = resp.json()["data"]["id"]

    # 2. 创建对话（关联临时文档）
    resp = requests.post(
        f"{RAGFLOW_URL}/chats",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={
            "name": f"审查 {contract_path}",
            "dataset_ids": [KNOWLEDGE_BASE_ID],
        },
    )
    chat_id = resp.json()["data"]["id"]

    # 3. 发送审查请求
    #   （字段名以 RAGFlow 官方 HTTP API 文档为准，不同版本 completions
    #     的会话/文档关联字段有变动，跑之前先对一遍 API Reference）
    resp = requests.post(
        f"{RAGFLOW_URL}/chats/{chat_id}/completions",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={
            "question": """
请根据合规审查标准，对以下合同进行逐项审查。
输出格式：表格，含审查项、判定、风险等级、原因、修改建议。
            """,
            "stream": False,
        },
    )

    return resp.json()

# 批量审查
contracts = glob.glob("contracts/*.pdf")
for path in contracts:
    result = review_contract(path)
    print(f"=== {path} ===")
    print(result["data"]["answer"])
    print()
```

#### FastGPT vs RAGFlow 最终选型建议

```
文档类型是核心决定因素：

文档以 Markdown/纯文本为主，格式规范
  → FastGPT，部署简单，上手快

文档以复杂 PDF 为主（表格、扫描件、双栏）
  → RAGFlow，文档解析是核心痛点

需要工作流编排（审批、多步骤处理）
  → FastGPT，工作流引擎更成熟

需要层级索引、长文档深度理解
  → RAGFlow，RAPTOR 索引有优势

团队非技术背景，需要拖拽式操作
  → 两者都行，FastGPT 更简单一些

已有 Milvus 基础设施
  → FastGPT 原生支持 Milvus（RAGFlow 用 ES）
```

---

## 6.5 RAG 开发框架

除了低代码平台，如果你需要更灵活的控制（手写代码路径），以下是主流开发框架。

### LangChain

最主流的 LLM 应用开发框架，组件化拼装 RAG 链路。

```python
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain.chains import RetrievalQA
from langchain_openai import ChatOpenAI

loader = PyPDFLoader("ops_manual.pdf")
documents = loader.load()
text_splitter = RecursiveCharacterTextSplitter(chunk_size=512, chunk_overlap=50)
chunks = text_splitter.split_documents(documents)
embedding = HuggingFaceEmbeddings(model_name="BAAI/bge-m3")
vectorstore = Chroma.from_documents(chunks, embedding)

qa_chain = RetrievalQA.from_chain_type(
    llm=ChatOpenAI(base_url="http://localhost:8000/v1", model="qwen3-8b"),
    retriever=vectorstore.as_retriever(search_kwargs={"k": 5}),
    return_source_documents=True,
)
result = qa_chain.invoke({"query": "Redis 主从切换故障排查"})
print(result["result"])
```

**特点**：生态最全，第三方集成最多。缺点是版本迭代快，API 常有 breaking change。

### LlamaIndex

数据索引能力比 LangChain 更强，内置多种高级检索策略。

```python
from llama_index.core import VectorStoreIndex, SimpleDirectoryReader, Settings
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.llms.openai_like import OpenAILike

Settings.embed_model = HuggingFaceEmbedding(model_name="BAAI/bge-m3")
Settings.llm = OpenAILike(api_base="http://localhost:8000/v1", model="qwen3-8b")

documents = SimpleDirectoryReader("./ops_docs/").load_data()
index = VectorStoreIndex.from_documents(documents)
query_engine = index.as_query_engine(similarity_top_k=5)
response = query_engine.query("K8s Pod 一直 Pending 怎么排查？")
```

**特点**：内置树形/关键词/知识图谱索引，数据处理 pipeline 强大。

### Haystack

Pipeline 架构，组件职责单一，适合生产级系统。

```python
from haystack import Pipeline
from haystack.components.embedders import SentenceTransformersTextEmbedder
from haystack.components.retrievers.in_memory import InMemoryEmbeddingRetriever
from haystack.components.generators import OpenAIGenerator

rag_pipeline = Pipeline()
rag_pipeline.add_component("embedder", SentenceTransformersTextEmbedder(model="BAAI/bge-m3"))
rag_pipeline.add_component("retriever", InMemoryEmbeddingRetriever(document_store))
rag_pipeline.add_component("generator", OpenAIGenerator(model="qwen3-8b"))
```

**特点**：文档版本管理成熟，长期维护友好。

### DSPy

声明式编程——不写 prompt，让框架自动调优。

```python
import dspy

lm = dspy.LM("openai/qwen3-8b", api_base="http://localhost:8000/v1")
dspy.configure(lm=lm)

class RAG(dspy.Module):
    def __init__(self, k=5):
        super().__init__()
        self.retrieve = dspy.Retrieve(k=k)
        self.generate = dspy.ChainOfThought("context, question -> answer")

    def forward(self, question):
        context = self.retrieve(question)
        return self.generate(context=context, question=question)

rag = RAG()
optimizer = dspy.BootstrapFewShot(metric=dspy.evaluate.answer_exact_match)
optimized_rag = optimizer.compile(rag, trainset=training_data)
```

**特点**：少量标注数据即可持续优化，追求极致效果。

### 框架选型速查

| 框架 | 上手难度 | 灵活性 | 适合场景 |
|---|---|---|---|
| LangChain | ★★ | ★★★★ | 快速原型，生态最全 |
| LlamaIndex | ★★ | ★★★★ | 复杂数据结构，高级检索 |
| Haystack | ★★★ | ★★★ | 生产级，Pipeline 架构 |
| DSPy | ★★★★ | ★★★★★ | 效果极致，自动优化 |

---

## 6.6 LangGraph：有状态的 Agent 编排

### LangGraph 是什么

LangGraph 是 LangChain 团队推出的**有状态图编排框架**，解决链式调用的核心局限——链是线性的，真实 Agent 需要循环、分支、状态保持。

**运维类比**：LangChain 的 Chain = 写死的脚本，一步到底。LangGraph = 故障排查决策树 + 状态机，检查→判断→执行→再检查→再判断，每一步都能看到当前状态。

### 核心概念

```
三要素：
  State（状态）：一个字典，整个流程中传递和更新
  Nodes（节点）：处理状态的函数
  Edges（边）：节点间流转（普通边 = 固定，条件边 = 动态路由）
```

### 实战：自适应检索 Agent

一个会自我纠错的 RAG——答案不好就自动改写查询重新检索：

```python
from typing import TypedDict
from langgraph.graph import StateGraph, END

class RAGState(TypedDict):
    question: str
    retrieved_docs: list
    answer: str
    iteration: int
    max_iterations: int

def retrieve(state: RAGState):
    docs = vector_store.similarity_search(state["question"], k=5)
    return {"retrieved_docs": [d.page_content for d in docs], "iteration": state["iteration"] + 1}

def generate(state: RAGState):
    prompt = f"参考文档：{state['retrieved_docs']}\n问题：{state['question']}"
    return {"answer": llm.invoke(prompt)}

def should_retry(state: RAGState):
    uncertain_signals = ["无法确定", "没有相关信息", "我不清楚"]
    if any(s in state["answer"] for s in uncertain_signals):
        if state["iteration"] < state["max_iterations"]:
            return "rewrite"
    return "done"

def rewrite_query(state: RAGState):
    new_q = llm.invoke(f"请换一种方式重新表述：{state['question']}")
    return {"question": new_q}

# 构建图
graph = StateGraph(RAGState)
graph.add_node("retrieve", retrieve)
graph.add_node("generate", generate)
graph.add_node("rewrite", rewrite_query)

graph.set_entry_point("retrieve")
graph.add_edge("retrieve", "generate")
graph.add_conditional_edges("generate", should_retry, {"rewrite": "rewrite", "done": END})
graph.add_edge("rewrite", "retrieve")  # 改写后重新检索，形成循环

app = graph.compile()
result = app.invoke({"question": "Redis 切换失败怎么办", "iteration": 0, "max_iterations": 3})
```

执行流程：`retrieve → generate → 答案不好 → rewrite → retrieve → generate → 答案合格 → 结束`

### LangGraph 核心价值

| 能力 | 说明 | 适用 |
|---|---|---|
| **持久化状态** | 每步状态可保存/恢复 | 长时间 Agent 任务 |
| **循环和分支** | Agent 可自我纠错、重试 | Self-RAG、Corrective RAG |
| **人机协同** | `interrupt` 暂停等人类批准后继续 | 需审批的运维操作 |
| **子图嵌套** | 大图可调用小图 | 复杂工作流模块化 |
| **流式输出** | 每节点事件可流式推送 | 实时展示 Agent 进度 |

### 运维场景：智能故障处理

```
问题输入 → 检索知识库 → 生成处理方案
                ↓
        方案发给值班人员确认（人机协同中断）
                ↓
            确认后执行：
        日志查询 → 分析结果 → 发布公告 → 创建工单
                ↓
            执行后验证：
        检查服务 → 未恢复则重新诊断（循环）
```

| 方案 | 适合 |
|---|---|
| **LangGraph** | 循环、分支、状态保留、人机协同的 Agent |
| **LangChain Chain** | 简单线性 RAG |
| **Dify 工作流** | 低代码拖拽编排 |
| **FastGPT 工作流** | 同上 |

---

## 6.7 其他低代码平台

| 平台 | 核心特点 | 部署方式 | 适合 |
|---|---|---|---|
| **Dify** | 工作流引擎最强，Agent 强，国际化好 | Docker / 云 | 复杂工作流 |
| **MaxKB** | 国信证券开源，金融级安全，信创适配 | Docker | 金融/政务合规 |
| **AnythingLLM** | 极简，单机零配置 | Docker / 桌面 | 个人知识库 |
| **Coze/扣子** | 字节出品，免费额度大 | SaaS + 开源版 Coze Studio 可自部署（2025-07 开源） | 快速验证 |
| **百度千帆 AppBuilder** | 百度生态，内置 ERNIE | 仅 SaaS | 已用百度云 |

### Dify 快速上手

```bash
git clone https://github.com/langgenius/dify.git
cd dify/docker && cp .env.example .env
docker-compose up -d
# 访问 http://localhost
```

| 维度 | Dify | FastGPT |
|---|---|---|
| 工作流 | ★★★★★ | ★★★★ |
| Agent | ★★★★ | ★★★ |
| 国际化 | ★★★★★ | ★★★ |
| 国内社区 | ★★★★ | ★★★★★ |

---

## 6.8 文档解析工具

PDF 里的表格、扫描件、双栏排版是 RAG 效果的隐形天花板。

| 工具 | 核心能力 | 使用 | 适合 |
|---|---|---|---|
| **MinerU** | 上海 AI Lab，PDF 解析天花板 | Python/CLI | 复杂 PDF，中文最佳 |
| **Docling** | IBM，PDF→Markdown 质量高 | Python SDK | 企业级 |
| **Unstructured.io** | 最成熟，20+ 格式 | Python/API | 多格式支持 |
| **Marker** | 快速批量 PDF→Markdown | CLI | 大量 PDF 批量 |

### MinerU

```bash
# MinerU 2.x 起包名和命令都叫 mineru（老版包名 magic-pdf 已弃用，
# 也不存在 from magic_pdf import parse_pdf 这种一行式 API）
pip install "mineru[core]"
mineru -p ops_manual.pdf -o ./parsed/
# 输出 Markdown：表格以 Markdown table 保留，公式以 LaTeX 保留
```

### Docling

```python
from docling.document_converter import DocumentConverter
converter = DocumentConverter()
result = converter.convert("contract.pdf")
markdown = result.document.export_to_markdown()
```

### Unstructured.io

```python
from unstructured.partition.auto import partition
elements = partition(filename="ops_manual.pdf")
for el in elements:
    print(f"[{el.category}] {el.text[:100]}...")
```

---

## 6.9 Reranker（重排序）

Embedding 粗筛 Top-20 → Reranker 精排取 Top-3。经验量级（具体数字因数据集差异很大，以自己的评测集为准）：

```
纯向量检索：Top-5 召回率通常 80-90%
向量 + Reranker：Top-3 就能到 90-95%+
```

| 模型 | 特点 | 推荐度 |
|---|---|---|
| **BGE-Reranker-v2-m3** | 中文首选，配套 BGE-M3 | ★★★★★ |
| **Cohere Rerank v3** | 商业 API，效果顶级 | ★★★★★ |
| **Jina Reranker v2** | 多语言，8192 上下文 | ★★★★ |
| **Qwen3-Reranker** | 阿里最新 | ★★★★ |

```python
from FlagEmbedding import FlagReranker
reranker = FlagReranker("BAAI/bge-reranker-v2-m3")
scores = reranker.compute_score([[query, p] for p in passages])
scored = sorted(zip(passages, scores), key=lambda x: x[1], reverse=True)
```

---

## 6.10 GraphRAG（知识图谱 + RAG）

### 为什么需要

传统 RAG 做局部语义匹配，遇到「过去一年哪些故障和 Redis 有关？分别是什么原因？」这种全局汇总问题就抓瞎。GraphRAG 从文档抽取实体关系建图来解决。

### 三种方案

| 方案 | 思路 | 适用 |
|---|---|---|
| **Microsoft GraphRAG** | 自动抽取实体和关系建图，生成社区摘要 | 全局理解 |
| **LightRAG** | 轻量版，比微软版省 80% token | 资源有限 |
| **Neo4j + LLM** | 已有图谱，LLM 做 Text2Cypher 查图 | 已有成熟图谱 |

### LightRAG 实战

```python
# 骨架示意；llm_model_func 要传 async 补全函数，
# lightrag 没有 OpenAILike 这个类，OpenAI 兼容端点用 openai_complete_if_cache 包一层
from lightrag import LightRAG, QueryParam
from lightrag.llm.openai import openai_complete_if_cache, openai_embed

async def llm_func(prompt, **kwargs):
    return await openai_complete_if_cache(
        "qwen3-8b", prompt,
        base_url="http://localhost:8000/v1", api_key="not-needed", **kwargs)

rag = LightRAG(
    working_dir="./lightrag_ops",
    llm_model_func=llm_func,
    embedding_func=openai_embed,  # 或自定义 EmbeddingFunc 包 BGE-M3
)

with open("ops_docs/2024_faults.txt") as f:
    rag.insert(f.read())  # 自动抽取实体建图

# 三种模式
rag.query("Redis 故障处理", param=QueryParam(mode="local"))   # 局部检索
rag.query("故障按根因分类统计", param=QueryParam(mode="global"))  # 全局汇总
rag.query("Redis 和网络故障的关联", param=QueryParam(mode="hybrid"))  # 混合
```

---

## 6.11 RAG 评测框架

### RAGAS

```python
from ragas import evaluate
from ragas.metrics import (
    faithfulness, answer_relevancy, context_precision, context_recall,
)
from datasets import Dataset

results = evaluate(
    Dataset.from_dict({
        "question": ["Redis 主从切换失败怎么处理？"],
        "answer": [model_answer],
        "contexts": [[retrieved_chunks]],
        "ground_truth": ["首先检查 sentinel 日志..."],
    }),
    metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
)
```

### DeepEval

CI/CD 可集成的 RAG 评测：

```python
from deepeval import evaluate
from deepeval.metrics import HallucinationMetric, FaithfulnessMetric
evaluate([HallucinationMetric(threshold=0.5), FaithfulnessMetric(threshold=0.8)], test_cases=[tc])
```

---

## RAG 技术栈全景图

```
┌──────────────────────────────────────────────────────────┐
│  文档解析层                                                │
│  MinerU / Docling / Unstructured / Marker                │
├──────────────────────────────────────────────────────────┤
│  切块层                                                    │
│  固定大小 / 按标题层级 / 语义边界 / 父子切块                 │
├──────────────────────────────────────────────────────────┤
│  Embedding 层                                             │
│  BGE-M3 / GTE-Qwen2 / jina-v3 / text-embedding-3         │
├──────────────────────────────────────────────────────────┤
│  存储层（向量数据库）                                       │
│  Milvus / Qdrant / Chroma / ES / PGVector                │
├──────────────────────────────────────────────────────────┤
│  检索层                                                    │
│  Hybrid Search(向量+关键词) → Reranker 精排               │
├──────────────────────────────────────────────────────────┤
│  Agent编排层（高级RAG）                                     │
│  LangGraph / Dify工作流 → 自适应检索、自我纠错、人机协同     │
├──────────────────────────────────────────────────────────┤
│  生成层                                                    │
│  Prompt 拼接 + LLM 生成 + 输出过滤                         │
├──────────────────────────────────────────────────────────┤
│  应用层                                                    │
│  低代码：FastGPT / Dify / RAGFlow / MaxKB                │
│  框架：LangChain / LlamaIndex / Haystack / DSPy          │
│  Agent：LangGraph ← 状态/循环/人机协同                     │
├──────────────────────────────────────────────────────────┤
│  评测层                                                    │
│  RAGAS / DeepEval / LangSmith                             │
└──────────────────────────────────────────────────────────┘
```

### 最终选型速查

| 你的场景 | 推荐方案 |
|---|---|
| 快速验证想法 | FastGPT / Coze |
| 文档以 Markdown 为主、非技术团队 | FastGPT |
| 大量复杂 PDF（表格/扫描件） | RAGFlow + MinerU |
| 需要工作流编排 + Agent | Dify |
| 需要循环、自我纠错、人机协同 | **LangGraph** |
| 金融/政务信创 | MaxKB |
| 自有开发团队，深度定制 | LangChain/LlamaIndex + Qdrant/Milvus |
| 个人知识管理 | AnythingLLM |
| 全局理解、汇总统计 | LightRAG（GraphRAG） |
| 已有知识图谱 | Neo4j + LLM |
| 追求检索精度 | BGE-Reranker-v2-m3 |
| 追求极致效果 | DSPy 自动优化 |
| 效果评估 | RAGAS + DeepEval |
