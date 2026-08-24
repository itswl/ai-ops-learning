# GPU 集群运维深水区：故障、网络、调度与利用率

> 创建时间: 2026-08-24
> 前置：[llm-hardware-guide.md](llm-hardware-guide.md)、[llm-ops-guide.md](llm-ops-guide.md)
> 定位：单卡/单机之后的世界——GPU 当成"会坏的生产资料"来管理：故障处置、集群网络、K8s 调度、真实利用率。

先立一个规模感：Llama 3 论文披露，405B 模型在 16,384 张 H100 上预训练的 54 天快照期里发生 466 次任务中断，其中 419 次是意外中断，约 78% 归因硬件，GPU 相关（含 HBM）占 58.7%——**平均每 3 小时坏一次**。GPU 集群运维的核心命题不是"让它不坏"，而是"坏了不停、停了快回"。

---

## 1. GPU 节点的软件栈分层

排障先分层，每层有自己的死法：

```
┌─────────────────────────────────────────────┐
│ workload: vLLM / PyTorch / Triton           │ ← CUDA OOM、NCCL timeout
├─────────────────────────────────────────────┤
│ K8s 接入层: device plugin 或 DRA driver      │ ← 资源上报缺失、分配失败
│ （由 gpu-operator 统一管理）                  │
├─────────────────────────────────────────────┤
│ 容器层: NVIDIA Container Toolkit             │ ← 容器内看不到卡、库版本错配
├─────────────────────────────────────────────┤
│ 用户态驱动: libcuda / NVML                    │ ← 版本兼容矩阵
│ 守护进程: nvidia-persistenced /              │ ← HGX 机型没起 fabricmanager
│           nvidia-fabricmanager (NVSwitch 机) │    → CUDA error 802
├─────────────────────────────────────────────┤
│ 内核驱动: nvidia.ko / nvidia-peermem(GDR)    │ ← Xid、掉卡、GSP 问题
├─────────────────────────────────────────────┤
│ 硬件: GPU / NVLink / NVSwitch / PCIe / HCA   │ ← ECC、链路降速、供电散热
└─────────────────────────────────────────────┘
```

高频分层错配三例：

- **HGX/DGX（带 NVSwitch）机器上 CUDA 初始化报 `error 802 (system not yet initialized)`** → `nvidia-fabricmanager` 没起或版本与驱动不匹配，和应用无关。
- **容器里 `nvidia-smi` 有卡、PyTorch 说没有** → 容器 toolkit 注入的 libcuda 与宿主驱动版本错配，或 `NVIDIA_VISIBLE_DEVICES` 被覆盖。
- **升级驱动后一切正常、重启后集体趴窝** → 驱动是 DKMS 编译失败但旧模块还在内存里，重启才暴露。升级必须带"重启验证"步骤。

版本兼容口诀：**驱动版本决定 CUDA 上限，容器内 CUDA runtime 可以比宿主 toolkit 新（forward compat），但绝不能超过驱动支持上限**。集群统一驱动版本，用 gpu-operator 的 driver 容器化管理，不要让节点各自 apt upgrade。

---

## 2. 先看懂拓扑再谈性能

```bash
nvidia-smi topo -m        # 卡间连接矩阵：NV# = NVLink，PIX/PXB/PHB/SYS = PCIe 距离由近到远
nvidia-smi nvlink -s      # 每条 NVLink 链路状态与速率
nvidia-smi -q -d ECC,ROW_REMAPPER   # ECC 计数与坏行重映射状态
```

带宽数量级要刻进肌肉记忆（决定并行方式怎么选）：

| 链路 | 量级（单向） |
|---|---|
| HBM3（H100 卡内） | 3.35 TB/s |
| NVLink4 域内卡间（H100） | 450 GB/s（双向 900） |
| PCIe Gen5 x16 | 64 GB/s |
| 跨机 400Gb IB/RoCE（单口） | 50 GB/s |

**结论复述**（和推理笔记呼应）：TP 锁死在 NVLink 域内；跨 PCIe 的"假 8 卡机"（无 NVLink 的推理机型）不适合大 TP；跨机通信规划先数网卡（H100 训练机型标配 8×400G，一卡一网口的 rail 设计）。

---

## 3. GPU 故障处置手册

