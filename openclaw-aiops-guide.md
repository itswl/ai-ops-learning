# OpenClaw 运维实战：从部署到 AIOps

> 创建时间: 2026-05-07

---

## 10.1 OpenClaw 介绍

### 是什么

OpenClaw 是一个开源的**多 Agent 运维编排框架**，核心定位是「用自然语言驱动运维操作」。它将 LLM 和运维工具链深度整合，让运维工程师通过聊天就能完成机器管理、集群操作、监控查询、日志分析。

### 和 Coze/FastGPT 的区别

| | OpenClaw | Coze | FastGPT |
|---|---|---|---|
| **定位** | 运维 Agent 框架 | 通用 AI Bot 平台 | 知识库问答平台 |
| **核心能力** | 多 Agent 协作 + 运维工具深度集成 | 插件 + 工作流编排 | RAG 知识库 |
| **部署方式** | 自部署 | SaaS + 自部署 | 自部署 |
| **运维友好度** | ★★★★★ 原生运维集成 | ★★★ 需要自定义插件 | ★★ 知识库为主 |
| **多 Agent** | 原生支持 | 实验性 | 不支持 |
| **适用场景** | 智能运维、自动化操作 | 通用对话机器人 | 知识库问答 |

### 核心架构

```
┌─────────────────────────────────────────────────────────┐
│                    OpenClaw 平台                          │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │              Agent 协作层                          │   │
│  │  指挥Agent + 诊断Agent + 日志Agent + 监控Agent     │   │
│  └──────────────────────────────────────────────────┘   │
│          ↓               ↓               ↓              │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐          │
│  │ 工具集成层│  │  IM 接入层│  │  知识库层     │          │
│  │ Ansible  │  │ Telegram │  │  运维文档     │          │
│  │ K8s      │  │ 飞书/企微 │  │  故障复盘     │          │
│  │ Prometheus│ │ 钉钉     │  │  脚本库      │          │
│  │ Loki     │  │ API      │  │  监控规则     │          │
│  └──────────┘  └──────────┘  └──────────────┘          │
└─────────────────────────────────────────────────────────┘
```

---

## 10.2 OpenClaw 部署

### 10.2.1 云主机部署

#### 环境要求

```
OS: Ubuntu 22.04+ / CentOS 8+
CPU: 4 核+
内存: 8GB+
磁盘: 40GB+（Docker 镜像 + 日志）
Docker: 24.0+
Docker Compose: 2.20+
```

#### 部署步骤

```bash
# 1. 安装 Docker
curl -fsSL https://get.docker.com | bash
sudo usermod -aG docker $USER
# 重新登录生效

# 2. 克隆仓库
git clone https://github.com/openclaw/openclaw.git /opt/openclaw
cd /opt/openclaw

# 3. 配置环境变量
cp .env.example .env
vim .env
```

```bash
# .env 关键配置
# LLM 后端（OpenAI 兼容 API）
OPENCLAW_LLM_BASE_URL=http://your-llm-server:8000/v1
OPENCLAW_LLM_API_KEY=sk-xxx
OPENCLAW_LLM_MODEL=qwen3-8b

# 数据库
OPENCLAW_DB_HOST=postgres
OPENCLAW_DB_USER=openclaw
OPENCLAW_DB_PASSWORD=changeme
OPENCLAW_DB_NAME=openclaw

# 加密密钥（用于存储敏感配置）
OPENCLAW_SECRET_KEY=$(openssl rand -hex 32)

# 日志级别
OPENCLAW_LOG_LEVEL=INFO
```

```bash
# 4. 启动服务
docker-compose up -d

# 5. 检查服务
docker-compose ps
# openclaw-web       — Web UI (端口 8800)
# openclaw-api       — API 服务 (端口 8801)
# openclaw-worker    — 后台任务处理
# postgres           — 数据库
# redis              — 消息队列

# 6. 验证
curl http://localhost:8801/api/health
# {"status": "ok"}
```

```bash
# 7. 初始化管理员账号
docker exec -it openclaw-api python manage.py createsuperuser
# Username: admin
# Password: ******

# 8. Nginx 反代（可选）
cat > /etc/nginx/sites-available/openclaw << 'NGINX'
server {
    listen 80;
    server_name openclaw.your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8800;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8801;
        proxy_set_header Host $host;
    }
}
NGINX

ln -s /etc/nginx/sites-available/openclaw /etc/nginx/sites-enabled/
nginx -t && nginx -s reload
```

