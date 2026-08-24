"""端到端 demo：RCA 判断 → 修复决策 → L1 执行（带全部安全边界）。

串起两个交付物，演示"诊断到处置"的完整链路和决策门：
  - RCA 弃权 → 不处置，升级人工
  - RCA 判为非 L1-可修复类别（如 deploy 应回滚而非重启）→ 只建议
  - RCA 判为可用重启缓解 + 置信度达标 → 触发 L1 重启（五层防御）

用法：
  python demo_e2e.py --backend mock
  python demo_e2e.py --backend vllm
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "remediation"))

from models import IncidentContext, RCAOutput  # noqa: E402
from llm_client import make_client  # noqa: E402
from rca_agent import RCAAgent  # noqa: E402
from k8s_client import FakeK8sClient, Pod  # noqa: E402
from audit import Audit  # noqa: E402
from actions import RestartPodAction  # noqa: E402

# 决策策略：只有这些"重启能缓解"的类别 + 置信度达标，才允许触发 L1 重启。
# deploy 类应走回滚、config 类应改配置——都不在此列，只出建议（体现风险分级）。
RESTART_SUITABLE = {"resource", "dependency"}
CONFIDENCE_GATE = 0.6

# 演示用的两个事件：一个资源类（会触发重启），一个发布类（只建议）
DEMO_INCIDENTS = [
    {
        "incident_id": "inc-resource",
        "summary": "web pod 内存接近上限、偶发不健康",
        "evidence": [
            {"id": "E1", "text": "内存 working set 接近 limit"},
            {"id": "E2", "text": "pod readiness 偶发失败但未 OOMKilled"},
        ],
        "data_source_status": {"prometheus": "ok"},
        "target": {"namespace": "prod", "pod": "web-1", "owner": "Deployment"},
    },
    {
        "incident_id": "inc-deploy",
        "summary": "api-gateway 发布新版本后 5xx 飙升",
        "evidence": [
            {"id": "E1", "text": "14:28 发布 新版本 v2.3.1"},
            {"id": "E2", "text": "错误仅出现在新版本 pod"},
        ],
        "data_source_status": {"prometheus": "ok", "loki": "ok"},
        "target": {"namespace": "prod", "pod": "api-gw-1", "owner": "Deployment"},
    },
]


def decide_and_remediate(rca: RCAOutput, target, incident_id, action) -> str:
    if rca.abstain:
        return "→ RCA 弃权（证据不足），不自动处置，升级人工。"
    cat = rca.root_cause_category
    if cat not in RESTART_SUITABLE:
        return (f"→ 根因类别 {cat}，重启不是对症处置（如 deploy 应回滚）。"
                f"只给建议，不自动执行。")
    if rca.confidence < CONFIDENCE_GATE:
        return f"→ 类别 {cat} 但置信度 {rca.confidence:.2f} < {CONFIDENCE_GATE}，不自动执行。"
    r = action.run(namespace=target["namespace"], pod_name=target["pod"],
                   incident_id=incident_id, evidence_snapshot=rca.evidence_ids,
                   confidence=rca.confidence)
    return f"→ 触发 L1 重启：{r.status} — {r.detail}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="mock",
                    choices=["mock", "vllm", "ollama", "openai"])
    ap.add_argument("--model", default="Qwen/Qwen3-8B")
    ap.add_argument("--base-url", default="http://localhost:8000/v1")
    args = ap.parse_args()

    root = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(root, "prompts", "rca_v1_good.txt"), encoding="utf-8") as f:
        system_prompt = f.read()

    client = make_client(args.backend, model=args.model,
                         base_url=args.base_url, json_schema=RCAOutput.JSON_SCHEMA)
    agent = RCAAgent(client, system_prompt)

    # 修复侧：fake 集群 + 临时审计
    pods = [Pod(i["target"]["pod"], i["target"]["namespace"],
                i["target"]["owner"]) for i in DEMO_INCIDENTS]
    tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False); tmp.close()
    action = RestartPodAction(FakeK8sClient(pods), Audit(tmp.name),
                              sleep_fn=lambda s: None)

    print(f"后端={args.backend}\n" + "=" * 60)
    for inc in DEMO_INCIDENTS:
        ctx = IncidentContext.from_dict(inc)
        rca = agent.analyze(ctx)
        print(f"\n[{ctx.incident_id}] {ctx.summary}")
        print(f"  RCA: 弃权={rca.abstain} 类别={rca.root_cause_category} "
              f"置信度={rca.confidence:.2f} 证据={rca.evidence_ids}")
        print("  " + decide_and_remediate(rca, inc["target"], inc["incident_id"], action))
    print(f"\n审计记录：{tmp.name}")


if __name__ == "__main__":
    main()
