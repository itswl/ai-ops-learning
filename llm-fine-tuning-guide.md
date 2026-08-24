# 大模型微调实战指南

> 创建时间: 2026-05-06

---

## 4.1 搞懂大模型微调

### 为什么需要微调

基础大模型虽然知识面广，但有三个致命短板：

| 短板 | 表现 | 举个例子 |
|---|---|---|
| **知识滞后** | 训练数据有截止日期 | 公司昨天的故障复盘，模型不可能知道 |
| **领域盲区** | 通用语料中缺乏专业知识 | 你司内部的工单格式、告警命名规范 |
| **风格不对** | 输出格式/语气不受控 | 需要固定 JSON 输出给自动化流程，不想看小作文 |

**微调就是解决这三个问题的**——让模型学会你的数据、你的格式、你的风格。

### 微调 ≠ 训练新模型

很多人一听「微调」就觉得要搞大规模训练。实际上微调更像是**给一个已经大学毕业的人做岗前培训**——基础能力已经有了，只是教会他你们公司的业务。

### 微调技术分类

```
微调技术
├── 全量微调 (Full Fine-Tuning)
│     更新所有参数，效果最好，成本最高
│     7B 模型需要 ~56GB 显存（不含优化器状态）
│
├── 参数高效微调 (PEFT)
│   ├── LoRA / QLoRA ★ 当前主流
│   │     只训练一小部分额外参数（适配器），冻结原模型
│   │     显存需求降低 70-80%，效果接近全量微调
│   │
│   ├── Adapter
│   │     在每层插入小网络，只训这些小网络
│   │
│   └── Prefix Tuning / P-Tuning
│         只训练可学习的「前缀向量」
│
└── 指令微调 (Instruction Tuning)
      用「问题-答案」格式数据训练，让模型学会遵循指令
      本质上是一种数据组织方式，不是独立技术
```

### 技术选项指南

| 技术 | 显存（7B） | 训练速度 | 效果 | 什么时候用 |
|---|---|---|---|---|
| **全量微调** | ~56GB+ | 慢 | 最好 | 有充足资源，需要效果极致 |
| **LoRA** | ~18-24GB | 快 | 接近全量 | **推荐首选**，性价比最高 |
| **QLoRA** | ~10-14GB | 中等 | 略低于 LoRA | 单张消费级显卡（如 RTX 4090）|
| **P-Tuning v2** | ~20GB | 快 | 中等 | 简单的指令跟随任务 |

### 微调策略

#### 什么时候应该微调（而非 RAG 或提示词工程）

```
决策树：
  需要引入新知识吗？
    ├── 需要大量外部知识 → RAG 更合适（比微调简单得多）
    └── 不需要
        └── 需要改变输出风格/格式/行为模式吗？
              ├── 是 → 微调
              └── 否 → 写好提示词就够了
```

**触发微调的信号**：
- 同一个 prompt 模板反复用，每次都要加一堆规范说明 → 微调省 token
- 模型在特定领域术语上反复出错 → 微调纠正
- 需要固定输出格式（JSON/YAML/自定义 DSL）→ 微调固化格式
- 需要使用小模型替代大模型（用小模型 + 领域微调达到大模型 80% 效果）

#### 是否要微调的对照表

| 问题 | RAG | 提示词工程 | 微调 |
|---|---|---|---|
| 注入新知识 | ★ 最佳 | 差（超出长度就失效） | 中（知识易过时） |
| 固定输出格式 | 中 | 中 | ★ 最佳 |
| 学习领域术语 | 中（检索到就能解释） | 中 | ★ 最佳 |
| 改变回答风格 | 不适用 | 中（长 prompt 费 token） | ★ 最佳 |
| 快速实施 | ★ 分钟级 | ★ 分钟级 | 差（小时到天级） |
| 维护成本 | 低（更新知识库） | 低（改 prompt） | 高（需重新微调） |

---

## 4.2 大模型微调工具

### 开源微调工具