---

### 10.2.2 本地部署

```bash
# 1. 前置依赖
# macOS
brew install python@3.12 node@20 postgresql@16 redis

# Ubuntu
sudo apt install python3.12 nodejs postgresql redis-server

# 2. 创建 Python 虚拟环境
python3.12 -m venv /opt/openclaw/venv
source /opt/openclaw/venv/bin/activate

# 3. 安装依赖
cd /opt/openclaw
pip install -r requirements.txt
npm install --prefix frontend

# 4. 初始化数据库
createdb openclaw
python manage.py migrate

# 5. 构建前端
cd frontend && npm run build && cd ..

# 6. 启动各组件（开发模式）
# 终端 1：API 服务
python manage.py runserver 0.0.0.0:8801

# 终端 2：Worker
celery -A openclaw worker -l info

# 终端 3：Web
cd frontend && npm run dev -- --port 8800
```

---

## 10.3 OpenClaw 接入聊天工具

### 统一配置入口

所有 IM 接入在 OpenClaw Web UI 中统一管理：

```
OpenClaw → 设置 → 通道管理 → 添加通道
```

每个通道配置完成后，用户在对应 IM 工具中 @机器人 即可与 Agent 交互。

---

### 10.3.1 接入 Telegram

```bash
# 1. 在 Telegram 创建 Bot
# 搜 @BotFather → /newbot → 填名称 → 获得 Token
# 示例 Token: 7123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 2. OpenClaw Web UI 配置
通道类型：Telegram
Bot Token：7123456789:AAHxxx...
代理模式：Webhook（推荐）或 Polling

# 3. 如果选 Webhook，需要配置 Nginx 反代
# Telegram 要求 Webhook URL 使用 HTTPS
```

```
curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://openclaw.your-domain.com/api/channels/telegram/webhook"
```

配置完成后在 Telegram 中搜索你的 Bot，发送 `/start` 即可开始对话。

---

### 10.3.2 接入飞书

```
飞书开放平台 → 创建应用 → 企业自建应用

第 1 步：创建应用
  名称：OpenClaw 运维助手
  描述：智能运维机器人

第 2 步：添加能力
  ├── 机器人（启用）
  └── 事件订阅
      请求网址：https://openclaw.your-domain.com/api/channels/feishu/event
      订阅事件：
        im.message.receive_v1（接收消息）
        im.message.send_v1（发送消息）

第 3 步：获取凭证
  App ID：cli_axxxxx
  App Secret：xxxxxx
  Verification Token：xxxxxx

第 4 步：OpenClaw Web UI 配置
  通道类型：飞书
  App ID：cli_axxxxx
  App Secret：xxxxxx
  Verification Token：xxxxxx

第 5 步：发布
  飞书开放平台 → 版本管理 → 创建版本 → 发布
  管理员审批后在飞书中搜索应用名称即可使用
```

**飞书卡片消息示例**（OpenClaw 内置支持）：

```json
{
  "msg_type": "interactive",
  "card": {
    "header": {
      "title": {"tag": "plain_text", "content": "故障排查结果"},
      "template": "red"
    },
    "elements": [
      {
        "tag": "div",
        "text": {"tag": "lark_md", "content": "**根因**：web-01 的 Java 进程 GC 频繁引发 CPU 飙高\n\n**建议**：调大堆内存至 4G 并重启"}
      },
      {
        "tag": "action",
        "actions": [
          {"tag": "button", "text": {"tag": "plain_text", "content": "确认重启"}, "type": "primary"},
          {"tag": "button", "text": {"tag": "plain_text", "content": "先查看日志"}, "type": "default"}
        ]
      }
    ]
  }
}
```

---

### 10.3.3 接入企业微信

```
企业微信管理后台 → 应用管理 → 创建应用

第 1 步：创建应用
  应用名称：OpenClaw 运维助手
  Logo：上传一个运维图标

第 2 步：获取凭证
  Corp ID（企业 ID）：ww1234567890abcdef
  Agent ID：1000001
  Secret：xxxxxx

第 3 步：配置接收消息
  URL：https://openclaw.your-domain.com/api/channels/wecom/callback
  Token：随机字符串（OpenClaw 生成）
  EncodingAESKey：随机 43 位字符串

第 4 步：OpenClaw Web UI 配置
  通道类型：企业微信
  Corp ID：ww1234567890abcdef
  Agent ID：1000001
  Secret：xxxxxx
  Token：和上一步一致
  EncodingAESKey：和上一步一致

第 5 步：设置可信 IP
  企业微信管理后台 → 应用管理 → 企业可信 IP
  添加 OpenClaw 服务器的公网 IP
```

