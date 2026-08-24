# 推理系统深水区：从调度器到 PD 分离（SRE 视角）

> 创建时间: 2026-08-24
> 前置：[llm-hardware-guide.md](llm-hardware-guide.md)、[llm-ops-guide.md](llm-ops-guide.md)
> 定位：那两篇解决"跑起来、管起来"，这篇解决"为什么是这个数、瓶颈在哪、还能怎么压"。

---

## 0. 为什么 SRE 要懂推理引擎内部

传统服务的性能模型你闭着眼都能画：连接池、线程池、队列、下游 RT。LLM 推理服务的性能模型完全不同，不懂内部机制的后果很具体：

| 现象 | 不懂机制的反应 | 真实原因可能是 |
|---|---|---|
| P99 ITL 周期性毛刺 | 扩容 | 长 prompt 的 prefill 插进了 decode batch（没开/没调 chunked prefill） |
| 吞吐上不去但 GPU util 100% | 换更强的卡 | decode 本来就是带宽瓶颈，util 是假象，该加大 batch 或量化 |
| 偶发大量请求同时变慢 | 查网络 | KV cache 池满触发抢占，一批请求被重算 |
| TTFT 忽快忽慢差 10 倍 | 怀疑模型抽风 | 前缀缓存命中/未命中的差异 |
| 压测 QPS 翻倍延迟没变，再加一点就雪崩 | 神秘 | continuous batching 的容量悬崖：KV 预算耗尽 |

核心心法一句话：**prefill 是算力瓶颈（compute-bound），decode 是显存带宽瓶颈（memory-bound），KV cache 是并发瓶颈**。整篇笔记都是这句话的展开。

---

## 1. 一个请求的一生（以 vLLM V1 为例）

vLLM 自 0.8.x 起默认 V1 引擎：EngineCore 独立进程、调度器按 token 预算统一对待 prefill/decode、chunked prefill 和前缀缓存默认开启。

```
HTTP 请求 (OpenAI API)
    ↓
① Tokenize（CPU）
    ↓
② 进入 waiting 队列                       ← 指标：num_requests_waiting
    ↓
③ 调度器每步（step）做一次决策：
   在 max_num_batched_tokens 的 token 预算内，
   把 waiting/running 请求的 token 塞进本步 batch
   （一个长 prompt 会被切成多个 chunk 分步跑 = chunked prefill）
    ↓
④ 为新请求分配 KV block（PagedAttention 分页）
   先查前缀缓存：命中的 block 直接复用     ← 指标：prefix cache hit
    ↓
⑤ 执行一步前向（prefill chunk 和 decode token 混在同一个 batch）
    ↓
⑥ 每个 running 请求产出 1 个 token → 流式返回  ← 指标：ITL/TPOT
    ↓
⑦ KV 池不够时：抢占最低优先级请求，释放其 block，
   稍后重算（V1 默认重算路径）             ← 指标：num_preemptions_total
    ↓
⑧ 请求完成（EOS / max_tokens / stop）→ 释放 block
```

**几个关键推论**：

- **没有"每请求一个线程"这回事**。所有请求共享同一个大 batch，每步前向所有 running 请求各前进一个 token。所以一个行为异常的请求（超长输出）会持续占据 KV 预算，影响所有人——这就是为什么 `max_tokens` 必须有服务端上限。
- **TTFT = 排队时间 + prefill 时间**。排队看 `num_requests_waiting`，prefill 时间正比于 prompt 长度。TTFT 劣化先分清是哪一半。
- **ITL 毛刺的第一嫌疑人永远是"decode batch 里混进了大 prefill chunk"**，第二嫌疑人是抢占重算。

### 调度器的三个核心参数

| 参数 | 含义 | 调大 | 调小 |
|---|---|---|---|
| `--max-num-batched-tokens` | 每步 token 预算 | prefill 更快（TTFT ↓），但每步耗时更长（ITL ↑） | ITL 更稳，长 prompt 的 TTFT 变差 |
| `--max-num-seqs` | 同时 running 的请求上限 | 并发/吞吐 ↑，人均 ITL ↑，KV 压力 ↑ | 延迟稳，吞吐低 |
| `--gpu-memory-utilization` | vLLM 预占显存比例（权重+KV+激活） | KV 池更大，并发 ↑ | 留余量防碎片/峰值 OOM |