### 3.1 Xid 速查表（按处置动作分级）

Xid 是驱动上报的 GPU 错误码，出现在 `dmesg`（`NVRM: Xid (PCI:xxxx): NN, ...`）和 DCGM 里。**别背全表，背分级**：

| 级别 | 典型 Xid | 含义 | 处置 |
|---|---|---|---|
| **应用级**（卡没坏） | 13（引擎异常）、31（非法显存访问）、43（应用错误停止）、45（任务被杀清理） | 多为应用 bug / 被 OOM kill | 查应用；同一应用多卡复现≈代码问题，单卡反复出≈怀疑硬件 |
| **可观察**（记录趋势） | 92（单比特 ECC 率偏高）、63（记录到坏行待重映射） | 可纠正错误，尚不影响正确性 | 63：安排维护窗口 reset 使重映射生效；92：加监控看斜率 |
| **需要 reset** | 48（双比特 ECC）、94（可抑制的 ECC 错误）、95（不可抑制）、119/120（GSP 超时/错误） | 计算正确性已受威胁 / GSP 固件卡死 | 排水 → `nvidia-smi -r`（或重启节点）→ `dcgmi diag -r 3` 通过再回池 |
| **疑似换卡** | 79（GPU fallen off the bus）、64（坏行重映射失败）、74（NVLink 错误频发） | 供电/PCIe/链路/显存硬损 | 79 先断电冷启排除接触问题；复发即报修 RMA；74 先查链路两端和线缆 |

两条铁律：

1. **双比特 ECC（48/94/95）之后的计算结果不可信**——训练任务必须回滚到上一个 checkpoint，而不是"接着跑"。
2. **同一张卡 30 天内第二次进入"需要 reset"级别 → 直接送修**，不要跟硬件斗智斗勇。

### 3.2 ECC 与坏行重映射（A100+）

```bash
nvidia-smi -q -d ROW_REMAPPER
#   Remapped Rows: Correctable/Uncorrectable 计数
#   Pending: Yes → 有坏行等待 reset 后重映射（尽快安排维护窗口）
#   Remapping Failure: Yes → 备用行耗尽，RMA
```

心智模型：显存自带备用行，坏行重映射是常规损耗（类比 SSD 坏块），**Pending 状态才需要行动，Failure 才是坏卡**。

### 3.3 dcgmi diag：分级体检

```bash
dcgmi diag -r 1    # 秒级：软件栈/基本健康，回池前的最低门槛
dcgmi diag -r 2    # ~2 分钟：加 PCIe/显存带宽小压测
dcgmi diag -r 3    # ~30 分钟：完整压测（显存、SM、拉满功耗），维修后验收用
dcgmi diag -r 4    # 更长：深度筛查（EUD），新卡到货 burn-in 用
```

### 3.4 自愈流水线（值得做成系统的部分）

```
dcgm-exporter 指标 / dmesg Xid
    ↓ 告警规则（按 3.1 分级）
应用级 → 只通知任务 owner
需 reset 级 →
    ① kubectl cordon + 排空该卡上的 Pod（训练任务先触发 checkpoint）
    ② nvidia-smi -r（不行则重启节点）
    ③ dcgmi diag -r 3 通过 → uncordon 回池
    ④ 未通过 / 30 天二进宫 → 打 RMA 标签，进备件流程
全程写事件审计：卡序列号、Xid 历史、处置动作、耗时
```

这套流水线正是 AIOps 落地的高价值场景：判定逻辑清晰、动作可回滚、频次高到人肉处理很痛苦（回顾开头：16K 卡的集群每 3 小时来一次）。

---

## 4. 集群网络与 NCCL

### 4.1 你需要的最小知识图

```
物理层：IB（原生无损）  vs  RoCEv2（以太网上模拟无损：PFC 防丢包 + ECN/DCQCN 控拥塞）
拓扑：rail-optimized —— 每台机器的第 i 号网卡都接同一组 leaf（同轨），
      GPU_i 的流量走自己同轨的 NIC_i，跨轨流量用 NVLink 先绕到对应 GPU（NCCL 的 PXN）
加速件：GPUDirect RDMA（网卡直读显存，需 nvidia-peermem 或 dmabuf）
        NVLS / SHARP（交换机内做规约，allreduce 卸载到网络）
```