| 工具 | 显存需求 | 上手难度 | 核心优势 | 适用场景 |
|---|---|---|---|---|
| **LLaMA-Factory** ★ | 低（支持 QLoRA） | ★ 极低，有 Web UI | 国产，中文友好，支持模型最多，一键启动 | **新手首选** |
| **Unsloth** | 低（QLoRA） | ★ 低 | 训练速度极快（比标准快 2-5 倍），内存优化 | 追求训练速度 |
| **HuggingFace TRL** | 中 | ★★ 中 | 官方标准库，最灵活，文档最全 | 需要定制训练流程 |
| **Axolotl** | 中（支持 QLoRA） | ★★ 中 | YAML 配置驱动，可复现性强 | 团队协作，需要版本管理 |
| **torchtune** | 中 | ★★ 中 | Meta 官方 PyTorch 微调库 | Meta 模型最佳支持 |
| **Firefly** | 低 | ★ 低 | 中文开源，预置中文数据集 | 中文社区新手 |

#### LLaMA-Factory 快速上手

```bash
# 安装（建议用 Docker，避免环境问题）
git clone https://github.com/hiyouga/LLaMA-Factory.git
cd LLaMA-Factory
pip install -e ".[torch,metrics]"

# 启动 Web UI
llamafactory-cli webui
# 浏览器打开 http://localhost:7860，选模型、选数据集、点开始
```

特点：支持 100+ 模型（Qwen、Llama、DeepSeek 等），内置 50+ 中文数据集，可视化配置所有超参数。

#### Unsloth 快速上手

```python
from unsloth import FastLanguageModel
import torch

# 加载模型（自动 4-bit 量化）
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="unsloth/Qwen3-8B",
    max_seq_length=2048,
    load_in_4bit=True,
)

# 加 LoRA 适配器
model = FastLanguageModel.get_peft_model(
    model,
    r=16,  # LoRA rank
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                    "gate_proj", "up_proj", "down_proj"],
)

# 训练...（详细步骤见 4.5 实战部分）
```

核心优势：重写了底层 CUDA 算子，**训练速度是标准 HF+Lora 的 2-5 倍**，显存占用少 50%。

### 商业微调/精调平台

| 平台 | 厂商 | 计费方式 | 适用场景 |
|---|---|---|---|
| **阿里云 PAI** | 阿里 | 按 GPU 时长计费 | Qwen 系列微调，企业级 |
| **ModelArts** | 华为 | 按 GPU 时长计费 | 昇腾环境微调 |
| **百炼平台** | 阿里 | 按训练 token + 托管费 | 零代码微调，Qwen 专精 |
| **火山引擎方舟** | 字节 | 按训练 token 计费 | 零代码微调，豆包系列 |
| **Replicate** | 海外 | 按 GPU 分钟计费 | 快速实验，无需配环境 |
| **Together AI** | 海外 | 按训练 token | 全托管，支持主流开源模型 |
| **讯飞星火** | 科大讯飞 | 按训练时长 | 中文场景，星火模型微调 |

> 推荐新手路径：先 LLaMA-Factory 本地跑通一条线 → 再考虑上云/商业平台。

---

## 4.3 大模型微调数据集

### 微调数据集分类

| 类型 | 格式 | 用途 | 举例 |
|---|---|---|---|
| **指令数据** | instruction-input-output | SFT 指令微调 | 「翻译成英文：你好→Hello」 |
| **对话数据** | messages (role + content) | 多轮对话微调 | 客服对话、工单处理 |
| **文本续写** | 纯文本 | 领域预训练 | 把你司的文档喂给模型 |
| **偏好数据** | chosen + rejected | RLHF/DPO | 「这个回答比那个好」 |
| **工具调用** | function call | 学会调用 API | 让模型学会查数据库、调接口 |

### 数据集格式

#### 格式一：Alpaca 格式（最通用，LLaMA-Factory 默认）