**企业微信特有的交互设计**：

```
企业微信机器人适合「消息卡片 + 按钮」交互模式：

用户：@OpenClaw 帮我查所有机器状态

Bot 卡片回复：
┌─────────────────────────────────┐
│ 📊 当前 8 台机器状态             │
│                                 │
│ ✅ 7 台正常运行                   │
│ 🔴 1 台异常 (web-03 CPU 92%)    │
│                                 │
│ [查看详情] [排查 web-03]         │
└─────────────────────────────────┘
```

---

### 10.3.4 接入钉钉

```
钉钉开放平台 → 创建应用 → 企业内部应用

第 1 步：创建应用
  应用类型：机器人
  开发模式：Stream 模式（推荐，无需公网 IP）

第 2 步：获取凭证
  App Key：dingxxxxxxxxx
  App Secret：xxxxxx

第 3 步：权限配置
  ├── 企业内机器人发送消息
  └── 接收消息

第 4 步：OpenClaw Web UI 配置
  通道类型：钉钉
  App Key：dingxxxxxxxxx
  App Secret：xxxxxx
  连接模式：Stream（推荐）

第 5 步：发布
  版本管理与发布 → 创建版本 → 发布
  企业内搜索机器人名称即可使用
```

**钉钉 Stream 模式优势**：

```
无需公网 IP 和 HTTPS 证书
钉钉主动推消息 → OpenClaw 本地接收
适合内网环境部署
```

---

## 10.4 OpenClaw 多 Agent 协作

### Agent 协作架构

```
          ┌─────────────────────┐
          │  指挥 Agent          │
          │  (Orchestrator)     │
          │  接收请求→分配任务→汇总 │
          └──────┬──────────────┘
                 │
    ┌────────────┼────────────┬──────────────┐
    ↓            ↓             ↓              ↓
┌────────┐ ┌─────────┐ ┌──────────┐ ┌──────────┐
│诊断    │ │监控     │ │日志      │ │操作      │
│Agent   │ │Agent    │ │Agent     │ │Agent     │
│        │ │         │ │          │ │          │
│Ansible │ │Prometheus│ │Loki     │ │K8s      │
│ping/ssh│ │Grafana  │ │grep     │ │kubectl  │
└────────┘ └─────────┘ └──────────┘ └──────────┘
```

### 配置多 Agent

```yaml
# openclaw/agents/team.yaml
# Agent 团队配置

team:
  name: ops-team
  description: "运维响应团队"

  orchestrator:
    name: commander
    model: qwen3-8b
    system_prompt: |
      你是运维指挥 Agent，负责：
      1. 分析用户请求，拆解为子任务
      2. 将子任务分配给最合适的专业 Agent
      3. 汇总各 Agent 的结果，形成统一回复
      4. 如果某个 Agent 的结果不够，重新分配任务
      
      可用的专业 Agent：
      - diagnostics：故障诊断（Ansible 操作、SSH 检查）
      - monitor：监控查询（Prometheus 指标、Grafana 面板）
      - logs：日志分析（Loki 查询、异常模式匹配）
      - operator：运维操作（K8s 管理、服务重启）

  agents:
    diagnostics:
      name: 诊断工程师
      model: qwen3-8b
      system_prompt: |
        你是故障诊断专家，使用 Ansible 和 SSH 进行主机级排查。
        可用工具：ansible_ping, ansible_shell, ansible_copy, ssh_exec
      
    monitor:
      name: 监控分析师
      model: qwen3-8b
      system_prompt: |
        你是监控专家，使用 Prometheus 查询指标并分析趋势。
        可用工具：promql_query, promql_range, get_alerts, get_rules
      
    logs:
      name: 日志分析师
      model: qwen3-8b
      system_prompt: |
        你是日志分析专家，使用 Loki 检索和解析日志。
        可用工具：loki_query, loki_stats, log_pattern_match
      
    operator:
      name: 操作工程师
      model: qwen3-8b
      system_prompt: |
        你是 K8s 运维操作专家。
        可用工具：kubectl_get, kubectl_describe, kubectl_logs,
                 kubectl_rollout, kubectl_scale, helm_list, helm_status
```

