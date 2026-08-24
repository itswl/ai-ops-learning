"""LLM 后端：本地 vLLM（OpenAI 兼容）+ 离线 Mock。

- VLLMClient：用 stdlib urllib 直连 vLLM 的 /v1/chat/completions，
  不强依赖 openai 包（装了也行，这里刻意零依赖）。
- MockLLMClient：离线/CI 用的确定性替身。它会读 system prompt 里有没有
  "弃权规则"来决定要不要 abstain——因此能真实复现"改坏 prompt → 指标下降"。
  Mock 只是替身；真实信号来自 --backend vllm。
"""
from __future__ import annotations

import json
import re
import urllib.request
import urllib.error

from models import CATEGORIES


class VLLMClient:
    def __init__(self, base_url="http://localhost:8000/v1",
                 model="Qwen/Qwen3-8B", api_key="not-needed",
                 timeout=60, json_schema=None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.json_schema = json_schema

    def generate(self, messages: list[dict]) -> str:
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.0,   # RCA 要稳定可复现
            "max_tokens": 1024,
        }
        if self.json_schema is not None:
            # vLLM 支持 OpenAI 风格 response_format=json_schema（引导解码）
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "rca_output", "schema": self.json_schema},
            }
            # 兼容写法：部分 vLLM 版本用 extra body 的 guided_json
            body["guided_json"] = self.json_schema
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=data,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode())
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"连不上 vLLM ({self.base_url})：{e}\n"
                f"请先启动：vllm serve {self.model}"
            ) from e
        return payload["choices"][0]["message"]["content"]


# ---- 离线 Mock：确定性替身，用于验证流水线本身 + 复现 prompt 回归 ----

_KEYWORDS = {
    "deploy": ["发布", "部署", "deploy", "rollout", "canary", "灰度",
               "新版本", "image", "镜像", "tag", "commit", "回滚"],
    "resource": ["oom", "cpu", "内存", "memory", "显存", "磁盘", "disk",
                 "throttl", "资源", "quota", "working set", "no space"],
    "dependency": ["依赖", "upstream", "下游", "endpoint", "redis", "kafka",
                   "数据库", "db ", "连接池", "consumer lag", "慢查询", "不可达"],
    "network": ["dns", "coredns", "conntrack", "丢包", "retransmit",
                "packet", "mtu", "网络", "跨az"],
    "config": ["配置", "config", "configmap", "maxmemory", "环境变量",
               "env ", "参数", "证书", "certificate", "cert", "过期"],
    "external": ["外部", "第三方", "provider", "上游厂商", "支付", "status page"],
}
_DEGRADED_MARKERS = ["degraded", "timeout", "超时", "无数据", "不可用", "缺失", "unavailable"]


class MockLLMClient:
    """按关键词判类别；按 system prompt 是否含弃权规则决定 abstain。"""

    def __init__(self, **_):
        pass

    def generate(self, messages: list[dict]) -> str:
        system = " ".join(m["content"] for m in messages if m["role"] == "system")
        user = " ".join(m["content"] for m in messages if m["role"] == "user")
        low = user.lower()

        abstain_rule_present = "证据不足" in system  # good prompt 有，broken 没有
        ev_ids = re.findall(r"\[([A-Za-z0-9]+)\]", user)
        # 只在"数据源状态"段里找降级标记，别把证据正文里的"超时"误判成数据源降级
        status_part = user.split("## 数据源状态", 1)[1].lower() if "## 数据源状态" in user else ""
        degraded = any(mk in status_part for mk in _DEGRADED_MARKERS)

        # 弃权判定：只有当 prompt 明确要求时才会弃权（真实模型也是这行为）
        should_abstain = abstain_rule_present and (len(ev_ids) <= 1 or degraded)

        if should_abstain:
            out = {
                "abstain": True, "root_cause_category": None, "confidence": 0.2,
                "evidence_ids": ev_ids,
                "reasoning": "可用证据不足或关键数据源不可用，不足以定论。",
                "missing_evidence": ["需要补充完整的指标/日志/trace 数据"],
            }
        else:
            scores = {cat: sum(low.count(k.lower()) for k in kws)
                      for cat, kws in _KEYWORDS.items()}
            best = max(scores, key=scores.get)
            best_score = scores[best]
            if best_score == 0:
                best = "unknown"     # 关键词全不命中：硬猜为 unknown
            out = {
                "abstain": False, "root_cause_category": best,
                "confidence": 0.5 + 0.1 * min(best_score, 4),
                "evidence_ids": ev_ids,
                "reasoning": f"证据关键词指向 {best}。",
                "missing_evidence": [],
            }
        return json.dumps(out, ensure_ascii=False)


def make_client(backend: str, **kwargs):
    if backend == "vllm":
        return VLLMClient(**kwargs)
    if backend == "mock":
        return MockLLMClient()
    raise ValueError(f"未知后端: {backend}（可选 vllm / mock）")
