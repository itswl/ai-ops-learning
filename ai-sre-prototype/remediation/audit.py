"""审计与幂等：每个修复动作追加一条决策快照（JSONL），并据此做幂等去重。

第 5 层防御。审计不是日志——它记录"当时为什么这么做"（触发事件、证据、置信度、
谁批的、结果），出事后可复盘、可追责。
"""
from __future__ import annotations

import json
import os
import time


class Audit:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    def record(self, entry: dict) -> None:
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _iter(self):
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def already_executed(self, incident_id: str, target: str) -> bool:
        """同一事件对同一目标是否已成功执行过（幂等判断）。"""
        for e in self._iter():
            if (e.get("incident_id") == incident_id
                    and e.get("target") == target
                    and e.get("status") == "executed"):
                return True
        return False