### Agent 协作流程示例

```
用户：「生产环境 API 服务 502 了，帮我查一下」

指挥Agent 拆解：
  ├── 子任务1 → 监控Agent: "查 API 服务的 QPS 和错误率最近 15 分钟趋势"
  ├── 子任务2 → 日志Agent: "查 API 服务 502 相关日志"
  └── 子任务3 → 诊断Agent: "查 API 服务器 CPU 和内存情况"

各Agent 返回结果：
  监控Agent: "QPS 正常，但 502 错误率从 14:30 跳到 30%"
  日志Agent: "nginx error.log 显示 upstream 10.0.1.50:8080 connect() failed"
  诊断Agent: "10.0.1.50 CPU 100%，java 进程 OOMKilled"

指挥Agent 汇总：
  "根因：后端服务 10.0.1.50 上的 Java 进程 OOMKilled，
   导致 upstream 不可达，nginx 返回 502。
   建议：1. 先切流量到备用节点 2. 重启故障服务 3. 调大 JVM 堆内存"
```

---

## 10.5 OpenClaw 实战

### 实战：一键环境巡检

```yaml
# openclaw/skills/env_health_check.yaml
# 注册为一个 Skill，可通过自然语言触发

skill:
  name: env_health_check
  triggers:
    - "环境巡检"
    - "健康检查"
    - "帮我看看所有机器"
    
  workflow:
    - step: check_connectivity
      agent: diagnostics
      action: ansible_ping
      target: all
      description: "检查所有机器连通性"
      
    - step: check_resources
      agent: monitor
      action: batch_query
      queries:
        - "100 - (avg(rate(node_cpu_seconds_total{mode='idle'}[5m])) * 100) > 80"
        - "(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100 > 80"
        - "100 - (node_filesystem_avail_bytes{fstype!='tmpfs'} / node_filesystem_size_bytes) * 100 > 80"
      description: "检查 CPU/内存/磁盘使用率"
      
    - step: check_services
      agent: operator
      action: kubectl_get_pods
      params:
        all_namespaces: true
        field_selector: "status.phase!=Running"
      description: "检查 K8s 异常 Pod"
      
    - step: check_recent_alerts
      agent: monitor
      action: get_alerts
      params:
        since: 1h
      description: "查看最近 1 小时告警"
      
    - step: generate_report
      agent: commander
      prompt: |
        汇总以下巡检结果，生成健康报告：
        连通性：{{steps.check_connectivity.output}}
        资源状态：{{steps.check_resources.output}}
        服务状态：{{steps.check_services.output}}
        告警情况：{{steps.check_recent_alerts.output}}
        
        报告格式：
        ## 环境巡检报告
        ### 概览
        健康度：X/N 台正常
        
        ### 异常项
        - [严重] xxx
        - [警告] xxx
        
        ### 建议
        1. xxx
```

**触发方式**：

```
# 方式1：任意 IM 渠道直接说
"做一次环境巡检"

# 方式2：定时任务
OpenClaw → 定时任务 → 添加
  cron: "0 */4 * * *"  # 每 4 小时
  skill: env_health_check
  通知渠道: 运维群

# 方式3：API 调用
curl -X POST https://openclaw.your-domain.com/api/skills/run \
  -H "Authorization: Bearer $OPENCLAW_API_KEY" \
  -d '{"skill": "env_health_check"}'
```

---

## 10.6 OpenClaw 与 AIOps

### 10.6.1 OpenClaw + Ansible 管理 Linux 主机

#### 集成配置

```bash
# OpenClaw 服务器上安装 Ansible
pip install ansible

# 配置主机清单
cat > /etc/ansible/hosts << 'EOF'
[web]
web-01 ansible_host=10.0.1.10
web-02 ansible_host=10.0.1.11
web-03 ansible_host=10.0.1.12

[db]
db-master ansible_host=10.0.2.10
db-slave ansible_host=10.0.2.11

[all:vars]
ansible_user=ops
ansible_ssh_private_key_file=/home/openclaw/.ssh/id_rsa
EOF
```

