"""K8s 客户端抽象：FakeK8sClient（离线可跑/测试）+ RealK8sClient（真集群）。

修复动作只依赖这个接口，因此评估和单测无需真集群；上生产时换成 RealK8sClient 即可。
真正的安全地基是 RealK8sClient 用的 kubeconfig 权限（只读+删Pod，不能删Deployment），
不是代码里的判断——见 actions.py 的第 1 层说明。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Pod:
    name: str
    namespace: str
    owner_kind: str          # Deployment / ReplicaSet / StatefulSet / DaemonSet / ""
    phase: str = "Running"   # Running / Pending / ...
    ready: bool = True
    restart_count: int = 0
    labels: dict = field(default_factory=dict)


class FakeK8sClient:
    """内存模拟。delete_pod 模拟"删除后由控制器重建"。

    recreate_ready 控制重建后是否变 ready，用来测成功/失败两条路径。
    """

    def __init__(self, pods: list[Pod], recreate_ready: bool = True):
        self._pods = {(p.namespace, p.name): p for p in pods}
        self.recreate_ready = recreate_ready
        self.deleted: list[tuple[str, str]] = []

    def get_pod(self, namespace: str, name: str) -> Pod | None:
        return self._pods.get((namespace, name))

    def owner_kind(self, namespace: str, name: str) -> str:
        p = self.get_pod(namespace, name)
        return p.owner_kind if p else ""

    def delete_pod(self, namespace: str, name: str) -> None:
        p = self._pods.get((namespace, name))
        if not p:
            raise KeyError(f"pod {namespace}/{name} 不存在")
        self.deleted.append((namespace, name))
        # 模拟控制器重建：重启计数+1；ready 取决于注入的健康与否
        p.restart_count += 1
        p.ready = self.recreate_ready
        p.phase = "Running" if self.recreate_ready else "Pending"

    def is_ready(self, namespace: str, name: str) -> bool:
        p = self.get_pod(namespace, name)
        return bool(p and p.ready and p.phase == "Running")


class RealK8sClient:
    """真集群适配器。lazy import kubernetes，未安装也不影响本文件加载。

    权限即安全边界：给这个客户端用的 ServiceAccount 应当只有
    get/list pod + delete pod，绝不给 delete deployment / exec 等。
    """

    def __init__(self, kubeconfig: str | None = None):
        from kubernetes import client, config  # noqa: 延迟导入
        if kubeconfig:
            config.load_kube_config(config_file=kubeconfig)
        else:
            try:
                config.load_incluster_config()
            except Exception:
                config.load_kube_config()
        self.core = client.CoreV1Api()
        self.apps = client.AppsV1Api()

    def get_pod(self, namespace: str, name: str):
        from kubernetes.client.exceptions import ApiException
        try:
            p = self.core.read_namespaced_pod(name, namespace)
        except ApiException as e:
            if e.status == 404:
                return None
            raise
        cs = p.status.container_statuses or []
        return Pod(
            name=name, namespace=namespace,
            owner_kind=self._owner_kind(p),
            phase=p.status.phase or "",
            ready=all(c.ready for c in cs) if cs else False,
            restart_count=sum(c.restart_count for c in cs),
            labels=p.metadata.labels or {},
        )

    def _owner_kind(self, pod) -> str:
        owners = pod.metadata.owner_references or []
        if not owners:
            return ""
        kind = owners[0].kind
        # ReplicaSet 往上解析到 Deployment（无状态判定的关键）
        if kind == "ReplicaSet":
            try:
                rs = self.apps.read_namespaced_replica_set(
                    owners[0].name, pod.metadata.namespace)
                rs_owners = rs.metadata.owner_references or []
                if rs_owners and rs_owners[0].kind == "Deployment":
                    return "Deployment"
            except Exception:
                pass
            return "ReplicaSet"
        return kind

    def owner_kind(self, namespace: str, name: str) -> str:
        p = self.get_pod(namespace, name)
        return p.owner_kind if p else ""

    def delete_pod(self, namespace: str, name: str) -> None:
        self.core.delete_namespaced_pod(name, namespace)

    def is_ready(self, namespace: str, name: str) -> bool:
        p = self.get_pod(namespace, name)
        return bool(p and p.ready and p.phase == "Running")