```json
[
  {
    "instruction": "你是一名运维工程师，请根据告警信息判断故障根因。",
    "input": "告警信息：生产环境 API 服务 502 错误，后端 upstream 为 10.0.1.50:8080，nginx error.log 显示 connect() failed (111: Connection refused)",
    "output": "根因判断：后端服务 10.0.1.50:8080 端口无进程监听。排查步骤：1. SSH 登录 10.0.1.50 检查服务进程是否存活 2. 检查端口监听 ss -tlnp | grep 8080 3. 查看服务自身日志。优先恢复：尝试重启服务，若重启失败切流量到备用节点。"
  },
  {
    "instruction": "将以下自然语言转换为 kubectl 命令",
    "input": "查看 kube-system 命名空间中所有 CrashLoopBackOff 状态的 pod",
    "output": "kubectl get pods -n kube-system --no-headers | awk '$3==\"CrashLoopBackOff\"{print $1}'\n# 或者更精确（注意不能用 --field-selector=status.phase!=Running，CrashLoopBackOff 的 Pod phase 是 Running）：\nkubectl get pods -n kube-system -o json | jq -r '.items[] | select(.status.containerStatuses[]?.state.waiting?.reason==\"CrashLoopBackOff\") | .metadata.name'"
  }
]
```

#### 格式二：ShareGPT 格式（多轮对话）

```json
[
  {
    "conversations": [
      {"from": "human", "value": "我们的 Redis 集群发生了主从切换，帮我分析一下可能的原因"},
      {"from": "gpt", "value": "好的，Redis 主从切换通常由以下原因触发：\n1. Sentinel 检测到主节点不可达（超时时间默认 30s）\n2. 主节点所在机器负载过高导致心跳丢失\n..."},
      {"from": "human", "value": "sentinel.conf 里的 down-after-milliseconds 设置的是 5000，还是触发了切换"},
      {"from": "gpt", "value": "5000ms 确实比较保守。排查方向：\n1. 检查那个时间点主节点的 CPU 使用率...\n2. 网络是否有短暂丢包...\n3. Redis 是否在 fork 子进程做 RDB 持久化..."}
    ]
  }
]
```

#### 格式三：OpenAI Chat 格式

```json
[
  {
    "messages": [
      {"role": "system", "content": "你是一个运维专家助手，回答简洁专业"},
      {"role": "user", "content": "Docker daemon 无法启动怎么办"},
      {"role": "assistant", "content": "常见排查步骤：\n1. journalctl -u docker -f 查看日志\n2. 检查 /etc/docker/daemon.json 是否有语法错误\n3. df -h 确认磁盘未满\n4. 是否有旧进程残留：ps aux | grep dockerd"}
    ]
  }
]
```

### 获取公共数据集

| 平台 | 说明 | 推荐数据集 |
|---|---|---|
| **HuggingFace Datasets** | 全球最大，数万数据集 | Open-Orca、Alpaca-GPT4、ShareGPT |
| **ModelScope** | 阿里出品，中文丰富 | 中文指令数据集合集 |
| **OpenDataLab** | 上海人工智能实验室 | 多领域中文数据 |
| **SFT-Data Hub** | 各模型厂开源 | 各厂商发布的微调数据 |

**新手推荐**：先从 HuggingFace 下载 `Open-Orca`（通用指令）或 ModelScope 搜「中文指令数据集」跑通流程，再制作自有数据。

### 制作自己的数据集

这是微调最核心也最耗时的一步。**数据质量决定微调效果上限**。

#### 制作流程

```
第 1 步：收集原始数据
  来源：工单记录、告警处理记录、运维文档、脚本注释
  目标：50-100 条高质量样本即可看到效果，500-2000 条能达到生产可用

第 2 步：清洗和格式化
  - 去掉敏感信息（密码、IP、手机号）
  - 统一格式为 Alpaca 或 ShareGPT
  - 确保 input/output 一一对应，没有错位

第 3 步：质量检查
  - 每一条都要人工看一遍（或让大模型帮你检查）
  - output 中的命令要验证是否能跑通
  - 风格是否一致（比如都用了中文回答、格式统一）

第 4 步：拆分数据集
  - 训练集：80%
  - 验证集：10%
  - 测试集：10%
```