经验起手式：延迟敏感的对话服务 `max_num_batched_tokens` 取 2048-4096；离线批处理直接拉到 8192+ 换吞吐。**这是 LLM 服务最重要的一个 trade-off 旋钮，值得为它单独做一轮压测**（实验见 roadmap 第七阶段深水区）。

### SGLang 的差异点（一句话版）

- **RadixAttention**：前缀缓存用基数树组织，天然适合多轮对话/共享 system prompt 的树状复用，命中粒度比按 block 哈希更细。
- **overlap scheduling**：CPU 调度与 GPU 执行重叠，把调度开销藏进前向时间里。
- 运维视角两者高度同构：指标体系、容量模型、故障模式基本可以互相套用。

---

## 2. KV Cache：唯一值得背下来的资源模型

### 2.1 复习一个数

```
每 token KV = 2(K,V) × 层数 × KV头数 × head_dim × 每元素字节
Llama-3.3-70B / Qwen2.5-72B（GQA, 80层×8头×128）：
  FP16 ≈ 0.32 MB/token，FP8 KV ≈ 0.16 MB/token
```

### 2.2 并发容量公式（背这个）

```
KV 预算 = 显存 × gpu_memory_utilization − 权重 − 激活/CUDA graph 开销(约2-4GB)

最大并发 ≈ KV 预算 ÷ (平均序列长度 × 每token KV)
```

**实算**：单卡 H100 80G 跑 72B-AWQ（权重 ~40GB）：

```
KV 预算 ≈ 80×0.90 − 40 − 3 ≈ 29 GB
可容纳 token 数 ≈ 29GB ÷ 0.32MB ≈ 9 万 token
  → 平均 4K 上下文：~22 并发
  → 平均 16K 上下文：~5 并发
```

同一张卡，上下文从 4K 涨到 16K，并发掉 4 倍——**"支持长上下文"和"长上下文下还有并发"是两个预算问题**。这也是 KV FP8（`--kv-cache-dtype fp8`）的价值：并发直接翻倍，代价是轻微精度损失。

### 2.3 PagedAttention 到底解决了什么

朴素实现按 `max_seq_len` 为每个请求预留连续显存 → 内部碎片浪费 60-80%。PagedAttention 把 KV 切成固定 block（vLLM 默认 16 token/block），逻辑连续、物理离散，用 block table 映射——就是操作系统的分页内存。运维上的意义：

- 显存利用率从"看运气"变成"可计算"（上面的公式因此成立）；
- block 是共享单位 → 前缀缓存、beam search、并行采样都靠 block 引用计数实现；
- `gpu_cache_usage_perc` 这个指标度量的就是 block 池水位，**它到 90%+ 时抢占就在路上了**。

### 2.4 前缀缓存（prefix caching）

同一个 system prompt / 多轮对话的历史，在 block 粒度上哈希去重，命中的部分跳过 prefill。

```
典型收益：2K token 的 system prompt，命中后 TTFT 里的 prefill 部分归零。
生效前提（也是排障 checklist）：
  1. 请求真的有公共前缀（差一个字符都会从分歧点开始 miss）
  2. 负载均衡把同会话请求路由到同一副本（否则副本各存一份，命中率随副本数下降）
  3. KV 池没有压力驱逐（LRU；池水位长期 90%+ 时命中率会莫名下降）
```

第 2 条是多副本部署的核心矛盾，解法见第 7 节（KV 感知路由）。

### 2.5 抢占（preemption）

KV 池满时，调度器牺牲部分请求释放 block，被抢占请求回队列稍后**重算**（V1 默认路径）。

- 症状：`num_preemptions_total` 增长；被抢占请求的 ITL 出现秒级空洞；整体吞吐下降（重算是纯浪费）。
- 治理：抢占应当是"偶发的安全阀"而不是常态。**告警建议：抢占率 > 1% 请求数就该扩容或调低 `max_num_seqs`**。

### 2.6 KV 的分层与外移（进阶）

长对话/Agent 场景下 KV 变成"状态"，出现了把 KV 从单卡显存扩展出去的一层生态：

| 方案 | 思路 |
|---|---|
| CPU offload | 冷 KV 挪到内存，命中时搬回（PCIe 带宽是天花板） |
| LMCache | vLLM 生态的 KV 缓存层，支持跨实例共享/持久化 |
| Mooncake Store | KVCache 为中心的分布式池（Kimi 的生产架构，FAST'25 论文） |

