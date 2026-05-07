# 基于 Coze 搭建运维智能体（AIOps Agent）

> 创建时间: 2026-05-07

---

## 整体架构预览

```
┌──────────────────────────────────────────────────────────┐
│                    Coze 运维智能体                         │
│                                                          │
│  用户通过 企业微信/飞书/Web 「自然语言」提问               │
│       ↓                                                  │
│  ┌─────────────────────────────────────────────────┐    │
│  │            Coze 工作流（编排层）                   │    │
│  │                                                   │    │
│  │  意图识别 → 检索知识库 → 调插件 → 汇总 → 回复       │    │
│  └─────────────────────────────────────────────────┘    │
│       ↓              ↓              ↓                   │
│  ┌─────────┐  ┌──────────┐  ┌──────────────┐           │
│  │ 知识库   │  │ 自定义插件 │  │  内置能力      │           │
│  │ 运维文档 │  │ 阿里云 ECS │  │  代码执行      │           │
│  │ 故障复盘 │  │ Prometheus│  │  网页抓取      │           │
│  │ 脚本库   │  │ 钉钉通知   │  │  图片理解     │           │
│  └─────────┘  └──────────┘  └──────────────┘           │
└──────────────────────────────────────────────────────────┘
```

---

## 9.1 Coze 自定义插件

### 什么是 Coze 插件

Coze 插件就是让 Bot 能调外部 API 的「连接器」。定义好 API 的 URL、参数、认证方式后，Bot 就能在对话中自动调用。

**运维类比**：插件 = 给 Bot 接上运维系统的「线缆」——接了阿里云 API 就能管机器，接了 Prometheus 就能查指标，接了钉钉就能发通知。

### 插件创建方式

Coze 支持三种方式：

| 方式 | 适用 | 特点 |
|---|---|---|
| **基于 API 创建** | 对接已有 API | 零代码，填 URL 和参数即可 |
| **基于 IDE 创建** | 复杂逻辑、需要聚合多个 API | 写 Node.js/Python 代码 |
| **插件市场安装** | 常见平台已有插件 | 直接用，改配置 |

---

### 9.1.1.1 基于 API 创建插件

适用于对接已有 HTTP API（Prometheus、阿里云、钉钉 webhook 等）。

#### 操作步骤

```
Coze 控制台 → 插件 → 创建插件 → 基于 API

第 1 步：基本信息
  插件名称：ops-aliyun-ecs
  插件描述：管理阿里云 ECS 实例（查询状态、启动、停止）
  插件图标：选一个云服务图标

第 2 步：添加 API 接口
  接口名称：DescribeInstances（查询 ECS 状态）
  请求方式：POST
  URL：https://ecs.cn-hangzhou.aliyuncs.com/
  
  认证方式：选择「阿里云 AK/SK 签名」
  （或者选择 API Key + 自定义签名逻辑）

第 3 步：定义参数
  ├── Action: String（固定值 DescribeInstances）
  ├── RegionId: String（如 cn-hangzhou）
  ├── InstanceIds: String（JSON 数组，可选）
  └── Status: String（Running/Stopped，可选）

第 4 步：测试
  填入真实参数 → 点击「测试」→ 检查返回结果

第 5 步：发布
```

#### 阿里云 API 签名处理

阿里云 API 需要特殊的签名算法，可以在 Coze 插件中选「自定义签名」或通过一个中间层 API 来代理：

```python
# 方案：用一个轻量 Flask 服务做阿里云 API 代理
# Coze 插件 → 调这个代理 → 代理用 AK/SK 签名 → 调阿里云 API

from flask import Flask, request, jsonify
from alibabacloud_ecs20140526.client import Client as EcsClient
from alibabacloud_tea_openapi import models as open_api_models

app = Flask(__name__)

# Coze 插件只需要调这个简单接口
@app.post("/api/ecs/describe")
def describe_instances():
    data = request.json
    client = EcsClient(open_api_models.Config(
        access_key_id="YOUR_AK",
        access_key_secret="YOUR_SK",
        region_id=data.get("region_id", "cn-hangzhou"),
    ))
    resp = client.describe_instances(
        region_id=data.get("region_id"),
        instance_ids=data.get("instance_ids"),
        status=data.get("status"),
    )
    instances = [{
        "id": i.instance_id,
        "name": i.instance_name,
        "status": i.status,
        "ip": i.public_ip_address[0] if i.public_ip_address else "无",
        "cpu": i.cpu,
        "memory": f"{i.memory / 1024} GB",
    } for i in resp.body.instances.instance]
    return jsonify(instances)

# Coze 插件配置：
# URL: https://your-proxy/api/ecs/describe
# 方法: POST
# 参数: region_id(String), instance_ids(String), status(String)
```

