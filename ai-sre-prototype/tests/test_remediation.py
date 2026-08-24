"""L1 修复动作的五层防御测试。

可直接跑：python tests/test_remediation.py
也兼容 pytest：pytest tests/test_remediation.py
每个用例对应一层防御或一条成功/失败路径。
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "remediation"))

from k8s_client import FakeK8sClient, Pod  # noqa: E402
from audit import Audit  # noqa: E402
from actions import RestartPodAction  # noqa: E402


def _mk(pods, recreate_ready=True):
    tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
    tmp.close()
    audit = Audit(tmp.name)
    k8s = FakeK8sClient(pods, recreate_ready=recreate_ready)
    action = RestartPodAction(k8s, audit, sleep_fn=lambda s: None)
    return action, k8s, audit, tmp.name


def test_happy_path_stateless_restart():
    """成功路径：无状态、ready 的 Pod → 重启成功。"""
    pod = Pod("web-1", "prod", "Deployment", ready=True, restart_count=0)
    action, k8s, _, _ = _mk([pod])
    r = action.run(namespace="prod", pod_name="web-1", incident_id="inc-1")
    assert r.status == "executed", r
    assert ("prod", "web-1") in k8s.deleted
    assert pod.restart_count == 1
    print("PASS 成功路径")


def test_idempotent_same_incident():
    """第5层：同一事件对同一 Pod 第二次触发 → 幂等跳过，不重复重启。"""
    pod = Pod("web-1", "prod", "Deployment")
    action, k8s, _, _ = _mk([pod])
    r1 = action.run(namespace="prod", pod_name="web-1", incident_id="inc-1")
    r2 = action.run(namespace="prod", pod_name="web-1", incident_id="inc-1")
    assert r1.status == "executed"
    assert r2.status == "skipped_idempotent", r2
    assert len(k8s.deleted) == 1  # 只删了一次
    print("PASS 幂等")


def test_reject_statefulset():
    """第3层：有状态负载 → 拒绝自动重启。"""
    pod = Pod("db-0", "prod", "StatefulSet")
    action, _, _, _ = _mk([pod])
    r = action.run(namespace="prod", pod_name="db-0", incident_id="inc-2")
    assert r.status == "rejected_precondition", r
    assert "非无状态" in r.detail
    print("PASS 拒绝有状态负载")


def test_reject_crashloop():
    """第3层：重启次数过高（疑似 CrashLoop）→ 拒绝，升级人工。"""
    pod = Pod("web-1", "prod", "Deployment", restart_count=8)
    action, _, _, _ = _mk([pod])
    r = action.run(namespace="prod", pod_name="web-1", incident_id="inc-3")
    assert r.status == "rejected_precondition", r
    assert "CrashLoop" in r.detail
    print("PASS 拒绝 CrashLoop")


def test_reject_missing_pod():
    """第3层：Pod 不存在 → 拒绝。"""
    action, _, _, _ = _mk([])
    r = action.run(namespace="prod", pod_name="ghost", incident_id="inc-4")
    assert r.status == "rejected_precondition", r
    print("PASS 拒绝不存在的 Pod")


def test_reject_bad_params():
    """第2层：参数缺失 → 拒绝。"""
    action, _, _, _ = _mk([Pod("web-1", "prod", "Deployment")])
    r = action.run(namespace="", pod_name="web-1", incident_id="inc-5")
    assert r.status == "rejected_params", r
    print("PASS 参数校验")


def test_failure_path_escalates():
    """第4层：重启后未恢复 ready → 失败即停并升级，不尝试其他动作。"""
    pod = Pod("web-1", "prod", "Deployment")
    action, k8s, _, _ = _mk([pod], recreate_ready=False)  # 模拟重建后仍不健康
    r = action.run(namespace="prod", pod_name="web-1", incident_id="inc-6")
    assert r.status == "failed_escalated", r
    assert ("prod", "web-1") in k8s.deleted  # 确实执行了，但判定失败
    print("PASS 失败升级")


def test_audit_snapshot_written():
    """第5层：执行留下带决策快照的审计。"""
    pod = Pod("web-1", "prod", "Deployment")
    action, _, audit, path = _mk([pod])
    action.run(namespace="prod", pod_name="web-1", incident_id="inc-7",
               evidence_snapshot=["E1", "E2"], confidence=0.8, approver="auto")
    with open(path, encoding="utf-8") as f:
        content = f.read()
    assert "inc-7" in content and "evidence_snapshot" in content and "executed" in content
    print("PASS 审计快照")


ALL = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main():
    passed = 0
    for t in ALL:
        t()
        passed += 1
    print(f"\n全部通过：{passed}/{len(ALL)}")


if __name__ == "__main__":
    main()
