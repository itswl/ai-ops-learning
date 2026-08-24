"""L1 修复动作：重启无状态 Pod，五层防御齐全。

安全边界建立在机制上，不是 prompt 上：
  第 1 层 权限：给 client 的 kubeconfig 就是"能删 Pod、不能删 Deployment"——想越权也做不到。
                （FakeK8sClient 没有 delete_deployment 这个方法，就是这层的最小体现）
  第 2 层 动作白名单 + 参数 schema：只有这一个参数化动作，绝不"让 LLM 生成 shell 去跑"。
  第 3 层 风险分级 + 前置条件：L1 自动带通知；前置条件不满足就拒绝。
  第 4 层 执行 + 成功/失败判据 + 失败即停：失败只升级给人，绝不自动尝试别的动作。
  第 5 层 审计 + 幂等：每个动作留决策快照；同一事件不重复执行。
"""
from __future__ import annotations

from dataclasses import dataclass

from audit import Audit

RISK_LEVEL = "L1"
STATELESS_OWNERS = {"Deployment", "ReplicaSet"}   # 只对无状态负载自动重启
CRASHLOOP_RESTART_THRESHOLD = 5   # 重启已很多 → 多半不是瞬时问题，重启治不好


@dataclass
class Result:
    status: str      # executed / skipped_idempotent / rejected_precondition / failed_escalated / rejected_params
    detail: str
    target: str = ""


class RestartPodAction:
    def __init__(self, k8s, audit: Audit, sleep_fn=None, ready_timeout_s=60,
                 poll_interval_s=3):
        self.k8s = k8s
        self.audit = audit
        self.sleep = sleep_fn or (lambda s: None)   # 测试可注入 no-op
        self.ready_timeout_s = ready_timeout_s
        self.poll_interval_s = poll_interval_s

    def run(self, *, namespace: str, pod_name: str, incident_id: str,
            evidence_snapshot=None, confidence: float = 0.0,
            approver: str = "auto") -> Result:
        target = f"{namespace}/{pod_name}"

        # --- 第 2 层：参数校验 ---
        if not namespace or not pod_name or not incident_id:
            return Result("rejected_params", "namespace/pod_name/incident_id 均必填", target)

        # --- 第 5 层（前置）：幂等。同一事件对同一 Pod 不重复重启 ---
        if self.audit.already_executed(incident_id, target):
            return Result("skipped_idempotent",
                          f"事件 {incident_id} 已对 {target} 执行过重启，跳过", target)

        # --- 第 3 层：前置条件（机制校验，不是 prompt 提醒）---
        pod = self.k8s.get_pod(namespace, pod_name)
        if pod is None:
            return self._reject(incident_id, target, confidence, approver,
                                "Pod 不存在")
        if pod.owner_kind not in STATELESS_OWNERS:
            return self._reject(incident_id, target, confidence, approver,
                                f"owner 为 {pod.owner_kind or '无'}，非无状态负载，"
                                f"拒绝自动重启（有状态服务需人工）")
        if pod.restart_count >= CRASHLOOP_RESTART_THRESHOLD:
            return self._reject(incident_id, target, confidence, approver,
                                f"重启次数已达 {pod.restart_count}，疑似 CrashLoop，"
                                f"重启治不好，升级人工排查")

        # --- 第 3 层：审批门。L1 = 自动执行但通知 owner ---
        # （L2/L3 动作在此处会阻塞等待人工确认，本 L1 动作放行）

        # --- 第 4 层：执行 ---
        try:
            self.k8s.delete_pod(namespace, pod_name)
        except Exception as e:
            return self._fail(incident_id, target, confidence, approver,
                              f"执行删除 Pod 失败：{e}")

        # --- 第 4 层：成功判据 = 重建后在超时内 ready ---
        waited = 0
        while waited < self.ready_timeout_s:
            if self.k8s.is_ready(namespace, pod_name):
                self.audit.record({
                    "action": "restart_pod", "risk": RISK_LEVEL,
                    "incident_id": incident_id, "target": target,
                    "status": "executed", "approver": approver,
                    "confidence": confidence,
                    "evidence_snapshot": evidence_snapshot or [],
                    "detail": f"重启完成，Pod 在 {waited}s 内恢复 ready",
                })
                return Result("executed", f"重启完成，{waited}s 内 ready", target)
            self.sleep(self.poll_interval_s)
            waited += self.poll_interval_s

        # --- 第 4 层：失败即停，升级给人，绝不自动尝试别的动作 ---
        return self._fail(incident_id, target, confidence, approver,
                          f"重启后 {self.ready_timeout_s}s 内未恢复 ready，停止并升级")

    def _reject(self, incident_id, target, confidence, approver, reason) -> Result:
        self.audit.record({
            "action": "restart_pod", "risk": RISK_LEVEL, "incident_id": incident_id,
            "target": target, "status": "rejected_precondition",
            "approver": approver, "confidence": confidence, "detail": reason,
        })
        return Result("rejected_precondition", reason, target)

    def _fail(self, incident_id, target, confidence, approver, reason) -> Result:
        self.audit.record({
            "action": "restart_pod", "risk": RISK_LEVEL, "incident_id": incident_id,
            "target": target, "status": "failed_escalated",
            "approver": approver, "confidence": confidence, "detail": reason,
        })
        return Result("failed_escalated", reason, target)