```yaml
# openclaw/tools/ansible_tools.yaml
tools:
  ansible_ping:
    description: "检查指定主机组的连通性"
    command: "ansible {group} -m ping"
    
  ansible_shell:
    description: "在目标主机上执行 shell 命令"
    command: 'ansible {group} -m shell -a "{command}"'
    # 安全白名单
    allowed_commands:
      - "df -h"
      - "free -m"
      - "uptime"
      - "ps aux.*"
      - "ss -tlnp"
      - "systemctl status .*"
      - "tail -n \d+ .*"
      - "cat /proc/loadavg"
    # 禁止危险操作
    blocked_patterns:
      - "rm -rf"
      - "mkfs"
      - "dd if="
      - "> /dev/"
      - "iptables"
      - "shutdown"
      - "reboot"
      - "init [0-6]"
    
  ansible_facts:
    description: "获取主机详细信息（CPU/内存/磁盘/网络）"
    command: "ansible {group} -m setup"
    
  ansible_copy:
    description: "推送文件到目标主机"
    command: "ansible {group} -m copy -a 'src={src} dest={dest}'"

  ansible_script:
    description: "在目标主机执行脚本"
    command: "ansible {group} -m script -a '{script_path}'"
```

#### 智能主机管理对话示例

```
用户：帮我看看 web 组所有机器的磁盘使用情况

Bot：
（Ansible Agent 执行 ansible web -m shell -a "df -h"）

| 机器 | 磁盘 | 总量 | 已用 | 可用 | 使用率 |
|------|------|------|------|------|--------|
| web-01 | /dev/vda1 | 40G | 15G | 25G | 37% |
| web-02 | /dev/vda1 | 40G | 32G | 8G | 80% ⚠️ |
| web-03 | /dev/vda1 | 40G | 38G | 2G | 95% 🔴 |

⚠️ web-03 磁盘使用率 95%，建议清理日志。
需要我帮你查 web-03 上哪些文件占用最大吗？

---
用户：查一下

Bot：
（Ansible Agent 执行 ansible web-03 -m shell -a "du -sh /* 2>/dev/null | sort -rh | head -10"）

| 目录 | 大小 |
|------|------|
| /var | 18G |
| /usr | 8.2G |
| /home | 5.1G |
| /opt | 3.8G |

进一步分析 /var 目录：
/var/log/nginx/access.log 占用 12G（最近 30 天未轮转）

建议：执行 logrotate 清理，需要我帮你执行吗？
```

---

### 10.6.2 OpenClaw + Kubernetes 做智能 K8s 管理平台

#### 集成配置

```yaml
# openclaw/tools/k8s_tools.yaml
tools:
  kubectl_get:
    description: "获取 K8s 资源列表"
    command: "kubectl get {resource} -n {namespace} {extra_flags}"
    
  kubectl_describe:
    description: "查看 K8s 资源详情"
    command: "kubectl describe {resource} {name} -n {namespace}"
    
  kubectl_logs:
    description: "查看 Pod 日志"
    command: "kubectl logs {pod_name} -n {namespace} --tail={lines}"
    
  kubectl_events:
    description: "查看命名空间事件"
    command: "kubectl get events -n {namespace} --sort-by='.lastTimestamp'"
    
  kubectl_top:
    description: "查看资源使用"
    command: "kubectl top {resource} -n {namespace}"
    
  kubectl_rollout:
    description: "查看/管理滚动更新"
    command: "kubectl rollout {action} deployment/{name} -n {namespace}"
    # 变更操作需要确认
    requires_confirmation: true
    
  kubectl_scale:
    description: "扩缩容"
    command: "kubectl scale deployment/{name} --replicas={count} -n {namespace}"
    requires_confirmation: true
    
  helm_list:
    description: "列出 Helm Release"
    command: "helm list -n {namespace}"
    
  helm_status:
    description: "查看 Helm Release 详情"
    command: "helm status {release} -n {namespace}"
    
  k8s_diagnose:
    description: "自动诊断 K8s 资源异常"
    # 这是一个组合命令，内部执行一组检查
    checks:
      - "kubectl get pods -n {namespace} --field-selector=status.phase!=Running"
      - "kubectl get events -n {namespace} --sort-by='.lastTimestamp' | tail -20"
      - "kubectl top pods -n {namespace}"
      - "kubectl describe nodes | grep -A5 'Conditions:'"
```

#### 智能 K8s 对话示例