---

### 9.1.1.2 基于 IDE 创建自定义插件

当需要聚合多个 API、做复杂数据转换、或有特定业务逻辑时，用 IDE 方式写代码。

#### 场景：智能重启插件

逻辑——先查 Pod 状态（K8s API），再判断是否需要重启，最后执行重启并推送结果到钉钉：

```javascript
// Coze IDE 插件（Node.js）
// 功能：智能重启 K8s Pod 并通知钉钉

class SmartRestartPod {
  constructor() {
    this.k8sApi = "https://your-k8s-api-server";
    this.dingtalkWebhook = "https://oapi.dingtalk.com/robot/send";
  }

  inputs = {
    pod_name: {
      type: "string",
      description: "Pod 名称",
      required: true,
    },
    namespace: {
      type: "string",
      description: "命名空间",
      required: false,
      default: "prod",
    },
    reason: {
      type: "string",
      description: "重启原因（会记录到通知中）",
      required: true,
    },
  };

  outputs = {
    result: { type: "object" },
  };

  async run(inputs) {
    const { pod_name, namespace, reason } = inputs;

    // 1. 查询 Pod 状态
    const podStatus = await this.getPodStatus(pod_name, namespace);
    
    // 2. 判断是否允许重启（检查 Pod 是否有重要业务标签）
    if (podStatus.labels?.critical === "true") {
      return {
        result: {
          success: false,
          message: `Pod ${pod_name} 标记为关键业务，请人工确认后手动重启`,
          pod_status: podStatus,
        }
      };
    }

    // 3. 执行重启
    await this.restartPod(pod_name, namespace);

    // 4. 发送钉钉通知
    await this.notifyDingTalk(pod_name, namespace, reason, podStatus);

    // 5. 等待 Pod Ready
    await this.waitForReady(pod_name, namespace, 60);

    return {
      result: {
        success: true,
        message: `Pod ${pod_name} 重启完成`,
        previous_status: podStatus.status,
      }
    };
  }

  async getPodStatus(name, ns) {
    const resp = await fetch(
      `${this.k8sApi}/api/v1/namespaces/${ns}/pods/${name}`,
      { headers: { Authorization: `Bearer ${process.env.K8S_TOKEN}` } }
    );
    return resp.json();
  }

  async restartPod(name, ns) {
    // kubectl rollout restart 对应的 API：删除 pod 让它自动重建
    const resp = await fetch(
      `${this.k8sApi}/api/v1/namespaces/${ns}/pods/${name}`,
      { method: "DELETE", headers: { Authorization: `Bearer ${process.env.K8S_TOKEN}` } }
    );
    return resp.json();
  }

  async notifyDingTalk(name, ns, reason, status) {
    await fetch(this.dingtalkWebhook, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        msgtype: "markdown",
        markdown: {
          title: "Pod 重启通知",
          text: `### Pod 重启通知\n> 操作者：Coze AIOps Agent\n> Pod：${ns}/${name}\n> 原因：${reason}\n> 重启前状态：${status.status}\n> 时间：${new Date().toISOString()}`,
        },
      }),
    });
  }

  async waitForReady(name, ns, timeout) {
    const start = Date.now();
    while (Date.now() - start < timeout * 1000) {
      const { status } = await this.getPodStatus(name, ns);
      if (status.phase === "Running" && status.conditions.every(c => c.status === "True")) {
        return true;
      }
      await new Promise(r => setTimeout(r, 3000));
    }
    throw new Error(`Pod ${name} 在 ${timeout}s 内未就绪`);
  }
}

module.exports = SmartRestartPod;
```

---

## 9.2 自定义 Coze 插件管理阿里云机器

### 9.1.2.1 准备工作

#### 阿里云侧

```bash
# 1. 创建 RAM 子账号，授权 ECS 操作权限
# 阿里云控制台 → RAM → 创建用户 → 编程访问
# 策略：AliyunECSFullAccess（或自定义只读+重启权限）

