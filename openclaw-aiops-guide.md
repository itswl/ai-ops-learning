# OpenClaw 运维实战：从部署到 AIOps

> 创建时间: 2026-05-07（2026-08 按真实项目重写勘误）

> ⚠️ 勘误说明：本文早期版本把 OpenClaw 描述成一个"Django + Celery + PostgreSQL 的多 Agent 运维编排框架"，包括 `manage.py createsuperuser`、`team.yaml`、8800/8801 端口、企微/钉钉通道等内容，**全部是 AI 编造的，真实项目里不存在**。以下内容以官方文档（docs.openclaw.ai）为准重写。

---

## 10.1 OpenClaw 介绍

### 是什么

OpenClaw 是 Peter Steinberger 开源的**个人 AI 助手网关**（前身叫 Clawdbot，2026 年 1 月因商标问题改名）。技术栈是 TypeScript/Node.js。它做的事情是：

- 在你的机器上跑一个常驻 **Gateway** 进程；
- 把 LLM Agent（默认 Claude 系，也支持其他 provider）接到你日常用的 IM 上；
- Agent 拥有工作区、会话记忆、技能（Skills）和执行本机命令的能力。

它**不是**一个运维产品，而是一个"带手脚的 AI 助手运行时"。对运维工程师的价值在于：Agent 能执行真实 CLI（kubectl / ansible / promtool / logcli ...），所以只要在一台配好运维工具链的跳板机上部署它，就能得到一个"在 IM 里聊天就能查集群、查监控、查日志"的运维助手——这一层是你自己用 Skills 和权限边界搭出来的，不是产品自带的。

### 和 Coze/FastGPT 的区别

| | OpenClaw | Coze | FastGPT |
|---|---|---|---|
| **定位** | 个人 AI 助手网关（自托管） | 通用 AI Bot 平台 | 知识库问答平台 |
| **核心能力** | IM 多通道接入 + 本机命令执行 + Skills | 插件 + 工作流编排 | RAG 知识库 |
| **部署方式** | 自部署（你的机器/VM） | SaaS + 企业版 | 自部署 |
| **执行真实命令** | ★★★★★ 原生（Agent 直接跑 shell） | 需要自己写插件调 API | 基本不涉及 |
| **多 Agent** | 多 Agent 实例 + 消息路由（非编排） | 有多 Agent 模式 | 不支持 |
| **风险面** | 高——Agent 有 shell，权限要自己收敛 | 低（沙箱内） | 低 |
| **适用场景** | 个人/小团队 ChatOps、运维助手 | 面向业务的对话机器人 | 企业知识库问答 |

### 真实架构

```
┌────────────────────────────────────────────────────────┐
│                      IM 通道层                           │
│  Telegram │ 飞书 │ Slack │ Discord │ WhatsApp │ iMessage │
│  Google Chat │ Mattermost │ MS Teams │ Signal            │
│  （注意：不支持企业微信和钉钉，别按老版本文档去找）           │
└───────────────────────┬────────────────────────────────┘
                        │
┌───────────────────────┴────────────────────────────────┐
│              OpenClaw Gateway（常驻进程）                 │
│  默认监听 127.0.0.1:18789（含 Control UI 网页控制台）      │
│  ├── 消息路由：channel/账号/群 → 绑定到某个 Agent          │
│  ├── Agent 运行时：会话、工作区、记忆                      │
│  ├── Skills：按目录组织的能力包（SKILL.md）                │
│  └── 定时任务（cron）：定点触发 Agent 干活                 │
└───────────────────────┬────────────────────────────────┘
                        │ Agent 执行本机命令
┌───────────────────────┴────────────────────────────────┐
│            这台机器上你准备好的运维工具链                   │
│  kubectl（只读 kubeconfig）│ ansible │ promtool/curl      │
│  logcli(Loki) │ 自定义脚本  │ MCP Server（可选扩展）       │
└────────────────────────────────────────────────────────┘
```

---

## 10.2 OpenClaw 部署

### 环境要求

```
OS: macOS / Linux（推荐一台专用小 VM 或跳板机）
Node.js: 22+（安装脚本会处理）
内存: 1-2GB 足够（它只是网关+Agent 运行时，模型在云端）
安全前提: 这台机器上的凭证（kubeconfig、SSH key）就是 Agent 的权限边界
```

### 部署步骤（真实命令）