### 4.2 NCCL 排障与调优的最小集合

```bash
# 看清 NCCL 实际选了什么路径（上线前跑一次留档）
NCCL_DEBUG=INFO NCCL_DEBUG_SUBSYS=INIT,GRAPH,ENV torchrun ...
# 日志里确认：用的是 IB 还是 Socket？GDR 有没有生效？拓扑识别对不对？

# 常用环境变量（默认值大多已经很好，改之前先能解释为什么）
NCCL_IB_HCA=mlx5_0,mlx5_1      # 指定 HCA，防止选错管理口
NCCL_IB_GID_INDEX=3            # RoCEv2 常见取值
NCCL_SOCKET_IFNAME=bond0       # bootstrap 网卡
NCCL_NET_GDR_LEVEL=PHB         # GPUDirect RDMA 生效的拓扑距离阈值
NCCL_ALGO / NCCL_PROTO         # Ring/Tree、LL/LL128/Simple，调优末段再碰
```

### 4.3 nccl-tests：集群交付的验收语言

```bash
# 单机 8 卡
mpirun -np 8 ./all_reduce_perf -b 8 -e 8G -f 2 -g 1
# 多机：-np 16/32...，看大消息段的 busbw

# 读数：
# algbw = 数据量/时间；busbw = algbw × 2(n-1)/n（allreduce 换算出的硬件带宽压力）
# 结论只看 busbw 与"同机型基线"的差距：
#   单机 NVLink 域（8×H100，NVLS 开启）应到数百 GB/s 量级
#   跨机段应逼近网卡聚合带宽的高比例；掉到基线 70% 以下 = 有病灶（降速链路/走错网卡/GDR 未生效）
```

验收基线用"同机型自己的历史最好值"，别用论文数字——固件、线缆、交换机配置都会造成机型间差异。

### 4.4 NCCL hang：分布式最恶心的故障

集合通信是同步语义：**一个 rank 出事，所有 rank 一起卡住**，表象是"全员静止、GPU util 100%（空转等待）"。

```
定位三板斧：
1. 每个节点扫 dmesg 找 Xid —— 多数 hang 的根因是某张卡先出了硬件问题
2. PyTorch flight recorder：
   TORCH_NCCL_TRACE_BUFFER_SIZE=1048576（常开）
   TORCH_NCCL_DUMP_ON_TIMEOUT=1 → 超时自动 dump 各 rank 卡在哪个 collective
   对比各 rank 的最后一个完成的 op，先掉队的那个 rank 就是嫌疑人
3. 网络面：ibstat 看链路状态，对嫌疑节点跑 ib_write_bw 点对点
处置：watchdog 超时杀任务 → 从 checkpoint 重启 → 嫌疑节点隔离进诊断流水线
```

---

## 5. K8s GPU 调度（2026 版图）

### 5.1 资源接入：device plugin → DRA

| | device plugin（存量主流） | DRA（K8s 1.34 GA，1.35 起锁定启用） |
|---|---|---|
| 资源模型 | `nvidia.com/gpu: 1` 整数计数 | ResourceClaim/DeviceClass/ResourceSlice，结构化描述设备属性 |
| 按属性选卡 | 不行（靠 node label 曲线救国） | 原生：按显存大小、型号、MIG profile 等 CEL 表达式筛选 |
| 动态 MIG | 静态预切，改 profile 要动节点配置 | 按 claim 动态创建 MIG 实例 |
| 多卡编排 | 无拓扑语义 | 支持设备间关系（如 GB200 NVL72 的 IMEX/ComputeDomain） |
| 现状 | gpu-operator 默认路径 | NVIDIA DRA 驱动已捐 CNCF（KubeCon NA 2025），新集群建议开始双轨验证 |

务实建议：**存量推理集群不必急着迁 DRA；新建训练集群、有 MIG 动态切分或 NVL72 机型的，直接按 DRA 设计**。

### 5.2 共享方案对比（这张表答对就能过面试）