```
用户：帮我看下 prod 命名空间有没有异常

Bot：
（Operator Agent 执行 kubectl get pods -n prod）

prod 命名空间概览：
| Pod | 状态 | 重启 | 运行时间 |
|-----|------|------|----------|
| api-gateway-7d4f | Running | 0 | 3d |
| api-gateway-9x2h | CrashLoopBackOff 🔴 | 128 | 5m |
| user-svc-5k3m | Running | 1 | 2d |
| order-svc-8p2n | Running | 0 | 7d |

发现异常：api-gateway-9x2h 处于 CrashLoopBackOff。
自动排查中...

（Operator Agent 自动执行）：
1. kubectl describe pod api-gateway-9x2h
   → Last State: Terminated (OOMKilled)
2. kubectl logs api-gateway-9x2h --tail=50
   → 日志显示启动时连接 Redis 超时
3. kubectl get svc redis -n prod
   → redis 服务存在，但 Endpoint 为空

📊 诊断结论：
Redis Service 的 Endpoint 为空，说明没有健康的 Redis Pod。
api-gateway 因为连不上 Redis 而启动失败。

🔧 建议：检查 Redis Pod 状态，可能需要重建。
输入"修复"让我自动处理。

---
用户：修复

Bot：
⚠️ 即将执行：
  1. kubectl delete pod redis-0 -n prod（重建 Redis Pod）
  2. 等待 Redis Ready
  3. kubectl delete pod api-gateway-9x2h（触发重新调度）
  [确认] [取消]

用户：确认

Bot：
执行中...
✅ Redis Pod 已重建 (Running)
✅ api-gateway Pod 重建后正常运行
✅ 健康检查通过

问题已解决。耗时 42 秒。
```

---

### 10.6.3 OpenClaw + Prometheus 做智能监控告警

#### 集成配置

```yaml
# openclaw/tools/prometheus_tools.yaml
tools:
  promql_query:
    description: "执行单个 PromQL 即时查询"
    endpoint: "http://prometheus:9090/api/v1/query"
    
  promql_range:
    description: "执行 PromQL 范围查询"
    endpoint: "http://prometheus:9090/api/v1/query_range"
    
  get_alerts:
    description: "获取当前活跃告警"
    endpoint: "http://alertmanager:9093/api/v2/alerts"
    params:
      active: true
      silenced: false
      inhibited: false
      
  get_rules:
    description: "获取告警规则列表"
    endpoint: "http://prometheus:9090/api/v1/rules"
    
  get_targets:
    description: "获取 Prometheus 采集目标状态"
    endpoint: "http://prometheus:9090/api/v1/targets"

  analyze_metric:
    description: "分析指标异常"
    # 组合查询：当前值 + 同比 + 环比
    actions:
      - promql_range: "{metric}[15m]"
      - promql_range: "{metric} offset 1d[15m]"  # 昨天同时段
      - promql_range: "{metric}[1h]"               # 更长趋势
```

#### 智能告警分析对话

```
用户：最近 1 小时有什么告警？帮分析一下

Bot：
（Monitor Agent 查询 Alertmanager）

活跃告警（2 条）：

🔴 严重 | 14:32 至今
  API 服务 P95 延迟 > 2s
  当前值：3.8s（阈值 2s）
  
🟡 警告 | 14:15 至今
  数据库连接数 > 80%
  当前值：92%（阈值 80%）

深入分析中...

（Monitor Agent 自动执行关联分析）：
1. API 延迟和 DB 连接数同时上涨 → 可能存在关联
2. 查 DB 慢查询：
   PromQL: rate(mysql_slow_queries[5m])
   结果：慢查询从 14:10 开始增加
3. 查 API QPS：
   PromQL: rate(http_requests_total[5m])
   结果：QPS 无明显变化，排除流量突增

📊 分析结论：
DB 慢查询激增 → 连接池拥挤 → API 等待 DB 响应 → P95 延迟上升

🔧 建议排查：
1. 检查最近的 DB 变更（索引变更？大事务？）
2. 查看是否有慢查询阻塞了其他连接
3. 必要时 Kill 慢查询 + 优化 SQL

需要我帮你查看慢查询详情吗？
```

---

### 10.6.4 OpenClaw + Loki 做智能日志平台

#### 集成配置