#### 运维领域数据举例

```json
{
  "instruction": "生成一个 Nginx 配置片段",
  "input": "需求：反向代理 /api/* 到后端 8080 端口，/ws 到 8081 端口（WebSocket），其他路径返回静态文件 /var/www/html",
  "output": "```nginx\nlocation /api/ {\n    proxy_pass http://backend:8080;\n    proxy_set_header Host $host;\n    proxy_set_header X-Real-IP $remote_addr;\n}\n\nlocation /ws {\n    proxy_pass http://backend:8081;\n    proxy_http_version 1.1;\n    proxy_set_header Upgrade $http_upgrade;\n    proxy_set_header Connection \"upgrade\";\n}\n\nlocation / {\n    root /var/www/html;\n    try_files $uri $uri/ =404;\n}\n```"
}
```

#### 数量与效果的经验关系

| 数据量 | 效果预期 |
|---|---|
| 50-100 条 | 能明显感受到风格变化和格式遵从 |
| 200-500 条 | 格式基本稳定，领域术语掌握良好 |
| 500-2000 条 | **生产可用水准**，效果趋于稳定 |
| 5000+ 条 | 边际收益递减，除非数据质量极高 |

> 关键：100 条高质量数据 >> 1000 条垃圾数据。宁可少而精。

---

## 4.4 微调超参数

### 核心超参数速查

| 参数 | 含义 | 推荐值（LoRA/QLoRA） | 调参方向 |
|---|---|---|---|
| **learning_rate** | 学习率 | 2e-4（7B）/ 5e-5（70B） | 效果不收敛→调大；loss 震荡→调小 |
| **num_epochs** | 训练轮数 | 3-5 | 数据少可多一点（5-10），数据多就少（1-3） |
| **batch_size** | 批大小 | 4-8（单卡 24G） | 显存不够→调小 + 增大 gradient_accumulation |
| **gradient_accumulation** | 梯度累积步数 | 4-8 | batch_size 太小导致训练不稳定时调大 |
| **lora_r** | LoRA 秩 | 8-16 | 简单任务 8，复杂任务 32-64 |
| **lora_alpha** | LoRA 缩放 | lora_r × 2 | 一般是 r 的 2 倍 |
| **lora_dropout** | LoRA Dropout | 0.05-0.1 | 数据少就大一点防过拟合 |
| **max_seq_length** | 最大序列长度 | 2048-4096 | 根据数据最长文本设，太大会 OOM |
| **warmup_ratio** | 学习率预热比例 | 0.03-0.1 | 先用小学习率慢慢爬坡 |
| **weight_decay** | 权重衰减 | 0.01 | L2 正则化，防过拟合 |
| **optimizer** | 优化器 | adamw_8bit / paged_adamw_8bit | QLoRA 用 8bit 版省显存 |

### 学习率调参实战判断

```
loss 曲线：
  正常：逐步下降，趋于平缓
  不收敛（loss 不降）→ lr 太小，调到 5e-4
  震荡严重（loss 上蹿下跳）→ lr 太大，调到 1e-5
  过拟合（train loss 降但 eval loss 升）→ 加 dropout / 减 epoch
```

### LLaMA-Factory 常用超参配置模板

```yaml
# QLoRA 微调 7B 模型，单卡 RTX 4090 24GB
cutoff_len: 2048
lora_rank: 16
lora_alpha: 32
lora_dropout: 0.05
lora_target: all
learning_rate: 2.0e-4
num_train_epochs: 3
per_device_train_batch_size: 4
gradient_accumulation_steps: 4
lr_scheduler_type: cosine
warmup_ratio: 0.1
logging_steps: 10
save_steps: 100
eval_steps: 100
optim: paged_adamw_8bit
quantization_bit: 4
```

---

## 4.5 大模型微调实战

### 实战一：讯飞星火微调实战

#### 平台特点

- 零代码 Web 界面操作
- 支持星火 3.5 / 4.0 系列
- 中文场景优化，自带中文对话模板
- 托管式，训练完直接部署为 API 端点

#### 操作步骤

```
1. 登录讯飞开放平台 → 大模型训练平台
2. 创建数据集：
   - 上传 JSONL 文件（每行一条，格式：{"prompt":"...","completion":"..."}）
   - 或使用平台内置模板手动录入
   - 建议 200 条起步