判断是否需要：算一下你的会话回访间隔 × KV 大小 × 命中收益，多数内部工具场景单机前缀缓存就够了。

---

## 3. 性能的第一性原理：roofline

### 3.1 decode 是带宽瓶颈

每生成 1 个 token，理论上要把全部权重从 HBM 读一遍：

```
单请求 decode 上限 ≈ 显存带宽 ÷ 权重字节数

H100 (3.35 TB/s)：
  70B-FP16 (140GB) → ~24 t/s 理论上限
  70B-INT4 (40GB)  → ~84 t/s 理论上限
实际能拿到理论值的 40-70%（MBU，见下）
```

三个直接推论：

1. **量化在单流/低并发下就是加速器**：权重字节减半，速度接近翻倍——因为瓶颈是"读权重"。
2. **单请求跑不满带宽 ≠ GPU 没用满，而是 GPU 在等内存**。加 batch：一次读权重服务 N 个请求的 token，吞吐随 batch 近似线性涨，直到撞上算力或 KV 预算。这就是 continuous batching 吞吐提升 5-10x 的物理来源。
3. **换卡估算不用跑分**：H200 带宽是 H100 的 1.43 倍（4.8 vs 3.35 TB/s），decode 吞吐大致就是 1.4x。

### 3.2 prefill 是算力瓶颈

```
prefill FLOPs ≈ 2 × 参数量 × prompt token 数

72B 模型 prefill 2048 token ≈ 2×72e9×2048 ≈ 295 TFLOP
H100 BF16 dense 989 TFLOPS，按 50% MFU → 约 0.6 秒
```

所以长 prompt 的 TTFT 基本是线性可预测的；也所以 prefill 和 decode 抢的是**不同的资源**——这是第 8 节 PD 分离的全部理论基础。

### 3.3 两个"真实利用率"指标

- **MBU**（Memory Bandwidth Utilization）= 实测 decode 吞吐 × 权重字节 ÷ 峰值带宽。健康值 50-70%；明显偏低查 kernel/并行配置。
- **MFU**（Model FLOPs Utilization）= 实测 FLOPs ÷ 峰值 FLOPs。prefill/训练看这个，40-50% 算优秀。
- `nvidia-smi` 的 GPU util 在这两个面前毫无意义（详见 gpu-cluster-ops.md 第 6 节）。

---

## 4. 量化的 runtime 真相

[llm-ops-guide.md](llm-ops-guide.md) 讲了量化格式，这里讲**运行时行为**——为什么"量化更快"有前提：

| 方案 | 计算路径 | 快在哪 | 什么时候吃亏 |
|---|---|---|---|
| W4A16 (AWQ/GPTQ) | 权重 4bit 存储，GEMM 内融合反量化，用 FP16 算 | 权重读取量 ↓75% → **memory-bound（小 batch）大幅加速** | 大 batch 变 compute-bound 后，反量化开销反而拖慢 |
| W8A8-INT8 (SmoothQuant) | 权重+激活都 8bit，走 INT8 Tensor Core | 算力吞吐 ×2 → **大 batch 也快** | 激活量化对精度更敏感，要校准 |
| W8A8-FP8 (H100+) | 权重+激活 FP8，走 FP8 Tensor Core | 算力 ×2 且精度损失比 INT8 小，**Hopper 生产首选** | 需要 H100/H200/B 系硬件 |
| KV FP8 | 只压 KV cache | 并发×2，对速度影响小 | 极长上下文下精度衰减需评估 |

关键 kernel 名词（看 vLLM 日志/issue 会遇到）：**Marlin / Machete**（W4A16 高效 GEMM）、cutlass FP8。同一个 AWQ 模型，kernel 选择不同吞吐差 30%+，升级 vLLM 版本本身就是性能优化手段。

**选型口诀**：低并发在线服务 → W4A16；高并发/高吞吐 + Hopper → FP8 W8A8；并发被 KV 卡住 → 先上 KV FP8 再谈别的。

---

## 5. 投机解码（speculative decoding）

原理：便宜的"草稿"猜 k 个 token，大模型一次前向并行验证，接受的部分白赚。**它把"多次读权重"换成"一次读权重验证多个 token"——本质还是围着带宽瓶颈做文章**。