```yaml
# openclaw/tools/loki_tools.yaml
tools:
  loki_query:
    description: "查询 Loki 日志"
    endpoint: "http://loki:3100/loki/api/v1/query_range"
    
  loki_stats:
    description: "统计日志量（按级别/服务/时间）"
    endpoint: "http://loki:3100/loki/api/v1/query"
    
  log_pattern_match:
    description: "匹配已知错误模式"
    patterns:
      - name: OOM
        regex: "OutOfMemoryError|OOMKilled|memory limit"
      - name: connection_error
        regex: "Connection refused|connection timeout|Connection reset"
      - name: disk_full
        regex: "No space left on device|disk full"
      - name: permission_denied
        regex: "Permission denied|EACCES"
      - name: DNS_failure
        regex: "Name or service not known|no such host|NXDOMAIN"
      - name: TLS_error
        regex: "certificate expired|certificate verify failed|SSL_ERROR"

  log_correlation:
    description: "关联分析多个服务的日志"
    # 同时查多个服务的日志，按时间轴对齐
```

#### 智能日志对话示例

```
用户：帮我查一下 14:30 左右的 ERROR 日志，看看发生了什么

Bot：
（Logs Agent 查询 Loki）

14:25-14:35 ERROR 日志汇总：

| 时间 | 服务 | 错误 | 数量 |
|------|------|------|------|
| 14:28 | api-gateway | Connection refused | 12 |
| 14:29 | user-svc | Redis timeout | 8 |
| 14:30 | api-gateway | 502 Bad Gateway | 45 |
| 14:31 | order-svc | Circuit breaker open | 3 |

📊 时间线分析（自动关联）：

14:28 — api-gateway 连接 user-svc 被拒（Connection refused）
14:29 — user-svc 连接 Redis 超时
14:30 — api-gateway 开始返回 502（user-svc 不可用）
14:31 — order-svc 熔断器打开（依赖 user-svc）

🔍 根因链：
Redis 异常 → user-svc 故障 → api-gateway 502 → order-svc 熔断

💡 排查建议：重点排查 14:28 之前 Redis 发生了什么事。
需要我查 Redis 的日志吗？

---
用户：查

Bot：
（Logs Agent 查 Redis 相关日志）

14:26 Redis 日志：
[WARNING] RDB snapshotting in progress (fork child process)
[ERROR] Can't save in background: fork: Cannot allocate memory

14:27 Redis 日志：
[WARNING] maxmemory limit reached (4GB)
[NOTICE] Evicting keys using allkeys-lru policy

14:28 Redis 日志：
[ERROR] Connection limit reached (maxclients=10000)

📊 最终诊断：
1. Redis 内存达到上限 4GB
2. RDB 持久化 fork 子进程时内存不足
3. 开始驱逐 Key → CPU 100%
4. 连接数打满 → user-svc 连不上 → 雪崩

🔧 建议：
1. 紧急：重启 Redis 恢复服务
2. 后续：调大 maxmemory 至 8GB
3. 后续：关闭 RDB 改用 AOF，避免 fork 内存翻倍问题
```

---

## 整体架构总结

```
┌──────────────────────────────────────────────────────────┐
│                    聊天工具层                              │
│  Telegram  │  飞书  │  企业微信  │  钉钉  │  API          │
└──────────────────────┬───────────────────────────────────┘
                       │
┌──────────────────────┴───────────────────────────────────┐
│                OpenClaw Agent 平台                        │
│                                                          │
│   ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  │
│   │  多Agent协作  │  │  技能/工作流   │  │  知识库(RAG)  │  │
│   │  指挥+专业    │  │  巡检/故障/    │  │  文档/复盘/   │  │
│   │  Agent团队   │  │  扩缩容       │  │  脚本         │  │
│   └─────────────┘  └──────────────┘  └───────────────┘  │
└──────────────────────┬───────────────────────────────────┘
                       │
┌──────────────────────┴───────────────────────────────────┐
│                  运维工具集成层                            │
│                                                          │
│  Ansible    │  K8s     │  Prometheus │  Loki            │
│  主机管理   │  容器编排 │  监控告警   │  日志分析         │
└──────────────────────────────────────────────────────────┘
```

---

## 快速上手路线

```
第 1 步：Docker Compose 部署 OpenClaw（10 分钟）
第 2 步：接入一个 IM（飞书或企业微信，15 分钟）
第 3 步：配置一个工具（先接 Ansible，20 分钟）
第 4 步：跑通第一个场景——"帮我查所有机器状态"
第 5 步：逐步添加 K8s / Prometheus / Loki
第 6 步：定义多 Agent 团队协作
第 7 步：创建定时巡检 Skill
```
