# AI SRE 原型：RCA 评估流水线 + 五层防御的 L1 修复

把[第八阶段深水区笔记](../../cloud-native-ai-sre-roadmap/第八阶段学习资料-AIOpsAISRE深水区.md)里两个纸面交付变成能跑的代码：

1. **RCA 评估流水线**——离线评估集（含"证据不足"样本）+ 指标计算，能演示"改坏 prompt → 指标下降"。
2. **五层防御的 L1 修复动作**——重启无状态 Pod，安全边界建立在机制而非 prompt 上。
3. **端到端 demo**——RCA 判断 → 风险分级决策门 → L1 执行。

后端是**本地 vLLM**（OpenAI 兼容）。为了无 vLLM、无 K8s 集群也能验证，提供了确定性的 `mock` 后端和内存版 `FakeK8sClient`——**核心路径零第三方依赖，`git clone` 后直接 `python3` 就能跑**。

## 快速开始（零依赖，离线）

```bash
cd ai-sre-prototype

# 1. 评估流水线：good prompt
python3 eval/run_eval.py --backend mock
#   → 弃权召回 100%、综合安全且正确 100%

# 2. 回归演示：换成故意删掉"弃权规则"的 broken prompt
python3 eval/run_eval.py --backend mock --prompt prompts/rca_v2_broken.txt
#   → 弃权召回暴跌到 0%、综合掉到 75% —— 评估集抓住了 prompt 回归

# 3. 五层防御修复动作的单测（8 个用例，覆盖每层 + 成功/失败路径）
python3 tests/test_remediation.py

# 4. 端到端：RCA → 决策门 → L1 修复
python3 demo_e2e.py --backend mock
#   → 资源类事件触发重启并执行；发布类事件只建议不自动执行
```

## 接真实模型（任意 OpenAI 兼容服务）

`--backend vllm/ollama/openai` 都走同一个 stdlib `urllib` 客户端,只连 `/v1/chat/completions`,**不需要装 openai 包**。response_format 自动降级(json_schema → json_object → 纯 prompt),所以对接谁都行。

### Linux + NVIDIA GPU：vLLM

```bash
vllm serve Qwen/Qwen3-8B     # 默认 :8000
python3 eval/run_eval.py --backend vllm --model Qwen/Qwen3-8B
```

### macOS（Apple Silicon）：用 Ollama，别用 vLLM

vLLM 在 macOS 只有实验性 CPU 后端，跑 8B 不可用。Mac 上用 Ollama（Metal 加速）：

```bash
# 装（任选）：brew install ollama  或官网安装包
ollama serve &                 # 起服务，OpenAI 兼容口在 :11434/v1
ollama pull qwen3:8b           # 拉模型（首次几个 GB）

python3 eval/run_eval.py --backend ollama \
  --base-url http://localhost:11434/v1 --model qwen3:8b
python3 eval/run_eval.py --backend ollama \
  --base-url http://localhost:11434/v1 --model qwen3:8b \
  --prompt prompts/rca_v2_broken.txt        # 看真实模型的回归幅度
python3 demo_e2e.py --backend ollama \
  --base-url http://localhost:11434/v1 --model qwen3:8b
```

### 远程 GPU 上的 vLLM

```bash
python3 eval/run_eval.py --backend vllm \
  --base-url http://<gpu-box>:8000/v1 --model Qwen/Qwen3-8B
```

留存指标做版本/后端对比：`--json-out results_ollama.json`。

## 目录

```
models.py              共享数据模型（IncidentContext / RCAOutput，dataclasses 零依赖）
llm_client.py          VLLMClient（urllib 直连）+ MockLLMClient（确定性替身）
rca_agent.py           RCA Agent：上下文渲染 + 弃权 + JSON 解析
prompts/
  rca_v1_good.txt      正确 prompt（含"证据不足必须弃权"规则）
  rca_v2_broken.txt    回归对照（删掉弃权规则）
eval/
  dataset.jsonl        20 样本，其中 5 个"证据不足"样本专考弃权能力
  run_eval.py          评估流水线 + 指标表
remediation/
  k8s_client.py        FakeK8sClient（离线）+ RealK8sClient（真集群）
  audit.py             审计 + 幂等
  actions.py           RestartPodAction：五层防御
tests/
  test_remediation.py  8 个用例
demo_e2e.py            端到端串联
```

## 它演示了深水区笔记的哪些要点

**评估工程**
- 评估集含 20-30% 的"证据不足"样本（这里 5/20），专门考 Agent 会不会说"我判不了"。
- 分开评检索/判断两侧：`category_accuracy`（判断）、`abstain_recall`（安全核心）、`hallucination_rate`（引用了上下文外的证据编号）。
- `mock` 后端会读 system prompt 里有没有弃权规则来决定行为，因此能真实复现"prompt 回归被评估集抓住"——这验证了评估集的价值不是摆设。

**上下文工程**
- `rca_agent.render_user_message` 把事件渲染成带证据编号、带数据源状态的结构化上下文，而不是灌原始日志。
- 明确告诉模型哪些数据源 `degraded/unavailable`，防止把"没查到"当成"没问题"。

**安全边界是机制不是 prompt**（`remediation/actions.py`）
- 第 1 层 权限：`FakeK8sClient` 根本没有 `delete_deployment` 方法——想越权也无从调用；真集群靠只读+删Pod 的 RBAC。
- 第 2 层 动作白名单 + 参数 schema：只有一个参数化动作，绝不让 LLM 生成 shell。
- 第 3 层 风险分级 + 前置条件：拒绝有状态负载、拒绝 CrashLoop（重启治不好）、拒绝不存在的 Pod。
- 第 4 层 执行 + 成功/失败判据 + **失败即停**：重启后未 ready 就升级人工，绝不自动尝试别的动作。
- 第 5 层 审计 + 幂等：每次执行留决策快照；同一事件对同一 Pod 不重复重启。

**决策门**（`demo_e2e.py`）
- 只有"重启能缓解"的类别（resource/dependency）+ 置信度达标才触发 L1；deploy 类只建议（该回滚不该重启）。

## 从原型到生产的差距（诚实清单）

这是**教学原型**，不是生产系统。上真环境至少还要补：

- 真实上下文收集器：现在的 `IncidentContext` 是手工/离线的，生产要接 Prometheus/Loki/trace/变更源自动拉取（笔记第 3-4 周的关联引擎 + 上下文工程）。
- 评估集要用**真实历史事故**且冻结"当时的数据快照"，20 条起步、持续扩充；接入影子模式。
- `RealK8sClient` 要配套真正的最小权限 RBAC，并做 dry-run 灰度。
- 修复动作执行期间要"接管"该资源的其他自动化（暂停 HPA 等），避免三方打架。
- 观测与告警：Agent 自身也是生产服务，要有它自己的 SLO 和成本监控。
