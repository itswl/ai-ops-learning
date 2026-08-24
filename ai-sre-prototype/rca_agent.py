"""RCA Agent：结构化上下文 in，结构化根因判断 out。

体现两个深水区要点：
- 上下文工程：把 IncidentContext 渲染成高信噪比、带证据编号、带数据源状态的 prompt。
- 弃权能力：prompt 要求证据不足时 abstain（评估里专门考这个）。
"""
from __future__ import annotations

from models import IncidentContext, RCAOutput


def render_user_message(ctx: IncidentContext) -> str:
    lines = [f"## 事件\n{ctx.summary}", "", "## 证据"]
    for e in ctx.evidence:
        lines.append(f"- [{e.id}] {e.text}")
    if ctx.data_source_status:
        lines.append("")
        lines.append("## 数据源状态")
        for src, st in ctx.data_source_status.items():
            mark = "OK" if st == "ok" else st
            lines.append(f"- {src}: {mark}")
    lines.append("")
    lines.append("## 任务\n基于以上证据给出根因判断。只引用给出的证据编号（如 E1）。")
    return "\n".join(lines)


class RCAAgent:
    def __init__(self, llm_client, system_prompt: str, max_retries: int = 1):
        self.llm = llm_client
        self.system_prompt = system_prompt
        self.max_retries = max_retries

    def analyze(self, ctx: IncidentContext) -> RCAOutput:
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": render_user_message(ctx)},
        ]
        last_err = None
        for _ in range(self.max_retries + 1):
            text = self.llm.generate(messages)
            try:
                # 返回原始判断，不剔除非法证据——幻觉率要靠评估层测量，
                # 若在此静默剔除，指标就永远是 0（掩盖问题）。
                return RCAOutput.parse(text)
            except ValueError as e:
                last_err = e
                messages.append({"role": "user",
                                 "content": "输出不是合法 JSON，请仅返回 JSON。"})
                continue
        raise RuntimeError(f"解析失败：{last_err}")