| 方案 | 隔离性 | 原理 | 适用 | 坑 |
|---|---|---|---|---|
| time-slicing | 无 | 时间片轮转 | 开发/测试环境凑数 | 无显存隔离，互相 OOM；延迟不可预测 |
| MPS | 弱（算力可分，故障不隔离） | 多进程共享 CUDA context | 同租户的小推理任务合并 | 一个 client 崩可能拖垮全体 |
| MIG | **硬件级**（SM/显存/L2 物理切分） | H100 最多切 7 个实例（1g.10gb×7 / 2g.20gb×3 / 3g.40gb×2…） | 多租户、小模型推理池 | 实例间无 NVLink；切分粒度固定；改 profile 需清空该卡 |
| HAMi / vGPU 类 | 软件层 | CUDA 劫持限显存/算力 | 国内私有云常见的碎卡复用 | 精度依赖实现，隔离承诺要实测 |

决策树：多租户生产 → MIG；同租户凑合用 → MPS；仅开发环境 → time-slicing。

### 5.3 队列与配额：训练集群的秩序

裸 K8s 调度器对批量训练缺三样东西：**gang（要么全起要么不起）、配额与借用、优先级抢占**。

| 组件 | 定位 | 一句话 |
|---|---|---|
| **Kueue** | K8s 官方系的作业排队 | ClusterQueue 配额 + 借还额度 + all-or-nothing 准入；配 JobSet/Kubeflow 生态最顺；TAS 提供拓扑感知 |
| **Volcano** | 老牌批调度器 | gang/fairshare/binpack 全家桶，国内采用最广 |
| **KAI Scheduler** | NVIDIA 开源（Run:ai 血统，Apache 2.0） | 层级队列 + 碎卡（fractional GPU）+ 训练推理混布，2026 年事实上的 AI 调度参考实现 |
| **LeaderWorkerSet (LWS)** | 多节点推理的 workload 原语 | 跨机 TP/PP/PD 分离部署的标准载体（llm-d 底层用它） |

没有 gang 调度的训练集群会周期性上演死锁：两个 64 卡任务各拿到 32 张卡互相等——**这是上任何多租户训练平台前必须堵死的第一个坑**。

### 5.4 拓扑感知

大规模训练对"卡在哪"极其敏感：同任务的 Pod 应集中在同一 spine 块内（跨块通信带宽收敛比常常 1:2 甚至更低）。手段：节点打上 block/rack 标签 + Kueue TAS / Volcano 网络拓扑感知策略。验收方法简单粗暴：**同一个 nccl-tests 任务，让调度器摆一次、手工集中摆一次，busbw 差值就是你的调度器欠的债**。

---

## 6. 利用率的真相与成本治理

### 6.1 GPU util 是个骗子

`nvidia-smi` 的 utilization.gpu 语义是"采样窗口内**有任意 kernel 在跑**的时间占比"——**一个 SM 干活也算 100%**。空转等 NCCL 的训练任务、带宽饥饿的推理服务，util 都显示 100%。

看真实利用率用 DCGM 的 profiling 指标：

| 指标 | 含义 | 用法 |
|---|---|---|
| `DCGM_FI_PROF_SM_ACTIVE` | SM 上有 warp 在跑的比例 | <0.5 说明 GPU 大量时间在等 |
| `DCGM_FI_PROF_SM_OCCUPANCY` | SM 内 warp 占用率 | 配合上面看 kernel 质量 |
| `DCGM_FI_PROF_PIPE_TENSOR_ACTIVE` | Tensor Core 活跃比例 | **训练 MFU 的代理指标**，矩阵乘密集型应 >0.3-0.5 |
| `DCGM_FI_PROF_DRAM_ACTIVE` | 显存带宽活跃比例 | 推理 decode 应该高（memory-bound 的自证） |
| `DCGM_FI_PROF_NVLINK_TX/RX_BYTES` | NVLink 流量 | TP/EP 通信是否符合预期 |

顶层北极星各选一个：训练看 **MFU**（Llama-3 量级参考：BF16 大规模训练 38-43%），推理看 **MBU** 或 goodput（满足 SLO 的 token 产出）。

### 6.2 成本换算（拿去怼预算表）

```
GPU 时成本 = (采购价/3年折旧 + 电费 + 机房) / 8760h        # 自建
每百万 token 成本 = GPU时成本 ÷ (实测吞吐 token/s × 3600) × 1e6

例：H100 按 ¥18/时全成本、72B-AWQ 实测聚合吞吐 1500 t/s（高并发 batch）
→ ¥18 / (1500×3600) × 1e6 ≈ ¥3.3 / 1M token
和 API 价对照，就知道自建的盈亏平衡利用率在哪。
```