# 2. 记录 AccessKey 信息
export ALIBABA_CLOUD_ACCESS_KEY_ID="LTAI5xxxxxxxxx"
export ALIBABA_CLOUD_ACCESS_KEY_SECRET="xxxxxxxxxxxxxxxxxxxxxxxx"

# 3. 确认 ECS API 可访问
# 需要确保 Coze 插件的代理服务能访问公网，或部署在阿里云内网
```

#### 代理服务部署

由于 Coze 插件不能直接处理阿里云的复杂签名，需要一个代理服务：

```python
# aliyun_ops_proxy.py
# 部署在阿里云 ECS 或有公网 IP 的机器上，用 gunicorn 跑

import json
import hmac
import hashlib
import base64
import urllib.parse
from datetime import datetime, timezone
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

AK_ID = "LTAI5xxxxxxxxx"
AK_SECRET = "xxxxxxxxxxxxxxxxxxxxxxxx"
REGION = "cn-hangzhou"

def aliyun_request(action, params=None):
    """构造阿里云 OpenAPI 请求（带签名）"""
    if params is None:
        params = {}
    
    # 公共参数
    body = {
        "Action": action,
        "Version": "2014-05-26",
        "RegionId": REGION,
        "Format": "JSON",
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "SignatureMethod": "HMAC-SHA1",
        "SignatureVersion": "1.0",
        "SignatureNonce": str(int(datetime.now().timestamp() * 1000)),
        "AccessKeyId": AK_ID,
        **params,
    }
    
    # 签名
    sorted_params = sorted(body.items())
    query_string = urllib.parse.urlencode(sorted_params, quote_via=urllib.parse.quote)
    string_to_sign = f"GET&{urllib.parse.quote('/', safe='')}&{urllib.parse.quote(query_string, safe='')}"
    signature = base64.b64encode(
        hmac.new(f"{AK_SECRET}&".encode(), string_to_sign.encode(), hashlib.sha1).digest()
    ).decode()
    body["Signature"] = signature
    
    resp = requests.get("https://ecs.cn-hangzhou.aliyuncs.com/", params=body)
    return resp.json()


# ---- API 接口（供 Coze 插件调用）----

@app.post("/api/ecs/list")
def list_instances():
    """列出 ECS 实例"""
    data = request.json or {}
    status = data.get("status", "")  # Running / Stopped / 空=全部
    
    result = aliyun_request("DescribeInstances", {
        "PageSize": "100",
        **({"Status": status} if status else {}),
    })
    
    instances = []
    for i in result.get("Instances", {}).get("Instance", []):
        instances.append({
            "id": i["InstanceId"],
            "name": i["InstanceName"],
            "status": i["Status"],
            "ip": next((ip for ip in i.get("PublicIpAddress", {}).get("IpAddress", [])), "无"),
            "cpu": i["Cpu"],
            "memory": i["Memory"] // 1024,
            "os": i["OSName"],
        })
    
    return jsonify({"count": len(instances), "instances": instances})


@app.post("/api/ecs/start")
def start_instance():
    """启动 ECS"""
    data = request.json
    result = aliyun_request("StartInstance", {"InstanceId": data["instance_id"]})
    return jsonify({
        "success": result.get("Code") == "200" or "RequestId" in result,
        "request_id": result.get("RequestId", ""),
    })


@app.post("/api/ecs/stop")
def stop_instance():
    """停止 ECS"""
    data = request.json
    force = data.get("force", False)
    result = aliyun_request("StopInstance", {
        "InstanceId": data["instance_id"],
        "ForceStop": "true" if force else "false",
    })
    return jsonify({
        "success": result.get("Code") == "200" or "RequestId" in result,
        "request_id": result.get("RequestId", ""),
    })


@app.post("/api/ecs/reboot")
def reboot_instance():
    """重启 ECS"""
    data = request.json
    result = aliyun_request("RebootInstance", {"InstanceId": data["instance_id"]})
    return jsonify({
        "success": result.get("Code") == "200" or "RequestId" in result,
        "request_id": result.get("RequestId", ""),
    })