| 草稿来源 | 说明 | 场景 |
|---|---|---|
| n-gram | 从 prompt 里找重复模式，零成本 | RAG/改写类：输出大量复述输入 → 白捡 1.5-2x |
| 小 draft 模型 | 同族小模型（如 0.5B 给 72B 打草稿） | 通用，需额外显存和运维一个小模型 |
| EAGLE / EAGLE-3 | 训练一个轻量草稿头挂在主模型上 | 当前效果最好路线，vLLM/SGLang 均支持 |

**SRE 需要知道的三件事**：

1. 核心指标是**接受率**（acceptance rate）。接受率低（输出不可预测、代码混杂文本）时纯属浪费算力。
2. **只在低并发下有收益**。高并发时 GPU 本来就被 batch 填满了，草稿计算反而挤占资源——很多"上了投机解码反而变慢"的案例都是压测姿势和生产流量不匹配。
3. 上线前用真实流量回放测，别信通用 benchmark。

---

## 6. 长上下文与并行方式速查

| 并行 | 切什么 | 通信 | 适用 |
|---|---|---|---|
| TP（张量并行） | 每层权重切到多卡 | 每层 2 次 all-reduce，**卡间带宽敏感（NVLink 域内）** | 单模型放不下一张卡时的默认选择 |
| PP（流水并行） | 按层切段 | 段间点对点，量小 | 跨机放超大模型；有 bubble |
| EP（专家并行） | MoE 专家分布到多卡 | all-to-all，**对网络最凶** | DeepSeek 类 MoE 的标准玩法 |
| DP（数据并行） | 整模型复制 | 无（推理） | 加吞吐的最朴素方式 |

运维上最常犯的错：**跨 NUMA/跨机做 TP**。TP 的 all-reduce 在每一层发生，NVLink（900GB/s）和跨机网络（400Gb/s=50GB/s）差 18 倍，TP 尽量锁死在 NVLink 域内，跨机用 PP/EP/DP。

MoE 补充：DeepSeek-V3/R1 的生产部署是"大 EP"路线（prefill/decode 各自不同的 EP 规模 + DeepEP 通信库 + 专家负载均衡 EPLB），运维复杂度显著高于稠密模型——专家热点会造成卡间负载不均，需要监控 per-rank 的 token 分布。

---

## 7. 多副本的路由问题：负载均衡毁掉前缀缓存

轮询 LB 对 LLM 服务是**负优化**：

```
问题1：同一会话的第 N 轮被路由到没有该会话 KV 的副本 → 前缀缓存全 miss
问题2：请求代价方差巨大（50 token 问答 vs 30K token 文档分析），
       连接数/QPS 均衡 ≠ 负载均衡 → 有的副本排队有的空转
```

2025-2026 的标准答案是 **Gateway API Inference Extension**（K8s 社区项目，GKE Inference Gateway 是其产品化）：

```
InferencePool CRD（一组模型服务副本）
    +
Endpoint Picker（EPP，路由决策器）：
  实时拉取每个副本的 → 队列深度 / KV cache 水位 / 前缀缓存局部性 / LoRA adapter 加载情况
  按评分选副本，而不是轮询
```

自建简化版的两条底线：**会话亲和**（session → 固定副本，保命中率）+ **最少排队**（按 `num_requests_waiting` 选副本，别按连接数）。

---

## 8. PD 分离（prefill/decode disaggregation）

### 8.1 为什么

同一实例上混跑 prefill 和 decode 的根本矛盾：

```
prefill：算力密集、一次性、延迟目标是 TTFT
decode： 带宽密集、持续性、延迟目标是 ITL
混跑 → 大 prefill 插入 decode batch → 别人的 ITL 毛刺（chunked prefill 只能缓解不能消除）
     → 两种资源画像迫使你按"最差情况"配置，goodput 上不去
```

分开跑：prefill 池和 decode 池各自选并行方式、各自扩缩容，中间传 KV：

```
请求 → [路由/编排层] → Prefill 实例（算完产出 KV）
                            │  KV 经 NVLink/RDMA 传输（NIXL / LMCache / Mooncake TransferEngine）
                            ↓
                        Decode 实例（接着生成）→ 流式返回
```

### 8.2 生态版图（2026-08 现状）