碎片治理三件套：MIG 把小任务收编、Kueue 配额防"占着不用"、按 `PIPE_TENSOR_ACTIVE` 做低利用率任务的自动通报（点名比说教有效）。

---

## 7. 训练任务的容错工程

### 7.1 checkpoint 是唯一的救命绳

```
最优间隔（Young/Daly 公式）：τ ≈ sqrt(2 × 单次ckpt耗时 × 集群MTBF)

例：ckpt 写 2 分钟、千卡集群 MTBF 12h → τ ≈ sqrt(2×2×720) ≈ 54 分钟
集群越大 MTBF 越短 → 间隔必须越密 → ckpt 开销占比越高 → 于是需要：
  异步 checkpoint（torch.distributed.checkpoint 异步保存，训练不停）
  分层存储（先落本机内存/NVMe，后台再传对象存储）
```

### 7.2 弹性与快速恢复

```
torchrun --nnodes=<MIN>:<MAX> --max-restarts=N ...   # 弹性 rendezvous
配套要素：
  备件池：热备节点占集群 2-5%，坏节点换入而不是等修
  恢复目标：从"节点故障"到"训练继续"的 MTTR 应压到 <15 分钟
             （检测 1-3min → 换节点 2-5min → 拉起+载 ckpt 5-10min）
  straggler：per-rank step time 分布监控，最慢 rank 持续离群 → 主动踢掉
             （一台降频的机器拖慢整个同步训练，比宕机更隐蔽也更贵）
```

### 7.3 goodput 核算

```
goodput = 有效训练时间占比
        = 1 − (故障停机 + 回滚重算 + ckpt 开销 + 排队等资源) / 总时间
Llama-3 披露值 >90%，这是有 466 次中断前提下做到的——
差距全在自动化：自动检测、自动换节点、自动从 ckpt 恢复，每一步的人肉都在烧钱。
```

---

## 8. 节点生命周期 runbook

```
【上架 burn-in】（新卡故障率曲线是浴盆型，头几天最危险）
□ 驱动/固件版本对齐集群基线；HGX 机确认 fabricmanager 运行
□ dcgmi diag -r 4 通过
□ gpu-burn 60 分钟，无 Xid、无降频（nvidia-smi dmon 盯 temp/pwr/clock）
□ 单机 nccl-tests 达机型基线；跨机 ib_write_bw 达线速
□ 跑一个标准训练/推理冒烟任务，吞吐达基线 ±5%

【日常巡检】（自动化，异常才通知）
□ Xid 新增 = 0；ECC Pending = 0；NVLink 无 inactive 链路
□ dcgm PROF 指标采集正常（缺数据往往 = exporter 或 profiling 权限挂了）
□ 温度/功耗无离群（同机型横向对比找"发烧卡"）

【故障处置】→ 见 3.4 自愈流水线
【退役】：ECC Failure / RMA 二进宫 / 过保评估 → 数据擦除 → 下架
```

---

## 9. 自测题（不查资料）

1. Xid 79 和 Xid 48 的处置有什么本质区别？为什么 48 之后必须回滚 checkpoint？
2. `nvidia-smi` util 100% 但 `PIPE_TENSOR_ACTIVE` 只有 0.15，训练任务大概率在等什么？给出三个排查方向。
3. 为什么 TP 不建议跨机？用带宽数字说明。
4. rail-optimized 拓扑里，GPU3 的流量想走 NIC7 会发生什么？PXN 解决的是什么问题？
5. 两个 64 卡任务在无 gang 调度的集群里各持有 32 卡，描述死锁形成过程和三种解法。
6. 千卡集群、单次 ckpt 3 分钟、MTBF 8 小时，算最优 ckpt 间隔；如果换成异步 ckpt，这个公式里哪个变量变了？
7. MIG 和 MPS 各自的隔离边界在哪？多租户对外售卖场景为什么只能选 MIG？
8. DRA 相比 device plugin，最能打动"要动态切 MIG 的平台团队"的是哪一点？

对应动手实验见 roadmap 仓库《第七阶段学习资料-AIInfra深水区》。