3. 创建微调任务：
   - 选择基座模型（星火 3.5 或 4.0）
   - 选择数据集
   - 设置训练 epoch（默认 3）
4. 等待训练完成（通常 1-4 小时，看数据量）
5. 部署为 API → 获得专属 endpoint
6. 测试：curl 调用对比微调前后效果
```

#### 注意事项

- 数据不能出域的话慎用（有合规风险）
- 微调后的模型按 token 计费，长期大量使用成本高于自部署
- 不支持 LoRA 级别的精细控制，适合追求简单的人

---

### 实战二：LLaMA-Factory 微调 Qwen3

#### 环境准备

```bash
# 推荐配置：RTX 4090 24G / A100 40G+
git clone https://github.com/hiyouga/LLaMA-Factory.git
cd LLaMA-Factory
pip install -e ".[torch,metrics]"
```

#### 步骤 1：准备数据集

将自己的 JSON 数据（Alpaca 格式）放到 `data/` 目录，然后在 `data/dataset_info.json` 中注册：

```json
{
  "my_ops_data": {
    "file_name": "my_ops_data.json",
    "columns": {
      "prompt": "instruction",
      "query": "input",
      "response": "output"
    }
  }
}
```

#### 步骤 2：Web UI 训练（推荐新手）

```bash
llamafactory-cli webui
```

浏览器打开后在界面里：
1. **模型选择**：Qwen3-8B（或 Qwen3-4B 先跑通）
2. **微调方法**：LoRA / QLoRA
3. **数据集**：选 `my_ops_data`
4. **超参数**：使用 4.4 节的推荐值
5. **点击「开始」**，等待训练完成

#### 步骤 3：命令行训练（适合脚本化）

```bash
llamafactory-cli train \
  --model_name_or_path Qwen/Qwen3-8B \
  --dataset my_ops_data \
  --template qwen3 \
  --finetuning_type lora \
  --lora_rank 16 \
  --lora_target all \
  --output_dir ./output/qwen3-ops-lora \
  --per_device_train_batch_size 4 \
  --gradient_accumulation_steps 4 \
  --lr_scheduler_type cosine \
  --logging_steps 10 \
  --save_steps 500 \
  --learning_rate 2e-4 \
  --num_train_epochs 3 \
  --fp16
```

#### 步骤 4：合并并导出

```bash
# LoRA 权重合并到基座模型
llamafactory-cli export \
  --model_name_or_path Qwen/Qwen3-8B \
  --adapter_name_or_path ./output/qwen3-ops-lora \
  --template qwen3 \
  --finetuning_type lora \
  --export_dir ./output/qwen3-ops-merged \
  --export_size 2 \
  --export_legacy_format false
```

#### 步骤 5：推理测试

```bash
llamafactory-cli chat \
  --model_name_or_path ./output/qwen3-ops-merged \
  --template qwen3
# 输入你的运维问题，对比微调前后的差异
```

#### 步骤 6：用 vLLM 部署（生产环境）

```bash
# 微调合并后的模型可直接用 vLLM 部署（新版 CLI 是 vllm serve，
# 老写法 python -m vllm.entrypoints.openai.api_server 也还能用）
vllm serve ./output/qwen3-ops-merged \
  --served-model-name ops-assistant \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.9