| 系统 | 出身 | 一句话 |
|---|---|---|
| **vLLM KVConnector** | vLLM 内置 | 框架层的 PD 原语，1P1D 起步，Meta/LinkedIn 等已生产 |
| **NVIDIA Dynamo** | GTC 2025 发布，**1.0 于 2026-03 GA** | 推理编排层（PD 路由、KV 管理、NIXL 传输、Planner 扩缩容），配套 Grove 做多节点 Pod 编排 |
| **llm-d** | Red Hat/Google/NVIDIA，**2026-03 捐入 CNCF** | K8s 原生路线：vLLM + Inference Gateway + PD，用 LeaderWorkerSet 编排 |
| **Mooncake** | 月之暗面（Kimi）生产系统，FAST'25 最佳论文 | KVCache 为中心的分离架构鼻祖之一 |
| **SGLang PD** | SGLang 内置 | DeepSeek 官方部署参考用的这条线 |

学术源头：DistServe（OSDI'24，goodput 提升最高 4.48x 的原始论证）、Splitwise。

### 8.3 什么时候（不）值得

```
值得：
  - 集群规模 ≥ 十几张卡、流量大且 SLO 严（TTFT 和 ITL 都有 P99 承诺）
  - 长 prompt 流量占比高（RAG/文档/Agent），prefill 干扰肉眼可见
  - MoE 大模型（prefill/decode 的最优 EP 配置本来就不同）

不值得：
  - 单机 8 卡以内：chunked prefill + 好好调参的收益/复杂度比高得多
  - KV 传输链路（RDMA 网络）不达标：传 KV 的时间吃掉全部收益
  - 团队还没有把"单体 vLLM 调到位"的能力——PD 是乘法器不是救命稻草
```

**SRE 检查清单**（上了 PD 之后新增的失败面）：KV 传输带宽/延迟监控、P:D 配比弹性（流量的 prompt/output 比例漂移会让固定配比失衡，Dynamo Planner 解决的就是这个）、prefill 池故障的降级路径（回退混跑）。

---

## 9. 故障模式手册（推理服务特有）

| 症状 | 机制层原因 | 定位 | 处置 |
|---|---|---|---|
| 启动即 OOM | 权重+KV 预分配超显存 | 启动日志的显存分解 | 调低 `gpu-memory-utilization` / `max-model-len`，或量化 |
| 运行数小时后 OOM | 碎片/激活峰值/长上下文叠加 | 显存水位趋势 | 留余量（0.85-0.9），限 `max-model-len` |
| ITL 周期性毛刺 | prefill 插队 | 毛刺与长 prompt 请求相关性 | 调小 `max-num-batched-tokens`；根治靠 PD |
| 集体变慢 + 恢复 | KV 满 → 批量抢占重算 | `num_preemptions_total` 阶跃 | 扩容/降 `max-num-seqs`/KV FP8 |
| TTFT 双峰分布 | 前缀缓存命中 vs miss | 命中率指标 + 按会话聚合 | 修路由亲和 |
| 多卡 TP 实例整体卡死 | NCCL 集合通信 hang（一卡异常全员等待） | NCCL watchdog 日志、Xid | 重启实例；根因查 GPU/链路（见 gpu-cluster-ops.md） |
| 尾部 1% 请求极慢 | 超长输出霸占 KV | 按 output_len 分桶看延迟 | 服务端强制 `max_tokens` 上限 |
| 升级后吞吐暴跌 | kernel 回退（量化模型尤其） | 版本对比压测 | 固定版本灰度，压测过再全量 |

指标名注意：vLLM V0/V1 的部分指标名有差异（如前缀缓存命中在 V1 是 queries/hits 两个 counter），以 `/metrics` 实际输出为准建盘，别照抄旧文章。

---

## 10. 把这篇变成能力的三个练习

1. **不查资料**，给"单卡 H100 跑 Qwen2.5-72B-AWQ、平均 6K 上下文、P95 ITL < 60ms"做容量估算（并发数、预期单流 t/s、何时该上第二张卡），然后压测验证误差。
2. 对你手上任一 vLLM 服务，画出它的 **TTFT 分解图**（排队 vs prefill）和 **ITL 毛刺归因**（prefill 干扰 vs 抢占），只用现有 metrics。
3. 写一页纸的《本团队何时引入 PD 分离》决策备忘录，含触发条件、候选方案（llm-d vs Dynamo vs 手搓 KVConnector）、新增故障面清单。

对应动手实验见 roadmap 仓库《第七阶段学习资料-AIInfra深水区》。