```bash
# 1. 官方安装脚本（macOS/Linux）
curl -fsSL https://openclaw.ai/install.sh | bash

# 2. 初始化向导：配置模型 API Key、连接第一个 IM 通道、
#    并把 Gateway 注册为后台服务（launchd/systemd）
openclaw onboard

# 3. 确认 Gateway 状态
openclaw gateway status

# 4. 自检（环境/配置/通道连通性）
openclaw doctor

# 5. 打开控制台（本机访问）
# 浏览器打开 http://127.0.0.1:18789 —— Control UI，可视化改配置、看会话
```

### 配置文件

配置在 `~/.openclaw/openclaw.json`（JSON5，允许注释；**schema 严格校验**，写了未知字段 Gateway 会拒绝启动）：

```json5
{
  // 模型 provider（示例：Anthropic 为主，本地 vLLM 兜底可另配 openai 兼容端点）
  models: {
    providers: {
      anthropic: { apiKey: "${ANTHROPIC_API_KEY}" },
    },
  },

  // Agent：工作区就是它的"家目录"
  agents: {
    defaults: {
      workspace: "~/.openclaw/workspace",
    },
    entries: {
      main: { default: true },
    },
  },

  // 通道：每个 IM 一个小节，白名单是第一道安全线
  channels: {
    telegram: {
      botToken: "${TELEGRAM_BOT_TOKEN}",
      // 只允许这些账号跟它说话
      allowFrom: ["@your_username"],
    },
  },
}
```

> 安全提醒：Gateway 只应监听 127.0.0.1。需要远程管理就走 SSH 隧道或 Tailscale，**永远不要**把 18789 端口暴露到公网。

---

## 10.3 OpenClaw 接入聊天工具

支持的通道（2026-08 官方列表）：Telegram、飞书（Feishu）、Slack、Discord、WhatsApp、iMessage、Google Chat、Mattermost、Microsoft Teams、Signal。

> 国内环境注意：**企业微信、钉钉没有官方通道**。要接入这两个，得自己写桥接（比如企微回调 → 转发到 OpenClaw 的 API/一个自建通道），或干脆选飞书/Telegram。

### 10.3.1 接入 Telegram

```bash
# 1. Telegram 里找 @BotFather → /newbot → 拿到 Bot Token

# 2. 写入配置 ~/.openclaw/openclaw.json
#    channels.telegram.botToken = "7123456789:AAH..."
#    channels.telegram.allowFrom = ["@你的用户名"]

# 3. 重载配置（Gateway 会热加载合法配置）后，
#    Telegram 里给 Bot 发消息即可。首次陌生账号会走配对确认，防止路人使唤你的 Agent
```

### 10.3.2 接入飞书

飞书是官方支持的通道，流程和所有飞书自建应用一致：

```
第 1 步：飞书开放平台 → 创建企业自建应用
第 2 步：开通机器人能力，订阅 im.message.receive_v1 事件
第 3 步：拿到 App ID / App Secret，填进 openclaw.json 的 channels.feishu 小节
第 4 步：事件回调地址指向你的 Gateway（内网部署时用官方推荐的长连接/代理方式）
第 5 步：发布应用，在飞书里 @机器人 对话
```

（具体字段名以 docs.openclaw.ai 的 Feishu 通道页为准，飞书侧的凭证概念——App ID/Secret/事件订阅——和你配其他飞书机器人没有任何区别。）

**飞书卡片消息**：OpenClaw 的回复是文本/Markdown 为主。要做"按钮确认"类交互（比如下文的高危操作审批），用文本确认词（"确认执行"/"取消"）最可靠，别指望它原生发复杂交互卡片。

---

## 10.4 多 Agent：路由隔离，而不是编排

真实的 OpenClaw 多 Agent 模型是：**多个 Agent 实例，各自独立的工作区和会话，用路由规则决定"哪条消息进哪个 Agent"**。它不是 CrewAI 那种"指挥 Agent 拆解任务分派给专业 Agent"的编排框架——那类编排要靠 LangGraph 等外部框架自己搭。

对运维场景，这个模型反而更实用——按**权限边界**切 Agent：

```json5
{
  agents: {
    defaults: { workspace: "~/.openclaw/workspace" },
    entries: {
      // 个人助手：全能力，只有你自己私聊能用
      main: { default: true },

      // 运维只读助手：绑定到运维群，工作区里只有只读 kubeconfig、
      // 只读的 Prometheus/Loki 查询脚本，皮肤是"值班助手"
      "ops-readonly": {
        workspace: "~/.openclaw/ops-readonly",
        // 用 skills 白名单收敛能力（省略 = 不限制，[] = 全禁）
        skills: ["k8s-readonly", "prom-query", "loki-query"],
      },
    },
  },
  // 路由绑定：运维群的消息 → ops-readonly；你的私聊 → main
  // （bindings 的具体写法以官方 configuration-reference 为准）
}
```

