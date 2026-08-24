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
//
// 注意：Coze IDE 的真实约定是「导出一个 handler 函数」，
// 输入/输出参数在 IDE 的"元数据"面板里定义（不是在代码里写 inputs/outputs 对象）。
// 下面把业务逻辑组织成类只是为了可读，入口必须是 handler。

const K8S_API = "https://your-k8s-api-server";
const DINGTALK_WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=xxx";

export async function handler({ input, logger }) {
  const agent = new SmartRestartPod();
  return await agent.run(input);
}

class SmartRestartPod {
  constructor() {
    this.k8sApi = K8S_API;
    this.dingtalkWebhook = DINGTALK_WEBHOOK;
  }

  async run(inputs) {
    const { pod_name, namespace = "prod", reason } = inputs;

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

def aliyun_request(action, params=None,
                   endpoint="https://ecs.cn-hangzhou.aliyuncs.com/",
                   version="2014-05-26"):
    """构造阿里云 OpenAPI 请求（RPC 风格 V1 签名）。
    生产建议直接用官方 SDK（alibabacloud_ecs20140526 / alibabacloud_cms20190101），
    手写签名仅用于理解原理。"""
    if params is None:
        params = {}
    
    # 公共参数
    body = {
        "Action": action,
        "Version": version,
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
    
    resp = requests.get(endpoint, params=body)
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
    """获取 ECS 监控指标（CPU/内存/磁盘/网络）
    注意：DescribeMetricData 是云监控 CMS 的接口（endpoint 是
    metrics.cn-hangzhou.aliyuncs.com，Version 2019-01-01），
    不能打到 ECS 的 endpoint 上，实例要通过 Dimensions 传，这三点都容易踩坑。
    """
    data = request.json
    instance_id = data["instance_id"]
    metric = data.get("metric", "CPUUtilization")  # CPUUtilization / memory_usedutilization 等

    result = aliyun_request(
        "DescribeMetricData",
        {
            "Namespace": "acs_ecs_dashboard",
            "MetricName": metric,
            "Dimensions": json.dumps([{"instanceId": instance_id}]),
            "Period": "60",
            "StartTime": data.get("start_time", ""),
            "EndTime": data.get("end_time", ""),
        },
        endpoint="https://metrics.cn-hangzhou.aliyuncs.com/",
        version="2019-01-01",
    )

    return jsonify({
        "metric": metric,
        # CMS 返回的 Datapoints 是 JSON 字符串
        "data": json.loads(result.get("Datapoints", "[]") or "[]"),
    })


@app.post("/api/ecs/cmd")
def run_command():
    """在 ECS 上执行命令（需要安装云助手）"""
    data = request.json
    result = aliyun_request("RunCommand", {
        "InstanceId.1": data["instance_id"],
        "Type": "RunShellScript",
        "CommandContent": base64.b64encode(data["command"].encode()).decode(),
        # 必须显式声明 Base64，RunCommand 默认按明文（PlainText）处理，
        # 漏了这个参数会把 base64 字符串当命令执行
        "ContentEncoding": "Base64",
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
      - default: llm_fallback

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

---

## 9.2 用 Coze + Ansible 做自动化运维智能体

### 整体架构

```
用户 → 企业微信/飞书 → Coze Bot → 插件(Ansible API) → Ansible → 目标主机

流程：
  用户说"重启 web 组的所有机器"
  → Coze Bot 识别意图
  → 工作流调用 Ansible 插件
  → 插件调 Ansible API 服务
  → Ansible 执行 playbook
  → 结果原路返回
```

---

### 9.2.1 准备工作

#### 9.2.1.1 准备 Ansible 环境

```bash
# 1. 在能 SSH 到目标主机的机器上安装 Ansible
# 建议用一台专门的跳板机或管理节点
yum install -y epel-release && yum install -y ansible   # CentOS
apt install -y ansible                                    # Ubuntu

# 2. 配置免密 SSH
ssh-keygen -t rsa -b 4096 -f ~/.ssh/ansible_rsa -N ""
ssh-copy-id -i ~/.ssh/ansible_rsa.pub root@10.0.1.10
ssh-copy-id -i ~/.ssh/ansible_rsa.pub root@10.0.1.11
# ... 对所有目标主机

# 3. 配置 hosts 清单
sudo mkdir -p /etc/ansible
sudo tee /etc/ansible/hosts << 'EOF'
[web]
web-01 ansible_host=10.0.1.10
web-02 ansible_host=10.0.1.11
web-03 ansible_host=10.0.1.12

[db]
db-master ansible_host=10.0.2.10
db-slave ansible_host=10.0.2.11

[all:vars]
ansible_user=root
ansible_ssh_private_key_file=/root/.ssh/ansible_rsa
EOF

# 4. 验证
ansible web -m ping
# web-01 | SUCCESS => {"changed": false, "ping": "pong"}
# web-02 | SUCCESS => {"changed": false, "ping": "pong"}
# web-03 | SUCCESS => {"changed": false, "ping": "pong"}
```

#### 9.2.1.2 编写 Ansible API 服务脚本并开启 API

```python
# ansible_api_server.py
# 用 Flask 将 Ansible 封装为 REST API，供 Coze 插件调用
# 部署：gunicorn -w 4 -b 0.0.0.0:8867 ansible_api_server:app

import subprocess
import json
import re
import os
from flask import Flask, request, jsonify

app = Flask(__name__)
ANSIBLE_BIN = "/usr/bin/ansible"
ANSIBLE_PLAYBOOK = "/usr/bin/ansible-playbook"
PLAYBOOK_DIR = "/etc/ansible/playbooks"

# 命令安全白名单
ALLOWED_MODULES = {
    "ping": {"safe": True},
    "command": {"safe": True, "blocked": ["rm -rf", "mkfs", "dd if=", "> /dev/sd", "shutdown", "reboot", "init ", "iptables -F"]},
    "shell": {"safe": True, "blocked": ["rm -rf", "mkfs", "dd if=", "> /dev/sd", "shutdown", "reboot", "init ", "iptables -F"]},
    "copy": {"safe": True},
    "script": {"safe": True},
    "service": {"safe": True, "allowed": ["start", "stop", "restart", "status", "reload"]},
    "systemd": {"safe": True},
    "yum": {"safe": True, "allowed": ["state=installed", "state=latest", "state=absent"]},
    "apt": {"safe": True, "allowed": ["state=installed", "state=latest", "state=absent"]},
    "get_url": {"safe": True},
    "uri": {"safe": True},
    "setup": {"safe": True},
    "cron": {"safe": True},
}


def check_command_safety(module: str, args: str) -> tuple:
    """检查命令安全性"""
    if module not in ALLOWED_MODULES:
        return False, f"模块 {module} 不在白名单中"

    mod_conf = ALLOWED_MODULES[module]
    if not mod_conf["safe"]:
        return False, f"模块 {module} 被禁用"

    # 检查阻塞模式
    if "blocked" in mod_conf:
        for pattern in mod_conf["blocked"]:
            if re.search(pattern, args, re.IGNORECASE):
                return False, f"参数包含被禁止的模式: {pattern}"

    # 对于 service/systemd 模块，检查 action 是否在允许列表
    if "allowed" in mod_conf:
        action = args.split()[-1] if args.split() else ""
        if not any(a in args for a in mod_conf["allowed"]):
            return False, f"操作不在允许列表中: {mod_conf['allowed']}"

    return True, "OK"


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "ansible_version": subprocess.run([ANSIBLE_BIN, "--version"], capture_output=True, text=True).stdout.splitlines()[0]})


@app.post("/api/ping")
def ping():
    """连通性检查"""
    data = request.json or {}
    group = data.get("group", "all")
    result = subprocess.run(
        [ANSIBLE_BIN, group, "-m", "ping", "-o"],
        capture_output=True, text=True, timeout=30
    )
    hosts = []
    for line in result.stdout.strip().split("\n"):
        if "SUCCESS" in line:
            host = line.split("|")[0].strip()
            hosts.append({"host": host, "status": "reachable"})
        elif "UNREACHABLE" in line:
            host = line.split("|")[0].strip()
            hosts.append({"host": host, "status": "unreachable"})
    return jsonify({"success": result.returncode == 0, "hosts": hosts, "summary": f"{len([h for h in hosts if h['status']=='reachable'])}/{len(hosts)} 可达"})


@app.post("/api/command")
def run_command():
    """在目标主机组执行命令"""
    data = request.json or {}
    group = data.get("group", "all")
    module = data.get("module", "command")
    args = data.get("args", "")
    
    # 安全检查
    safe, reason = check_command_safety(module, args)
    if not safe:
        return jsonify({"success": False, "error": reason}), 403

    result = subprocess.run(
        [ANSIBLE_BIN, group, "-m", module, "-a", args, "-o"],
        capture_output=True, text=True, timeout=60
    )

    outputs = []
    for line in result.stdout.strip().split("\n"):
        if "|" in line:
            host = line.split("|")[0].strip()
            status = "成功" if result.returncode == 0 else "失败"
            outputs.append({"host": host, "result": status, "output": line})

    return jsonify({"success": result.returncode == 0, "outputs": outputs})


@app.post("/api/facts")
def get_facts():
    """获取主机详细信息"""
    data = request.json or {}
    group = data.get("group", "all")
    result = subprocess.run(
        [ANSIBLE_BIN, group, "-m", "setup", "-o"],
        capture_output=True, text=True, timeout=30
    )
    facts = []
    for line in result.stdout.strip().split("\n"):
        if "|" in line and "SUCCESS" in line:
            host = line.split("|")[0].strip()
            json_str = line.split("|", 2)[-1].strip()
            try:
                fact = json.loads(json_str)
                facts.append({
                    "host": host,
                    "os": fact.get("ansible_distribution", "") + " " + fact.get("ansible_distribution_version", ""),
                    "cpu": fact.get("ansible_processor_vcpus", 0),
                    "memory": round(int(fact.get("ansible_memtotal_mb", 0)) / 1024, 1),
                    "disk": fact.get("ansible_devices", {}).keys(),
                    "ip": fact.get("ansible_default_ipv4", {}).get("address", ""),
                })
            except json.JSONDecodeError:
                pass
    return jsonify({"hosts": facts})


@app.post("/api/playbook")
def run_playbook():
    """执行 playbook"""
    data = request.json or {}
    playbook_name = data.get("playbook")
    group = data.get("group")
    extra_vars = data.get("extra_vars", {})

    playbook_path = os.path.join(PLAYBOOK_DIR, playbook_name)
    if not os.path.exists(playbook_path):
        return jsonify({"success": False, "error": f"Playbook {playbook_name} 不存在"}), 404

    cmd = [ANSIBLE_PLAYBOOK, playbook_path]
    if group:
        cmd.extend(["-l", group])
    for k, v in extra_vars.items():
        cmd.extend(["-e", f"{k}={v}"])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return jsonify({
        "success": result.returncode == 0,
        "stdout": result.stdout[-3000:],
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8867)
```

```bash
# 部署 API 服务
pip install flask gunicorn
gunicorn -w 4 -b 0.0.0.0:8867 ansible_api_server:app --daemon \
  --access-logfile /var/log/ansible-api-access.log \
  --error-logfile /var/log/ansible-api-error.log

# 验证
curl -X POST http://localhost:8867/api/ping \
  -H "Content-Type: application/json" \
  -d '{"group": "web"}'
```

#### 9.2.1.3 编写 Playbook

```yaml
# /etc/ansible/playbooks/restart_service.yaml
- name: 安全重启服务
  hosts: "{{ target_group | default('all') }}"
  gather_facts: no
  tasks:
    - name: 检查服务是否存在
      systemd:
        name: "{{ service_name }}"
      register: svc_check
      ignore_errors: yes

    - name: 重启服务
      systemd:
        name: "{{ service_name }}"
        state: restarted
      when: svc_check.status.ActiveState is defined
      register: restart_result

    - name: 等待服务就绪
      wait_for:
        port: "{{ health_port | default(80) }}"
        timeout: 30
      when: health_port is defined
```

```yaml
# /etc/ansible/playbooks/check_disk.yaml
- name: 磁盘巡检
  hosts: all
  gather_facts: no
  tasks:
    - name: 检查磁盘使用率
      shell: "df -h / | tail -1 | awk '{print $5}'"
      register: disk_usage

    - name: 检查 inode 使用率
      shell: "df -i / | tail -1 | awk '{print $5}'"
      register: inode_usage

    - name: 告警
      debug:
        msg: "警告：{{ inventory_hostname }} 磁盘使用率 {{ disk_usage.stdout }}"
      when: "disk_usage.stdout | replace('%', '') | int > 80"
```

```yaml
# /etc/ansible/playbooks/collect_logs.yaml
- name: 收集指定时间段日志
  hosts: "{{ target_group }}"
  gather_facts: no
  tasks:
    - name: 打包日志
      archive:
        path: "/var/log/{{ app_name }}/"
        dest: "/tmp/{{ app_name }}-logs-{{ ansible_date_time.date }}.tar.gz"
        format: gz

    - name: 拉取到管理节点
      fetch:
        src: "/tmp/{{ app_name }}-logs-{{ ansible_date_time.date }}.tar.gz"
        dest: "/tmp/collected-logs/{{ inventory_hostname }}/"
        flat: yes
```

---

### 9.2.2 创建 Coze 插件

基于 Ansible API 服务创建 5 个插件：

#### 插件 1：ansible-ping

```
Coze 插件 → 基于 API 创建
  名称：ansible-ping
  描述：检查目标主机组的 SSH 连通性
  URL：https://your-ansible-api/api/ping
  方法：POST
  参数：group(String, 非必填, 默认all)

输出：
  {"success": true, "hosts": [...], "summary": "3/3 可达"}
```

#### 插件 2：ansible-run-command

```
Coze 插件 → 基于 API 创建
  名称：ansible-run-command
  描述：在目标主机上执行命令（df、free、ps、ss 等安全命令）
  URL：https://your-ansible-api/api/command
  方法：POST
  参数：
    group(String, 必填)：目标主机组（web/db/all）
    module(String, 必填)：Ansible 模块（command/shell/systemd/service）
    args(String, 必填)：模块参数（如 "df -h"）

输出：
  {"success": true, "outputs": [{"host":"web-01", "result":"成功", "output":"..."}]}
```

#### 插件 3：ansible-facts

```
Coze 插件 → 基于 API 创建
  名称：ansible-facts
  描述：获取主机详细配置信息（OS/CPU/内存/磁盘/IP）
  URL：https://your-ansible-api/api/facts
  方法：POST
  参数：group(String, 非必填)
```

#### 插件 4：ansible-restart-service

```
Coze 插件 → 基于 API 创建
  名称：ansible-restart-service
  描述：安全重启指定服务（先检查、后重启、再验证）
  URL：https://your-ansible-api/api/playbook
  方法：POST
  参数：
    group(String, 必填)
    service_name(String, 必填)
    health_port(Integer, 非必填)
```

#### 插件 5：ansible-check-disk

```
Coze 插件 → 基于 API 创建
  名称：ansible-check-disk
  描述：磁盘巡检（检查使用率和 inode）
  URL：https://your-ansible-api/api/playbook
  方法：POST
  参数：
    playbook(String, 固定 check_disk.yaml)
    group(String, 默认 all)
```

---

### 9.2.3 创建 Coze 工作流

#### 工作流1：智能磁盘清理

```
工作流名称：smart-disk-cleanup

触发条件：用户说"磁盘满了" / "清理磁盘" / "磁盘巡检"

节点流程：
┌─────────────┐
│  开始        │
│  提取：用户   │
│  指定的主机组 │
└──────┬──────┘
       ↓
┌─────────────┐
│ 1.磁盘巡检   │
│ ansible-     │
│ check-disk   │
└──────┬──────┘
       ↓
┌─────────────┐
│ 2.LLM分析    │
│ 判断哪些机器  │
│ 磁盘>80%     │
└──────┬──────┘
       ↓
┌─────────────┐
│ 3.条件分支    │
│ >80%且>1台   │→ 走清理流程
│ 全部正常     │→ 回复"磁盘正常"
└──────┬──────┘
       ↓
┌─────────────┐
│ 4.查大文件   │
│ ansible-     │
│ run-command  │
│ du -sh /* │  │
│ sort -rh     │
└──────┬──────┘
       ↓
┌─────────────┐
│ 5.LLM分析    │
│ 识别可清理的  │
│ 日志/缓存/    │
│ 临时文件      │
└──────┬──────┘
       ↓
┌─────────────┐
│ 6.用户确认    │
│ 展示清理计划  │
│ [确认][取消]  │
└──────┬──────┘
       ↓
┌─────────────┐
│ 7.执行清理   │
│ ansible-     │
│ run-command  │
│ 安全清理命令  │
└──────┬──────┘
       ↓
┌─────────────┐
│ 8.验证       │
│ 再次查磁盘   │
└──────┬──────┘
       ↓
┌─────────────┐
│ 9.LLM 汇总   │
│ 清理前后对比  │
│ + 防止再满建议│
└──────┬──────┘
       ↓
      结束
```

#### 工作流2：服务自动重启

```
工作流名称：safe-restart-service

节点流程：
┌──────────────┐
│ 开始          │
│ 提取：服务名   │
│ 提取：目标组   │
└──────┬───────┘
       ↓
┌──────────────┐
│ 1.服务状态    │
│ ansible-run   │
│ systemctl     │
│ status xxx    │
└──────┬───────┘
       ↓
┌──────────────┐
│ 2.依赖检查    │
│ 查数据库连接数 │
│ 查上游服务状态 │
└──────┬───────┘
       ↓
┌──────────────┐
│ 3.LLM判断     │
│ 安全吗？      │
│ 依赖正常？    │
└──────┬───────┘
       ↓
     ┌─┴──────────────────┐
     ↓                     ↓
  安全                   有风险
┌──────────────┐  ┌──────────────┐
│ 4.确认重启    │  │ 4b.警告+额外  │
│ [确认][取消]  │  │ 确认         │
└──────┬───────┘  └──────┬───────┘
       ↓                 ↓
┌──────────────┐
│ 5.执行重启    │
│ ansible       │
│ restart-      │
│ service       │
└──────┬───────┘
       ↓
┌──────────────┐
│ 6.验证恢复    │
│ 等端口就绪    │
│ 查进程存活    │
└──────┬───────┘
       ↓
┌──────────────┐
│ 7.通知        │
│ 重启结果 +    │
│ 建议          │
└──────────────┘
```

---

### 9.2.4 配置 Coze 智能体

```
Bot: AIOps-Ansible 运维助手

System Prompt:
  你是运维自动化助手，通过 Ansible 管理服务器。
  
  能力：
  - 查询任意主机组连通性
  - 获取主机配置信息（CPU/内存/磁盘/OS）
  - 在主机上执行安全命令（df/free/ps/systemctl 等）
  - 安全重启服务（自动检查依赖+确认+验证）
  - 磁盘巡检与智能清理
  
  安全原则：
  - 变更操作必须用户确认
  - 仅执行白名单内的安全命令
  - 操作前评估影响范围

绑定插件：
  ├── ansible-ping
  ├── ansible-run-command
  ├── ansible-facts
  ├── ansible-restart-service
  └── ansible-check-disk

绑定工作流：
  ├── smart-disk-cleanup
  └── safe-restart-service

快捷指令：
  /ping <组名>       → 连通性检查
  /info <组名>       → 主机详情
  /disk <组名>       → 磁盘巡检
  /restart <服务名>  → 安全重启
```

---

## 9.3 用 Dify + JumpServer 做运维智能体

### 为什么 Dify + JumpServer

JumpServer 是开源堡垒机，负责**安全审计和权限控制**；Dify 负责**智能编排**。两个加起来：

```
JumpServer 提供：安全登录 + 操作审计 + 权限控制
Dify 提供：自然语言理解 + 工作流编排 + LLM 推理

组合后：
  用户说 "重启 web-01 上的 nginx"
  → Dify 解析意图 → JumpServer MCP 执行操作
  → JumpServer 审计录像
  → Dify 返回结果
```

---

### 9.3.1 部署 JumpServer

#### 9.3.1.1 部署 JumpServer

```bash
# 官方一键部署脚本（推荐）
curl -sSL https://github.com/jumpserver/jumpserver/releases/latest/download/quick_start.sh | bash

# 安装完成后访问
# Web: http://<服务器IP>:80
# 默认账号: admin
# 默认密码: admin （首次登录强制修改）

# 或者使用 Docker 部署
git clone https://github.com/jumpserver/Dockerfile.git /opt/jumpserver-docker
cd /opt/jumpserver-docker
cp config_example.conf .env
# 编辑 .env，修改密码和密钥
docker-compose up -d
```

```bash
# 关键配置项 (.env)
SECRET_KEY=$(openssl rand -hex 32)
BOOTSTRAP_TOKEN=$(openssl rand -hex 16)
DB_PASSWORD=$(openssl rand -hex 16)
REDIS_PASSWORD=$(openssl rand -hex 16)

# Web 端口
HTTP_PORT=80
SSH_PORT=2222

# 邮箱配置（告警用）
EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_HOST_USER=ops@example.com
EMAIL_HOST_PASSWORD=***
```

#### 9.3.1.2 快速体验 JumpServer

```
JumpServer 核心概念：
  ├── 资产管理：添加你要管理的 Linux/Windows/数据库/网络设备
  ├── 用户管理：谁可以登录 JumpServer
  ├── 资产授权：谁可以访问哪些资产（权限最小化）
  └── 审计录像：所有操作都有录屏回放

快速上手：
  1. 资产管理 → 创建资产 → 填 IP + SSH 端口 + 凭据
  2. 用户管理 → 创建用户 → 分配资产权限
  3. Web 终端 → 选择资产 → 直接 SSH 登录
  4. 审计台 → 查看操作录像
```

```
JumpServer 三种连接模式：

1. Web 终端（默认）：浏览器内直接 SSH，无需客户端
2. Web SFTP：浏览器内传输文件
3. 数据库连接：支持 MySQL/PostgreSQL/Redis 等 Web 终端

对于 AI 智能体，主要用 API 方式 —— 通过 JumpServer MCP 程序化调用
```

---

### 9.3.2 部署 JumpServer MCP

JumpServer MCP 是一个中间层，将 JumpServer 的资产管理能力暴露为 MCP 协议，供 Dify 调用。

#### 9.3.2.1 获取用户 Token

```bash
# 在 JumpServer 管理后台获取 API Key
# 右上角头像 → 个人信息 → API Key → 生成
# 或者用命令行获取
curl -X POST http://<jumpserver-ip>/api/v1/authentication/auth/ \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "your-password"}'

# 返回:
# {"token": "abc123...", "user": {...}}

# 记录这个 Token 用于 MCP 配置
```

#### 9.3.2.2 部署 JumpServer MCP

JumpServer 官方有 MCP Server 项目：**github.com/jumpserver/mcp**（老版本教程里流传的 `wojiushixiaobai/jumpserver-mcp` 仓库不存在，是编造的地址）。

```bash
git clone https://github.com/jumpserver/mcp.git /opt/jumpserver-mcp
cd /opt/jumpserver-mcp

# 官方支持 Docker 部署，核心配置就两项：
# JumpServer 地址 + API Token（上一步获取）
# 具体环境变量名和启动方式以仓库 README 为准
docker build -t jumpserver-mcp .
docker run -d --name jumpserver-mcp \
  -p 8870:8870 \
  -e JUMPSERVER_URL="http://<jumpserver-ip>" \
  -e JUMPSERVER_TOKEN="abc123..." \
  jumpserver-mcp
```

**MCP Server 暴露的工具（以实际版本为准，围绕资产/连接/审计三类）**：

```
典型工具（供 Dify 调用）：
├── 资产类：列资产、查资产详情
├── 连接类：通过堡垒机在资产上执行命令
└── 审计类：查询会话/操作记录
```

#### 9.3.2.3 到 Dify 上添加 JumpServer MCP

```
Dify 控制台 → 工具 → MCP → 添加 MCP Server

配置：
  名称：JumpServer
  传输协议：SSE
  URL：http://<jumpserver-mcp-ip>:8870/sse

添加后 Dify 会自动发现 MCP Server 提供的所有工具
在 Agent 应用中就可以勾选使用这些工具了
```

---

### 9.3.3 实现一个简单的需求

#### 9.3.3.1 创建 Dify 应用

```
Dify → 创建应用 → Agent 类型

第 1 步：基本信息
  应用名称：JumpServer 运维助手
  模型：Qwen3-8B（或你部署的模型）

第 2 步：添加工具
  Agent 设置 → 工具 → 添加
  勾选 JumpServer MCP 的工具：
    ☑ list_assets
    ☑ get_asset_info
    ☑ connect_asset
  
第 3 步：配置 System Prompt
```

```markdown
你是 JumpServer 运维助手，通过堡垒机安全管理所有资产。

## 你的能力
1. 查询所有资产列表和状态
2. 查看单个资产的详细信息
3. 在资产上执行命令（所有操作经过 JumpServer 审计）
4. 查看操作审计记录

## 安全规则
- 执行命令前，如果命令涉及变更操作（restart/stop/reboot/rm），必须先向用户确认
- 所有操作自动由 JumpServer 录屏审计

## 回答格式
- 资产列表 → 表格
- 执行命令 → 显示命令 + 输出 + 审计链接
```

#### 9.3.3.2 测试 Dify 应用

```
用户：帮我看看有哪些资产

Bot：（调 list_assets）

| 名称 | 类型 | 地址 | 状态 |
|------|------|------|------|
| web-01 | Linux | 10.0.1.10 | ✅ 活跃 |
| web-02 | Linux | 10.0.1.11 | ✅ 活跃 |
| db-master | Linux | 10.0.2.10 | ✅ 活跃 |
| db-slave | Linux | 10.0.2.11 | ⚠️ 离线 |

---
用户：在 web-01 上帮我查一下磁盘使用情况

Bot：
（调 connect_asset 执行 df -h）
  JumpServer 审计 ID: #20240507-001

| 文件系统 | 总量 | 已用 | 可用 | 使用率 |
|----------|------|------|------|--------|
| /dev/vda1 | 40G | 18G | 22G | 45% |

---
用户：在 web-01 上重启 nginx

Bot：
⚠️ 即将在 web-01(10.0.1.10) 上重启 nginx
此操作会短暂影响服务，是否继续？
[确认] [取消]

用户：确认

Bot：
✅ nginx 重启成功
  执行命令: systemctl restart nginx
  审计 ID: #20240507-002
  操作人: 张三
  [查看审计录像]
```

---

### 9.3.4 做一个综合性应用智能体

#### 场景：智能扩容决策 + 执行

```
用户：web 组 CPU 平均 85% 持续 30 分钟了，帮我扩容

智能体执行流程：
┌─────────────────────┐
│ 1. 查资产现状         │
│ JumpServer: list_    │
│ assets → 当前 3 台   │
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│ 2. 查资源现状         │
│ JumpServer: connect  │
│ → top/free/df        │
│ 3台 CPU 均在 85%+    │
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│ 3. 查历史容量数据     │
│ 知识库: 检索历史      │
│ 流量高峰时的容量需求   │
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│ 4. LLM 决策          │
│ 综合 CPU + 内存 +    │
│ 历史数据 → 建议      │
│ 扩容 2 台 4C8G       │
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│ 5. 用户确认           │
│ [确认扩容][详细分析]  │
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│ 6. 执行扩容           │
│ (对接云API或K8s)      │
│ 新增 2 台 → 加入LB   │
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│ 7. 验证 + 通知        │
│ 检查新增机器健康      │
│ 通知运维群 + 创建     │
│ 变更工单              │
└─────────────────────┘
```

**关键**：JumpServer 保证了每一步操作都有审计录像，Dify 保证了智能编排。这个组合对于需要合规审计的企业运维场景非常合适。

---

## 9.4 用 Dify + K8s 做运维智能体

### 架构

```
用户 → Dify Agent → K8s MCP Server → kubectl → K8s 集群
```

### 部署 K8s MCP Server

现成方案（都是真实活跃项目）：

- `containers/kubernetes-mcp-server`（Go，原 manusa 项目，功能全）
- `Flux159/mcp-server-kubernetes`（TypeScript，社区流行）

也可以像下面这样用 FastMCP 自己写一个最小版——好处是工具面完全可控（只暴露你想给 AI 的能力）：

```bash
mkdir /opt/k8s-mcp && cd /opt/k8s-mcp
pip install kubernetes "mcp[cli]"
```

```python
# k8s_mcp_server.py
from mcp.server.fastmcp import FastMCP
from kubernetes import client, config
import json

# 端口在构造时传（FastMCP 的 run() 不接受 port 参数）
mcp = FastMCP("k8s-ops", port=8871)
config.load_kube_config()  # 或 config.load_incluster_config() 集群内
v1 = client.CoreV1Api()
apps_v1 = client.AppsV1Api()

@mcp.tool()
def list_namespaces() -> list:
    """列出所有命名空间"""
    ns_list = v1.list_namespace()
    return [{"name": ns.metadata.name, "status": ns.status.phase} for ns in ns_list.items]

@mcp.tool()
def list_pods(namespace: str = "default") -> list:
    """列出指定命名空间的 Pod"""
    pods = v1.list_namespaced_pod(namespace)
    return [{
        "name": p.metadata.name,
        "status": p.status.phase,
        "node": p.spec.node_name,
        "restarts": sum(c.restart_count for c in p.status.container_statuses or []),
        "age": str(p.status.start_time),
    } for p in pods.items]

@mcp.tool()
def describe_pod(name: str, namespace: str = "default") -> dict:
    """查看 Pod 详情"""
    pod = v1.read_namespaced_pod(name, namespace)
    return {
        "name": pod.metadata.name,
        "status": pod.status.phase,
        "conditions": [{"type": c.type, "status": c.status} for c in pod.status.conditions or []],
        "containers": [{"name": c.name, "image": c.image} for c in pod.spec.containers],
        "container_statuses": [
            {"name": s.name, "ready": s.ready, "restarts": s.restart_count,
             "waiting_reason": s.state.waiting.reason if s.state.waiting else None}
            for s in pod.status.container_statuses or []
        ],
    }

@mcp.tool()
def get_pod_logs(name: str, namespace: str = "default", tail: int = 100) -> str:
    """获取 Pod 日志"""
    return v1.read_namespaced_pod_log(name, namespace, tail_lines=tail)

@mcp.tool()
def list_deployments(namespace: str = "default") -> list:
    """列出 Deployment"""
    deps = apps_v1.list_namespaced_deployment(namespace)
    return [{
        "name": d.metadata.name,
        "replicas": f"{d.status.ready_replicas or 0}/{d.spec.replicas}",
        "image": d.spec.template.spec.containers[0].image,
    } for d in deps.items]

@mcp.tool()
def scale_deployment(name: str, namespace: str, replicas: int) -> dict:
    """扩缩容 Deployment（需要确认）"""
    dep = apps_v1.read_namespaced_deployment(name, namespace)
    dep.spec.replicas = replicas
    apps_v1.patch_namespaced_deployment(name, namespace, dep)
    return {"success": True, "name": name, "new_replicas": replicas}

@mcp.tool()
def get_events(namespace: str = "default") -> list:
    """查看命名空间事件"""
    events = v1.list_namespaced_event(namespace)
    return [{"type": e.type, "reason": e.reason, "message": e.message, "time": str(e.last_timestamp)} for e in events.items]

@mcp.tool()
def get_nodes() -> list:
    """查看集群节点状态"""
    nodes = v1.list_node()
    return [{
        "name": n.metadata.name,
        "status": [c.status for c in n.status.conditions if c.type == "Ready"][0],
        "cpu": n.status.capacity.get("cpu", ""),
        "memory": n.status.capacity.get("memory", ""),
    } for n in nodes.items]

mcp.run(transport="sse")
```

```bash
# 启动
python k8s_mcp_server.py &
# Dify 1.6+ 也支持 streamable-http 传输（mcp.run(transport="streamable-http")），
# 新集成建议优先用它，SSE 是兼容老客户端的选项
```

### Dify 配置

```
Dify → 工具 → MCP → 添加
  名称：K8s-Ops
  URL：http://<k8s-mcp-ip>:8871/sse
  传输：SSE

Dify → Agent 应用 → 绑定 K8s MCP 工具
```

### 对话示例

```
用户：prod 命名空间有哪些异常的 Pod

Bot：
(Normal Pods omitted)
异常 Pod：

🔴 api-gateway-9x2h — CrashLoopBackOff (重启 128 次)
🔴 worker-cron-5h3k   — OOMKilled (重启 3 次)

需要我深入排查吗？

---
用户：查 api-gateway

Bot：（调 describe_pod + get_pod_logs）
根因：连接 Redis 超时。Redis Service Endpoint 为空。
建议：检查 Redis Pod 状态。
```

---

## 9.5 用 Dify + Prometheus + Alertmanager 做运维智能体

### 架构

```
Prometheus → 持续采集指标
Alertmanager → 触发告警 → webhook 推给 Dify
Dify Agent → 接收告警 → 自动排查 → 返回诊断结果
```

### Alertmanager Webhook 配置

```yaml
# alertmanager.yml
# 注意：Alertmanager 的 webhook payload 格式是固定的（alerts 数组等字段），
# 而 Dify 的 /v1/workflows/run 要求 {"inputs": {...}, "user": "..."} 结构，
# 两者不能直接对接——中间需要一层转换（常见做法：n8n 或一个小 Flask 适配器
# 收 Alertmanager webhook，再改写成 Dify 的请求格式转发）。
receivers:
  - name: 'dify-aiops'
    webhook_configs:
      - url: 'http://<adapter-ip>:5000/alert-to-dify'   # 指向转换层，不是直接指向 Dify
        send_resolved: true
        max_alerts: 5

# 转换层再调 Dify：
# POST http://<dify-ip>/v1/workflows/run
# Authorization: Bearer app-xxxx
# {"inputs": {"alert_info": "<整理后的告警文本>"}, "user": "alertmanager"}
```

### Dify 告警分析工作流

```
工作流名：alert-analyzer

触发：外部 API 调用（Alertmanager webhook）

节点：
┌──────────────────┐
│ 开始              │
│ 接收告警 JSON     │
│ alertname,       │
│ severity,        │
│ description,     │
│ labels           │
└────────┬─────────┘
         ↓
┌──────────────────┐
│ 1. 告警分级        │
│ P0 → 立即处理      │
│ P1 → 15分钟内      │
│ P2 → 标记观察      │
└────────┬─────────┘
         ↓
┌──────────────────┐
│ 2. 查 Prometheus  │
│ HTTP 请求节点     │
│ 查相关指标趋势    │
└────────┬─────────┘
         ↓
┌──────────────────┐
│ 3. 查知识库       │
│ 检索历史相似告警  │
│ 和解决方案        │
└────────┬─────────┘
         ↓
┌──────────────────┐
│ 4. LLM 诊断       │
│ 综合：当前告警 +  │
│ 历史数据 + 指标   │
│ 输出：根因 + 建议 │
└────────┬─────────┘
         ↓
      ┌──┴──────────┐
      ↓               ↓
  严重/紧急        普通
┌──────────┐  ┌──────────┐
│5a.立即推送 │  │5b.汇总到  │
│飞书/企微   │  │日报      │
│+ @值班人   │  │          │
└──────────┘  └──────────┘
```

### Dify Prompt 配置

```markdown
你是在线故障诊断专家。收到 Alertmanager 告警后请：

1. 分析告警严重程度和影响范围
2. 查询 Prometheus 获取相关指标趋势
3. 检索知识库找到相似历史告警
4. 给出根因分析和处理建议

告警信息：
{{alert_info}}

Prometheus 查询结果：
{{prometheus_result}}

相似历史告警：
{{knowledge_base_result}}

请按以下格式输出：
## 告警分析
- 严重程度：...
- 影响范围：...
- 可能根因：...（概率排序）

## 处理建议
1. 紧急措施：...
2. 排查步骤：...
3. 预防措施：...
```

---

## 9.6 用 n8n + Prometheus + Alertmanager 做运维智能体

### 为什么 n8n

n8n 是一个开源的工作流自动化工具，适合低代码编排。相比 Dify，它在**定时任务、条件分支、多系统串联**上更强。

```
n8n 优势：
  ├── 可视化拖拽工作流
  ├── 400+ 内置集成节点（Prometheus / Slack / Email / HTTP / DB）
  ├── 定时触发 + Webhook 触发
  ├── 条件分支/循环/错误处理
  └── 开源自部署，数据不出域
```

### 部署 n8n

```bash
# Docker 部署
docker run -d --name n8n \
  -p 5678:5678 \
  -v n8n_data:/home/node/.n8n \
  -e N8N_SECURE_COOKIE=false \
  n8nio/n8n

# 访问 http://localhost:5678
```

### 创建告警处理工作流

#### 工作流结构

```
Webhook (接收 Alertmanager 告警)
    ↓
解析告警 JSON
    ↓
条件判断（告警级别）
    ├── critical → Slack 通知 + 创建 PagerDuty 事件
    ├── warning  → 查询 Prometheus 趋势 → 判断是否升级
    └── info     → 记录到数据库
                   ↓
              汇总到日报
```

#### 节点配置

**节点 1：Webhook 触发器**

```
方式：POST
路径：/alert-webhook
响应模式：立即返回
```

**节点 2：解析 JSON**

```
类型：Function 节点
代码：
  const alert = items[0].json;
  const severity = alert.commonLabels?.severity || 'info';
  const alertname = alert.commonLabels?.alertname || 'unknown';
  return [{
    json: {
      alertname,
      severity,
      description: alert.commonAnnotations?.description,
      instance: alert.commonLabels?.instance,
      value: alert.commonAnnotations?.value,
    }
  }];
```

**节点 3：条件分支（IF 节点）**

```
条件1：severity == 'critical' → 紧急处理流程
条件2：severity == 'warning'  → 趋势分析流程
条件3：severity == 'info'     → 记录日志
```

**节点 4：Prometheus 查询（HTTP Request 节点）**

```
方法：GET
URL：http://prometheus:9090/api/v1/query
参数：query=rate(http_requests_total{job="{{$node['解析JSON'].json['alertname']}}"}[5m])
```

**节点 5：LLM 分析**

```
类型：HTTP Request（调 LLM API）
或使用 n8n 的 OpenAI 节点

Prompt：
  告警：{{$json.description}}
  Prometheus 数据：{{$node['查Prometheus'].json}}
  请分析告警原因并给出处理建议。
```

**节点 6：通知（Slack / 钉钉 / 飞书）**

```
类型：Slack 节点 或 HTTP Request
内容：
  🚨 {{severity == 'critical' ? '紧急' : ''}}告警
  告警名称：{{alertname}}
  实例：{{instance}}
  描述：{{description}}
  分析：{{$node['LLM分析'].json.analysis}}
```

### 定时巡检工作流

```
Cron 触发器（每 4 小时）
    ↓
HTTP Request: Prometheus 查询
  - CPU > 80% 的节点
  - 内存 > 85% 的节点
  - 磁盘 > 80% 的节点
    ↓
IF 判断：是否有异常
    ├── 有 → LLM 生成巡检报告 → 推送
    └── 无 → 记录"一切正常"
```

```
n8n vs Dify 选型：

n8n 更适合：
  - 定时任务密集型（巡检/日报/周报）
  - 需要条件分支和错误重试
  - 多外部系统串联（Prometheus→Jira→Slack）
  - 非 AI 核心的自动化流程

Dify 更适合：
  - AI 推理密集型（告警分析/故障诊断）
  - 需要 RAG 知识库检索
  - 自然语言驱动的交互
  - Agent 自主决策

实践中通常两者配合：
  n8n 负责触发+串联+通知 → 调 Dify API 做 AI 推理
```

---

## 9.7 用 Dify + Ansible MCP 做运维智能体

和 9.2 的 Coze+Ansible 方案类似，但用 Dify 替代 Coze，用 MCP 替代自定义 API。

### 部署 Ansible MCP Server

社区没有一个"事实标准"的 Ansible MCP（搜到的多是个人实验仓库），这类薄封装自己写反而最可控——几十行 FastMCP 就够：

```bash
mkdir /opt/ansible-mcp && cd /opt/ansible-mcp
pip install "mcp[cli]" ansible-runner
```

```python
# ansible_mcp_server.py
from mcp.server.fastmcp import FastMCP
import ansible_runner
import json

mcp = FastMCP("ansible-ops", port=8872)

INVENTORY = "/etc/ansible/hosts"

@mcp.tool()
def ping_hosts(group: str = "all") -> dict:
    """检查主机连通性"""
    r = ansible_runner.run(
        private_data_dir="/tmp/ansible",
        host_pattern=group,
        module="ping",
        inventory=INVENTORY,
        quiet=True,
    )
    return {"reachable": r.stats["ok"], "unreachable": r.stats["failures"]}

@mcp.tool()
def run_shell(group: str, command: str) -> dict:
    """在主机组执行安全 shell 命令"""
    # 安全过滤
    blocked = ["rm -rf", "mkfs", "shutdown", "reboot", "dd if=", "> /dev/sd"]
    for pattern in blocked:
        if pattern in command:
            return {"error": f"命令包含被禁止的模式: {pattern}"}
    
    r = ansible_runner.run(
        private_data_dir="/tmp/ansible",
        host_pattern=group,
        module="shell",
        module_args=command,
        inventory=INVENTORY,
        quiet=True,
    )
    results = {}
    for host, data in r.stats.get("dark", {}).items():
        results[host] = "unreachable"
    for event in r.events:
        if event["event"] == "runner_on_ok":
            host = event["event_data"]["host"]
            results[host] = event["event_data"]["res"].get("stdout", "")
    return {"results": results}

@mcp.tool()
def get_facts(group: str = "all") -> list:
    """获取主机配置信息"""
    r = ansible_runner.run(
        private_data_dir="/tmp/ansible",
        host_pattern=group,
        module="setup",
        module_args="filter=ansible_processor_vcpus,ansible_memtotal_mb,ansible_distribution",
        inventory=INVENTORY,
        quiet=True,
    )
    facts = []
    for event in r.events:
        if event["event"] == "runner_on_ok":
            f = event["event_data"]["res"].get("ansible_facts", {})
            facts.append({
                "host": event["event_data"]["host"],
                "cpu": f.get("ansible_processor_vcpus", 0),
                "memory": round(f.get("ansible_memtotal_mb", 0) / 1024, 1),
                "os": f.get("ansible_distribution", ""),
            })
    return facts

@mcp.tool()
def restart_service(group: str, service: str) -> dict:
    """安全重启服务"""
    # 1. 检查状态
    r1 = ansible_runner.run(private_data_dir="/tmp/ansible", host_pattern=group,
        module="systemd", module_args=f"name={service} state=started", inventory=INVENTORY, quiet=True)
    
    # 2. 执行重启
    r2 = ansible_runner.run(private_data_dir="/tmp/ansible", host_pattern=group,
        module="systemd", module_args=f"name={service} state=restarted", inventory=INVENTORY, quiet=True)
    
    return {"restarted": r2.stats["ok"], "failed": r2.stats["failures"]}

mcp.run(transport="sse")
```

### Dify 配置

```
Dify → 工具 → MCP → 添加
  名称：Ansible
  URL：http://<ansible-mcp-ip>:8872/sse
  传输：SSE

Agent 绑定 Ansible MCP 工具后，用户就可以用自然语言管理主机了
```

**和 Coze 方案对比**：

| | Coze + API | Dify + MCP |
|---|---|---|
| 插件开发 | 写 Flask API 服务 | 写 MCP Server（标准协议） |
| 工具复用 | 仅 Coze 可用 | 任何支持 MCP 的都能用 |
| 工作流 | 有一定局限 | 更灵活（支持代码执行） |
| 成本 | Coze 免费额度大 | Dify 完全自部署 |
| 推荐 | 快速验证 | 生产落地 |

---

## 9.8 用 n8n + Jenkins 做 DevOps + AIOps 智能体

### 场景

将 CI/CD 流水线和 AIOps 结合——让 AI 参与代码构建、测试、发布的决策和通知。

### 架构

```
GitLab Webhook（代码推送/PR 合并）
    ↓
n8n 工作流
    ├── 触发 Jenkins Pipeline
    ├── 等待构建结果
    ├── 如果失败 → LLM 分析失败日志 → 通知开发者+建议修复
    └── 如果成功 → 自动部署 → 部署后监控检查 → 通知
```

### Jenkins API 准备

```bash
# Jenkins 获取 API Token
# Jenkins → 用户 → Configure → API Token → 生成

# 测试 API
curl -X POST "http://jenkins:8080/job/deploy-api/build" \
  --user "admin:<api-token>"
```

### n8n 完整工作流

#### 节点 1：GitLab Webhook

```
类型：Webhook
事件：Push Event / Merge Request Event
过滤：仅 master/main 分支
```

#### 节点 2：执行 Fast Check（可选）

```
类型：Function
作用：在触发完整 CI/CD 前做快速检查
  - 配置文件语法检查
  - 敏感信息扫描
  - 分支保护规则检查
```

#### 节点 3：触发 Jenkins

```
类型：HTTP Request
方法：POST
URL：http://jenkins:8080/job/{job_name}/buildWithParameters
认证：Basic Auth（用户名 + API Token）
参数传递：注意！buildWithParameters 接收的是查询参数/表单参数，
         不是 JSON body（发 JSON 会被 Jenkins 忽略，构建拿到的全是默认值）
Query Parameters：
  BRANCH   = {{$json.ref}}
  COMMIT_ID = {{$json.after}}
  AUTHOR   = {{$json.user_name}}
```

#### 节点 4：轮询 Jenkins 构建状态

```
类型：Loop / Wait 节点
逻辑：每 15 秒查询 Jenkins API → 直到构建完成
API：http://jenkins:8080/job/{job_name}/{build_number}/api/json

Pseudocode：
  while True:
    result = get("jenkins/.../api/json")
    if result.building == false:
      break
    sleep(15)
```

#### 节点 5：IF 分支（构建结果）

```
条件：构建成功？
  ├── 成功 → 节点6（部署）
  └── 失败 → 节点7（失败分析）
```

#### 节点 6a：自动部署

```
类型：HTTP Request
URL：触发部署流水线或 K8s rollout
通知：#ci-cd 频道：✅ 构建成功，正在部署...
```

#### 节点 6b：部署后验证

```
类型：HTTP Request
检查项：
  - 新 Pod 是否 Running
  - 健康检查端点是否 200
  - P95 延迟是否在正常范围
  
失败 → 自动回滚 → 通知
```

#### 节点 6c：部署成功通知

```
类型：Slack/飞书/企微/钉钉
消息：
  ✅ 部署成功
  项目：api-service
  分支：main
  提交：abc123 (张三: 修复登录bug)
  部署耗时：3m42s
  新版本：v2.3.1
  [查看变更] [回滚]
```

#### 节点 7a：构建失败分析

```
类型：HTTP Request（调 LLM API）

调用 LLM 分析构建日志：
  "以下是 Jenkins 构建失败的日志，请分析失败原因并给出修复建议：
  {{$node['获取日志'].json.console_output}}"
```

#### 节点 7b：构建失败通知

```
类型：Slack/飞书/企微/钉钉 + @提交者
消息：
  ❌ 构建失败
  项目：api-service
  提交者：@张三
  提交：abc123
  失败原因（AI 分析）：缺少依赖包 requests>=2.28.0
  修复建议：在 requirements.txt 中添加该依赖
  [查看日志] [重新构建]
```

### 完整 n8n 工作流 JSON（概念结构）

```json
{
  "nodes": [
    {"name": "GitLab Webhook", "type": "webhook"},
    {"name": "Fast Check", "type": "function"},
    {"name": "Trigger Jenkins", "type": "http-request"},
    {"name": "Poll Build Status", "type": "wait"},
    {"name": "Build Success?", "type": "if"},
    {"name": "Deploy", "type": "http-request"},
    {"name": "Post-Deploy Verify", "type": "http-request"},
    {"name": "Deploy Success Notify", "type": "slack"},
    {"name": "Fetch Build Logs", "type": "http-request"},
    {"name": "AI Analyze Failure", "type": "http-request"},
    {"name": "Build Fail Notify", "type": "slack"}
  ]
}
```

### 扩展：AIOps + DevOps 能力矩阵

```
阶段          传统 DevOps          AIOps + DevOps
────────────────────────────────────────────────
代码提交      lint + unit test    AI 代码审查
构建          Jenkins             Jenkins + AI 日志分析
测试          自动化测试           AI 判断失败是否 flaky（可忽略）
部署          人工审批             AI 评估风险 → 自动审批低风险部署
发布          灰度发布             AI 分析灰度指标 → 自动决策全量或回滚
监控          告警通知             AI 诊断 + 自动修复建议
事后分析      人工写复盘           AI 自动生成故障复盘草稿
```