```

---

### 实战三：Unsloth 微调 Qwen3

Unsloth 的优势是速度快、省显存，特别适合消费级显卡。

#### 环境准备

```bash
pip install unsloth
# 如果报 CUDA 错误，按官方文档装对应 CUDA 版本
pip install unsloth --upgrade --force-reinstall --no-deps
```

#### 完整训练脚本

```python
# train_qwen3_ops.py
from unsloth import FastLanguageModel
from datasets import load_dataset
import torch
from transformers import TrainingArguments
from trl import SFTTrainer

# 1. 加载模型和 tokenizer（4bit 量化）
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="unsloth/Qwen3-8B",
    max_seq_length=2048,
    dtype=None,           # 自动检测
    load_in_4bit=True,    # QLoRA
)

# 2. 添加 LoRA 适配器
model = FastLanguageModel.get_peft_model(
    model,
    r=16,                 # LoRA rank
    lora_alpha=32,
    lora_dropout=0.05,
    target_modules=[
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ],
    use_gradient_checkpointing="unsloth",  # Unsloth 优化版
)

# 3. 加载数据集（Alpaca 格式）
dataset = load_dataset("json", data_files="my_ops_data.json", split="train")

# 4. 格式化函数
def format_alpaca(examples):
    texts = []
    for inst, inp, out in zip(
        examples["instruction"],
        examples["input"],
        examples["output"]
    ):
        if inp:
            text = f"### 指令:\n{inst}\n### 输入:\n{inp}\n### 回答:\n{out}"
        else:
            text = f"### 指令:\n{inst}\n### 回答:\n{out}"
        texts.append(text + tokenizer.eos_token)
    return {"text": texts}

dataset = dataset.map(format_alpaca, batched=True)

# 5. 训练参数 & 训练
trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=dataset,
    dataset_text_field="text",
    max_seq_length=2048,
    args=TrainingArguments(
        per_device_train_batch_size=4,
        gradient_accumulation_steps=4,
        warmup_steps=10,
        num_train_epochs=3,
        learning_rate=2e-4,
        fp16=not torch.cuda.is_bf16_supported(),
        bf16=torch.cuda.is_bf16_supported(),
        logging_steps=1,
        optim="adamw_8bit",
        output_dir="./unsloth_qwen3_ops",
        save_steps=100,
    ),
)

trainer.train()

# 6. 保存模型
model.save_pretrained_merged(
    "qwen3-ops-merged",
    tokenizer,
    save_method="merged_16bit",  # 合并为 FP16 用于推理
)
```

#### 验证微调效果

```python
# 微调后推理测试
from unsloth import FastLanguageModel

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="./qwen3-ops-merged",
    max_seq_length=2048,
    dtype=None,
    load_in_4bit=True,
)
FastLanguageModel.for_inference(model)

prompt = "### 指令:\n生产环境 API 返回大量 502，请分析根因\n### 回答:\n"
inputs = tokenizer(prompt, return_tensors="pt").to("cuda")
outputs = model.generate(**inputs, max_new_tokens=512)
print(tokenizer.decode(outputs[0], skip_special_tokens=True))
```

---

### 实战速查：三个方案对比

| 维度 | 讯飞星火 | LLaMA-Factory | Unsloth |
|---|---|---|---|
| **上手难度** | ★（最低） | ★（有 Web UI） | ★★（写代码） |
| **可控制性** | ★（低） | ★★★（高） | ★★★（最高） |
| **训练速度** | 看平台 | 标准 | ★★★（最快，2-5x 加速） |
| **模型选择** | 仅星火 | 100+ 模型 | 主流模型 |
| **数据安全** | 数据上云 | 完全本地 | 完全本地 |
| **成本** | 按量付费 | 仅 GPU 电费 | 仅 GPU 电费 |
| **适用** | 快速验证想法 | 生产级微调首选 | 消费级显卡最优解 |

> 建议：先在讯飞星火或百炼平台快速验证「微调是否有用」→ 确认有价值后，用 LLaMA-Factory 或 Unsloth 本地微调 → vLLM 部署到生产。