这样"运维群里任何人都能问，但只能查不能改；改的能力只在你私聊的 main Agent 里"——权限模型是靠**Agent 隔离 + 工作区里放什么凭证**实现的，比 prompt 里写"你不许执行危险命令"可靠得多。

---

## 10.5 OpenClaw 实战

### Skills：把巡检流程做成能力包

OpenClaw 的 Skill 遵循 AgentSkills 约定：工作区 `skills/` 下一个目录 + `SKILL.md`（说明什么时候用、怎么用）+ 附带脚本。Agent 会按需读取并执行。

```
~/.openclaw/ops-readonly/skills/env-health-check/
├── SKILL.md
└── scripts/
    ├── check_nodes.sh      # kubectl get nodes / top nodes
    ├── check_pods.sh       # 异常 Pod 扫描
    ├── check_alerts.sh     # curl Alertmanager /api/v2/alerts
    └── check_capacity.sh   # PromQL：CPU/内存/磁盘水位
```

```markdown
<!-- SKILL.md 示例 -->
---
name: env-health-check
description: 生产环境例行巡检。用户说"巡检"/"健康检查"/"看看环境"时使用。
---

# 环境巡检

按顺序执行 scripts/ 下的四个脚本，汇总为一份报告：

1. check_nodes.sh —— 节点 Ready 状态与资源水位
2. check_pods.sh —— 非 Running/未就绪 Pod 清单
3. check_alerts.sh —— 当前活跃告警
4. check_capacity.sh —— CPU/内存/磁盘超过 80% 的节点

报告格式：
## 环境巡检报告
### 概览（健康度：X/N 正常）
### 异常项（[严重]/[警告] 分级）
### 建议
所有结论必须来自脚本输出，不允许推测。
```

**触发方式**：

```
# 方式1：任意已接通道里直接说
"做一次环境巡检"

# 方式2：内置定时任务（cron），到点让 Agent 执行并把报告发到指定通道
openclaw cron add --schedule "0 */4 * * *" --prompt "执行环境巡检并输出报告"

# 具体 cron 子命令参数以 openclaw cron --help 为准
```

---

## 10.6 OpenClaw 与 AIOps

核心思想只有一句话：**OpenClaw 负责"嘴和手"，运维能力来自这台机器上你配好的工具链，安全来自你给这台机器的凭证权限**。以下集成全部是"在 Agent 所在机器上装好 CLI + 用 Skill 教会它用法"，没有任何平台级魔法。

### 10.6.1 + Ansible：管 Linux 主机

```bash
# Agent 所在机器上装 Ansible，配好清单
pip install ansible

cat > /etc/ansible/hosts << 'EOF'
[web]
web-01 ansible_host=10.0.1.10
web-02 ansible_host=10.0.1.11
web-03 ansible_host=10.0.1.12

[all:vars]
ansible_user=ops
ansible_ssh_private_key_file=/home/openclaw/.ssh/id_rsa
EOF
```

安全边界（重要，顺序就是优先级）：

```
1. SSH 层：给 Agent 用的 ops 账号在目标机上不给 sudo，
   或 sudoers 只放行 systemctl status/journalctl 等只读命令
2. Skill 层：SKILL.md 里写明只允许 df/free/uptime/ss/systemctl status 类只读命令，
   写操作必须先输出计划、等用户回复"确认执行"
3. 通道层：allowFrom 白名单 + 群里用只读 Agent
不要指望第 2 层单独兜底——prompt 约束挡不住注入，硬边界永远在第 1 层。
```

对话效果（真实交互模式示意）：

```
用户：帮我看看 web 组所有机器的磁盘使用情况

Agent：（执行 ansible web -m shell -a "df -h /"）
| 机器 | 总量 | 已用 | 使用率 |
|------|------|------|--------|
| web-01 | 40G | 15G | 37% |
| web-02 | 40G | 32G | 80% ⚠️ |
| web-03 | 40G | 38G | 95% 🔴 |

web-03 磁盘 95%，要我看看是什么占的吗？

用户：查
Agent：（执行 ansible web-03 -m shell -a "du -xh /var 2>/dev/null | sort -rh | head"）
/var/log/nginx/access.log 占 12G，30 天没轮转。
建议配置 logrotate。这是写操作，需要你确认后我再执行。
```

