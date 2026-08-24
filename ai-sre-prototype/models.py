"""共享数据模型（stdlib only，用 dataclasses 而非 pydantic 以零依赖运行）。

生产里可直接换成 pydantic BaseModel，字段语义一致。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict

# RCA 根因类别的封闭集合。判类别（"是发布问题"）比判具体 commit 容易，也常常够用。
CATEGORIES = {
    "deploy",      # 发布/变更引入
    "resource",    # 资源不足：OOM/CPU throttle/磁盘满
    "dependency",  # 依赖故障：DB/Redis/Kafka/上下游
    "network",     # 网络：DNS/conntrack/丢包/证书
    "config",      # 配置错误
    "external",    # 外部/第三方
    "unknown",     # 判不了（配合 abstain）
}


@dataclass
class EvidenceItem:
    id: str          # 证据编号，如 "E1"，Agent 必须按 id 引用
    text: str


@dataclass
class IncidentContext:
    """喂给 RCA Agent 的结构化上下文——一次事件的证据快照（不是实时数据）。"""
    incident_id: str
    summary: str
    evidence: list[EvidenceItem] = field(default_factory=list)
    # 数据源状态：明确告诉模型哪些源查不到，防止把"没查到"当成"没问题"
    data_source_status: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def from_dict(d: dict) -> "IncidentContext":
        return IncidentContext(
            incident_id=d["incident_id"],
            summary=d["summary"],
            evidence=[EvidenceItem(**e) for e in d.get("evidence", [])],
            data_source_status=d.get("data_source_status", {}),
        )

    def evidence_ids(self) -> set[str]:
        return {e.id for e in self.evidence}


@dataclass
class RCAOutput:
    """RCA Agent 的结构化输出。"""
    abstain: bool                     # 证据不足以定论时为 True
    root_cause_category: str | None   # abstain=True 时应为 None
    confidence: float                 # 0-1
    evidence_ids: list[str]           # 引用的证据编号，必须来自上下文
    reasoning: str
    missing_evidence: list[str] = field(default_factory=list)

    JSON_SCHEMA = {
        "type": "object",
        "properties": {
            "abstain": {"type": "boolean"},
            "root_cause_category": {
                "type": ["string", "null"],
                "enum": sorted(CATEGORIES) + [None],
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
            "reasoning": {"type": "string"},
            "missing_evidence": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["abstain", "root_cause_category", "confidence",
                     "evidence_ids", "reasoning"],
    }

    @staticmethod
    def parse(text: str) -> "RCAOutput":
        """从模型输出里宽松提取 JSON 并校验。"""
        obj = _extract_json(text)
        abstain = bool(obj.get("abstain", False))
        cat = obj.get("root_cause_category")
        if cat in ("", "null"):
            cat = None
        # 校验：弃权则类别应为空；否则类别必须合法
        if abstain:
            cat = None
        elif cat not in CATEGORIES:
            cat = "unknown"
        conf = obj.get("confidence", 0.0)
        try:
            conf = max(0.0, min(1.0, float(conf)))
        except (TypeError, ValueError):
            conf = 0.0
        ev = obj.get("evidence_ids", []) or []
        if not isinstance(ev, list):
            ev = []
        return RCAOutput(
            abstain=abstain,
            root_cause_category=cat,
            confidence=conf,
            evidence_ids=[str(x) for x in ev],
            reasoning=str(obj.get("reasoning", "")),
            missing_evidence=[str(x) for x in (obj.get("missing_evidence", []) or [])],
        )

    def to_dict(self) -> dict:
        return asdict(self)


def _extract_json(text: str) -> dict:
    """容错提取：优先整体 parse，失败则截取第一个 { 到最后一个 }。"""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"无法从模型输出解析 JSON: {text[:200]!r}")