@app.post("/api/ecs/monitor")
def get_monitor():
    """获取 ECS 监控指标（CPU/内存/磁盘/网络）"""
    data = request.json
    instance_id = data["instance_id"]
    metric = data.get("metric", "CPUUtilization")  # CPUUtilization / MemoryUtilization / DiskReadBPS
    
    result = aliyun_request("DescribeMetricData", {
        "Namespace": "acs_ecs_dashboard",
        "MetricName": metric,
        "InstanceId": instance_id,
        "Period": "60",
        "StartTime": data.get("start_time", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
        "EndTime": data.get("end_time", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
    })
    
    return jsonify({
        "metric": metric,
        "data": result.get("Datapoints", {}).get("Datapoint", []),
    })


@app.post("/api/ecs/cmd")
def run_command():
    """在 ECS 上执行命令（需要安装云助手）"""
    data = request.json
    result = aliyun_request("RunCommand", {
        "InstanceId.1": data["instance_id"],
        "Type": "RunShellScript",
        "CommandContent": base64.b64encode(data["command"].encode()).decode(),
        "Timeout": str(data.get("timeout", 60)),
    })
    return jsonify({
        "success": "RequestId" in result,
        "invoke_id": result.get("InvokeId", ""),
        "request_id": result.get("RequestId", ""),
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8866)
```

```bash
# 部署代理服务
pip install flask requests gunicorn
gunicorn -w 4 -b 0.0.0.0:8866 aliyun_ops_proxy:app --daemon
```

---

### 9.1.2.2 创建 Coze 插件

代理服务跑起来后，在 Coze 中逐一创建插件：

#### 插件 1：列出 ECS 实例

```
Coze 控制台 → 插件 → 创建 → 基于 API

基本信息：
  名称：list-ecs-instances
  描述：查询阿里云 ECS 实例列表，可按状态过滤
  URL：https://your-domain/api/ecs/list
  方法：POST

输入参数：
  status (String, 非必填): Running / Stopped，不填返回全部

输出示例：
  {"count": 5, "instances": [{"id": "i-xxx", "name": "web-01", "status": "Running", "ip": "47.xx", "cpu": 2, "memory": 4}]}
```

#### 插件 2：重启 ECS

```
名称：reboot-ecs-instance
描述：重启指定的阿里云 ECS 实例
URL：https://your-domain/api/ecs/reboot
方法：POST

输入参数：
  instance_id (String, 必填): 实例 ID，如 i-bp1xxx

输出示例：
  {"success": true, "request_id": "xxx"}
```

#### 插件 3：启动 / 停止 ECS

```
名称：start-ecs-instance / stop-ecs-instance
类似上面的重启插件，只改 URL 路径和参数
```

#### 插件 4：获取 ECS 监控

```
名称：get-ecs-monitor
描述：获取 ECS 实例的 CPU/内存/磁盘监控数据
URL：https://your-domain/api/ecs/monitor
方法：POST

输入参数：
  instance_id (String, 必填)
  metric (String): CPUUtilization / MemoryUtilization / DiskReadBPS
```

---

## 9.3 设计 Coze 工作流

工作流是编排层——定义「收到用户请求后，走什么流程、调哪些插件」。

### 工作流：智能故障排查

```
用户输入: "web-01 的 CPU 飙到 100% 了，帮我看看"
    │
    ├── 节点1: 意图识别（LLM）
    │     识别出：意图=故障排查, 目标=web-01, 指标=CPU
    │
    ├── 节点2: 查监控（调 get-ecs-monitor 插件）
    │     参数: instance_id=web-01, metric=CPUUtilization
    │     输出: CPU 曲线数据
    │
    ├── 节点3: 查进程（调 run-command 插件）
    │     参数: instance_id=web-01, command="top -bn1 | head -20"
    │     输出: 进程列表
    │
    ├── 节点4: LLM 分析
    │     Prompt: "根据以下监控数据和进程信息，分析 CPU 飙高原因"
    │     输入: 监控数据 + top 输出 + 知识库（历史故障）
    │
    ├── 节点5: 判断是否需要人工确认
    │     如果是"重启服务"等变更操作 → 发确认卡片
    │     如果只是"查看信息" → 直接返回
    │
    └── 节点6: 回复 + 通知
          回复用户分析结果 → 同时推送到运维群
```

### Coze 工作流配置步骤

```
第 1 步：创建工作流
  Coze → 工作流 → 新建
  名称：ops-incident-handler
  描述：运维故障排查工作流

第 2 步：添加「开始」节点
  输入变量：
    user_input: String（用户的问题）
    instance_id: String（可选，用户指定的机器）

第 3 步：添加「意图识别」节点（LLM 节点）
  Prompt: "判断用户意图，输出 JSON：
  {
    \"intent\": \"query_status | check_monitor | restart_service | unknown\",
    \"instance_name\": \"机器名或IP\",
    \"metric\": \"CPU | memory | disk | network\"
  }"

第 4 步：添加「条件分支」节点
  根据 intent 走不同路线：
    query_status → 调 list-ecs-instances
    check_monitor → 调 get-ecs-monitor
    restart_service → 先查状态 → 确认 → 重启

第 5 步：添加「插件调用」节点
  选择对应插件，绑定输入参数
  用 {{节点名.output.字段名}} 引用上游输出

第 6 步：添加「LLM 汇总」节点
  Prompt: "你是运维专家，根据以下信息回答用户：
  监控数据：{{monitor_node.output}}
  用户问题：{{start.user_input}}
  
  要求：简洁、给排查建议、标注置信度"

第 7 步：添加「结束」节点
  输出：answer（回复文本），need_confirm（是否需要确认）

第 8 步：测试
  点「试运行」→ 输入测试问题 → 看每个节点输出
```

### 工作流 JSON 配置示例

```yaml
# Coze 工作流概念结构
workflow: ops-incident-handler
nodes:
  - id: start
    type: start
    outputs: [user_input, instance_name]

  - id: intent_parser
    type: llm
    prompt: "识别用户运维意图..."
    inputs: {text: "{{start.user_input}}"}
    outputs: {intent: "...", action: "..."}

  - id: router
    type: condition
    conditions:
      - if: "{{intent_parser.intent == 'check_monitor'}}"
        goto: fetch_monitor
      - if: "{{intent_parser.intent == 'query_status'}}"
        goto: list_ecs
      - default: goto: llm_fallback

  - id: fetch_monitor
    type: plugin
    plugin: get-ecs-monitor
    inputs:
      instance_id: "{{start.instance_name}}"
      metric: "{{intent_parser.metric}}"

  - id: analyze
    type: llm
    prompt: "分析故障根因..."
    inputs:
      monitor_data: "{{fetch_monitor.output}}"
      user_context: "{{start.user_input}}"

  - id: end
    type: end
    output: "{{analyze.output}}"
```

---

## 9.4 设计 AIOps 智能体

### 智能体整体规划

一个可用的 AIOps Agent 需要以下能力：

```
AIOps Agent 能力清单：
├── 查：ECS 状态、K8s Pod、Prometheus 指标、ELK 日志
├── 控：重启服务、扩缩容、流量切换
├── 通：企业微信/钉钉/飞书通知、创建工单
├── 知：故障知识库（RAG）、运维文档检索
└── 判：故障诊断、根因分析、风险评级
```

### Coze 智能体配置

```
第 1 步：创建智能体
  Coze → 创建 Bot → 空白模板
  Bot 名称：AIOps 运维助手
  Bot 描述：智能运维助手，支持查询机器状态、排查故障、
            执行运维操作、查看监控指标

第 2 步：配置人设与 Prompt
```

#### 核心 System Prompt

```markdown
# 角色
你是专业的运维智能助手（AIOps Agent），负责帮助运维工程师管理云资源、
排查故障、执行运维操作。

# 核心能力
1. 查询阿里云 ECS 实例状态（运行中/已停止/异常）
2. 查看实例 CPU/内存/磁盘监控指标
3. 重启/启动/停止 ECS 实例
4. 在实例上执行命令并返回结果
5. 排查常见故障（高 CPU、高内存、磁盘满、网络不通）

# 安全原则
1. 涉及重启、停止等变更操作，必须先向用户确认
2. 执行命令前评估风险，高风险命令需用户二次确认
3. 所有操作记录操作人、时间、原因

# 知识库使用
1. 优先检索知识库中的历史故障记录
2. 如果找到相似故障，引用历史解决方案
3. 如果知识库无相关内容，基于专业知识给出建议并标注不确定性

# 回答格式
- 简洁、专业、可操作
- 状态查询 → 表格
- 故障诊断 → 可能性排序 + 排查步骤
- 变更操作 → 操作前确认 + 操作后验证
```

```
第 3 步：关联工作流
  Bot 设置 → 工作流 → 关联 ops-incident-handler
  当用户意图匹配时自动触发工作流

第 4 步：配置知识库
  上传运维文档、故障复盘到 Coze 知识库
  （或对接外部 RAG 系统的 API）

第 5 步：绑定插件
  将 9.2 中创建的所有插件绑定到 Bot：
    ├── list-ecs-instances
    ├── start-ecs-instance
    ├── stop-ecs-instance
    ├── reboot-ecs-instance
    ├── get-ecs-monitor
    └── run-command (ECS 云助手)

第 6 步：设置快捷指令
  为常见操作设置快捷指令：
    /status <实例名>     → 查询实例状态
    /monitor <实例名>    → 查看监控指标
    /restart <实例名>    → 重启实例（需确认）
    /health              → 全量健康检查

第 7 步：发布到渠道
  ├── Web 页面（Coze 默认提供）
  ├── 企业微信机器人
  ├── 飞书机器人
  ├── 钉钉机器人
  └── API 端点（供其他系统调用）
```

### 智能体对话示例

```
用户: /status web-01

Bot:
查询中...

| 属性 | 值 |
|------|-----|
| 实例 ID | i-bp1xxx |
| 名称 | web-01 |
| 状态 | ✅ Running |
| 公网 IP | 47.96.xx.xx |
| 规格 | 4C 8G |
| 运行时长 | 128 天 |

---
用户: web-01 的 CPU 最近 1 小时飙高，帮我看看

Bot:
好的，我来排查 web-01 的 CPU 问题：

1. 查看监控数据...
   [CPU 曲线：从 14:30 开始从 20% 飙升到 98%]

2. 查看 top 进程...
   [java 进程占用 85% CPU，已运行 3 天]

3. 检查知识库中的相似故障...
   找到 2024-03-15 的故障复盘：同样是 java 进程 CPU 飙高，
   根因是 GC 频繁触发（堆内存配置过小）

📊 诊断结果：
  - 可能性1 (高)：Java GC 频繁触发 → 建议调大堆内存
  - 可能性2 (中)：代码死循环 → 建议查看线程 dump
  - 可能性3 (低)：外部流量突增 → 已对比，流量正常

🔧 建议操作：
  1. 先执行 jstack 获取线程 dump 确认
  2. 临时调大 -Xmx 参数并重启

需要我帮你执行 jstack 吗？

---
用户: 帮我执行

Bot:
⚠️ 即将在 web-01(i-bp1xxx) 上执行 jstack，是否确认？
  [确认] [取消]

用户: 确认

Bot:
执行结果：
[线程 dump 摘要]
发现 12 个线程处于 BLOCKED 状态，集中在 com.example.cache.CacheManager.get()

已创建工单 #1275，建议转开发排查缓存逻辑。
同时已将 Xmx 从 2G 调至 4G 作为临时缓解。
```

---

## 完整部署清单

```
前置准备：
□ 阿里云 RAM 子账号 + AK/SK
□ 一台能访问公网的机器部署代理服务（可用最小规格 ECS）
□ Coze 账号（免费额度够个人/小团队用）

代理服务部署：
□ 部署 aliyun_ops_proxy.py（gunicorn + Flask）
□ 配置安全组（Coze 能访问代理服务的 8866 端口）
□ 配置 HTTPS（Coze 要求插件 URL 使用 HTTPS，可用 Nginx 反代 + Let's Encrypt）

Coze 插件创建：
□ list-ecs-instances（查询实例）
□ get-ecs-monitor（查监控）
□ reboot-ecs-instance（重启）
□ start/stop-ecs-instance（启停）
□ run-command（执行命令）

Coze 工作流：
□ ops-incident-handler（故障排查主流程）
□ ops-health-check（定时健康检查）

Coze 智能体：
□ 配置 System Prompt
□ 绑定插件 + 工作流
□ 上传知识库文档
□ 设置快捷指令
□ 发布到 IM 渠道
```

---

## 安全注意事项

```
1. 最小权限原则
   RAM 子账号只授予 ECS 的 DescribeInstances + RebootInstance 
   + StartInstance + StopInstance 权限，不要给 FullAccess

2. 变更审批
   重启/停止操作 Coze 侧先做确认卡片
   代理服务加一层审批逻辑（如工单号校验）

3. 命令白名单
   run-command 插件只允许预定义的安全命令列表：
   ["top", "df", "free", "netstat", "jstack", "systemctl status *"]
   禁止 rm、mkfs、iptables 等危险命令

4. 审计日志
   代理服务记录每次 API 调用：
   - 谁（Coze Bot 绑定的用户）
   - 做了什么操作
   - 时间
   - 结果
   写入日志文件或数据库

5. HTTPS
   代理服务必须配置 HTTPS，避免 AK/SK 在传输中被截获
```