### 10.6.2 + Kubernetes：智能 K8s 排障

```bash
# 关键在 kubeconfig 的权限，而不在工具本身：
# 给 Agent 一个只读 ServiceAccount
kubectl create serviceaccount openclaw-readonly -n ops
kubectl create clusterrolebinding openclaw-view \
  --clusterrole=view --serviceaccount=ops:openclaw-readonly
# 用这个 SA 的 token 生成 kubeconfig，放进 Agent 工作区
# （view 角色天然看不了 Secret 内容之外的敏感写口，扩缩容/删 Pod 一律 Forbidden）
```

对话效果：

```
用户：帮我看下 prod 命名空间有没有异常

Agent：（kubectl get pods -n prod）
api-gateway-9x2h 处于 CrashLoopBackOff（重启 128 次）。继续排查：
（kubectl describe pod ... → Last State: OOMKilled）
（kubectl logs --previous → 启动时连接 Redis 超时）
（kubectl get endpointslice -l kubernetes.io/service-name=redis -n prod → 无就绪后端）

诊断：Redis Service 没有健康后端，api-gateway 启动即失败；
容器又因内存超限被 OOMKill，两个问题叠加。
我的 kubeconfig 是只读的，修复动作（重建 Redis Pod）请值班同学执行，
或切到有写权限的 Agent 走审批流程。
```

这里的关键设计：**"只读诊断"和"可写修复"物理分离**（两个 Agent、两份 kubeconfig），修复动作永远带"计划 → 人工确认 → 执行 → 复验"四步。

### 10.6.3 + Prometheus/Alertmanager：告警分析

```bash
# 不需要专门的"集成插件"，HTTP API + curl/jq 就是全部：
# 活跃告警
curl -s http://alertmanager:9093/api/v2/alerts | jq '.[] | {alertname: .labels.alertname, severity: .labels.severity}'
# 即时查询
curl -s 'http://prometheus:9090/api/v1/query?query=up==0'
# 范围查询（趋势）
curl -s 'http://prometheus:9090/api/v1/query_range?query=rate(http_requests_total{code=~"5.."}[5m])&start=...&end=...&step=60'
```

把常用 PromQL 封装成脚本放进 Skill，让 Agent 做"告警 → 关联指标 → 时间线 → 结论"的分析。要求它：结论必须引用查询结果，不足以判断就明说。

### 10.6.4 + Loki：日志分析

```bash
# logcli 是最顺手的接口
logcli query '{namespace="prod", level="error"}' --since=1h --limit=200
# 或 HTTP API
curl -G http://loki:3100/loki/api/v1/query_range \
  --data-urlencode 'query={namespace="prod"} |= "error"' \
  --data-urlencode 'since=1h'
```

Skill 里可以预置常见错误模式（OOM / Connection refused / No space left / NXDOMAIN / certificate expired），让 Agent 先跑模式匹配再做自由分析，比裸看日志省 token 也稳定。

---

## 安全清单（自托管 Agent 的命门）

```
□ Gateway 只监听 127.0.0.1，远程访问走 SSH 隧道/Tailscale
□ 每个通道配 allowFrom 白名单；群场景只挂只读 Agent
□ Agent 专用系统账号运行，不给 sudo
□ kubeconfig 用只读 SA；SSH key 对应账号无提权能力
□ 写操作 Agent 与读操作 Agent 分离，写操作必须人工确认
□ 凭证不进工作区明文文件（Agent 能读到的东西 = 可能被 prompt 注入骗走的东西）
□ 会话/命令留痕，定期审计 Agent 执行过什么
□ 及时跟进官方安全公告并升级（这类"有 shell 的 Agent"是高价值攻击面）
```

---

## 快速上手路线

```
第 1 步：install.sh 安装 + openclaw onboard（10 分钟）
第 2 步：接一个 IM 通道（Telegram 最快，国内团队用飞书）
第 3 步：跳板机上配好只读 kubeconfig + promtool/logcli
第 4 步：跑通第一个场景——"帮我看下 prod 有没有异常 Pod"
第 5 步：把巡检流程写成 Skill + cron 定时巡检
第 6 步：按权限边界拆多 Agent（群只读 / 私聊可写带确认）
第 7 步：写安全清单并演练一次 prompt 注入攻击，验证边界真的兜得住
```
