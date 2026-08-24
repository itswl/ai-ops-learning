"""离线评估流水线：把 RCA Agent 跑过评估集，输出指标表。

分开评"检索/判断"两侧的核心指标：
- category_accuracy：不该弃权且没弃权的样本里，类别判对的比例
- abstain_recall：该弃权的样本里，正确弃权的比例（安全性核心）
- false_abstain_rate：不该弃权却弃权了的比例
- hallucination_rate：引用了上下文里不存在的证据编号的样本比例
- safe_correct：综合"既安全又判对"的比例

用法：
  python eval/run_eval.py --backend mock                       # 离线，验证流水线
  python eval/run_eval.py --backend mock --prompt prompts/rca_v2_broken.txt   # 看回归
  python eval/run_eval.py --backend vllm --model Qwen/Qwen3-8B  # 打真实 vLLM
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import IncidentContext, RCAOutput  # noqa: E402
from llm_client import make_client  # noqa: E402
from rca_agent import RCAAgent  # noqa: E402


def load_dataset(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def evaluate(agent, rows):
    per_sample = []
    for row in rows:
        ctx = IncidentContext.from_dict(row)
        gt = row["ground_truth"]
        out = agent.analyze(ctx)
        cited_invalid = [x for x in out.evidence_ids if x not in ctx.evidence_ids()]
        per_sample.append({
            "id": ctx.incident_id,
            "gt_category": gt["category"],
            "gt_abstain": gt["should_abstain"],
            "pred_category": out.root_cause_category,
            "pred_abstain": out.abstain,
            "confidence": round(out.confidence, 2),
            "hallucinated": bool(cited_invalid),
            "invalid_ids": cited_invalid,
        })
    return per_sample


def compute_metrics(per_sample):
    n = len(per_sample)
    should_abstain = [s for s in per_sample if s["gt_abstain"]]
    should_not = [s for s in per_sample if not s["gt_abstain"]]

    # 类别准确：在"不该弃权且没弃权"的样本里算
    judged = [s for s in should_not if not s["pred_abstain"]]
    cat_correct = sum(s["pred_category"] == s["gt_category"] for s in judged)
    category_accuracy = cat_correct / len(judged) if judged else 0.0

    abstain_recall = (sum(s["pred_abstain"] for s in should_abstain)
                      / len(should_abstain)) if should_abstain else 0.0
    false_abstain_rate = (sum(s["pred_abstain"] for s in should_not)
                          / len(should_not)) if should_not else 0.0
    hallucination_rate = sum(s["hallucinated"] for s in per_sample) / n if n else 0.0

    # safe_correct：该弃权的正确弃权 + 不该弃权的判对类别，且无幻觉
    safe = 0
    for s in per_sample:
        ok_no_halluc = not s["hallucinated"]
        if s["gt_abstain"]:
            ok = s["pred_abstain"]
        else:
            ok = (not s["pred_abstain"]) and s["pred_category"] == s["gt_category"]
        safe += ok and ok_no_halluc
    safe_correct = safe / n if n else 0.0

    return {
        "n_total": n,
        "n_should_abstain": len(should_abstain),
        "category_accuracy": category_accuracy,
        "abstain_recall": abstain_recall,
        "false_abstain_rate": false_abstain_rate,
        "hallucination_rate": hallucination_rate,
        "safe_correct": safe_correct,
    }


def print_report(metrics, per_sample, verbose):
    print("\n===== 逐样本 =====")
    print(f"{'id':<9}{'真类别':<12}{'预测':<12}{'该弃权':<7}{'弃权':<6}{'幻觉':<5}")
    for s in per_sample:
        flag = ""
        if s["gt_abstain"] and not s["pred_abstain"]:
            flag = "  <- 漏弃权"
        elif not s["gt_abstain"] and s["pred_abstain"]:
            flag = "  <- 误弃权"
        elif not s["gt_abstain"] and s["pred_category"] != s["gt_category"]:
            flag = "  <- 判错"
        gt_cat = s["gt_category"] or "-"
        pred = s["pred_category"] or "(弃权)"
        print(f"{s['id']:<9}{gt_cat:<12}{pred:<12}"
              f"{str(s['gt_abstain']):<7}{str(s['pred_abstain']):<6}"
              f"{str(s['hallucinated']):<5}{flag}")

    print("\n===== 指标 =====")
    m = metrics
    print(f"样本数                 : {m['n_total']}（其中该弃权 {m['n_should_abstain']}）")
    print(f"类别准确率             : {m['category_accuracy']:.2%}")
    print(f"弃权召回率(安全核心)   : {m['abstain_recall']:.2%}")
    print(f"误弃权率               : {m['false_abstain_rate']:.2%}")
    print(f"幻觉率                 : {m['hallucination_rate']:.2%}")
    print(f"综合安全且正确         : {m['safe_correct']:.2%}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="mock",
                    choices=["mock", "vllm", "ollama", "openai"])
    ap.add_argument("--prompt", default=None, help="system prompt 文件，默认 v1_good")
    ap.add_argument("--dataset", default=None)
    ap.add_argument("--model", default="Qwen/Qwen3-8B")
    ap.add_argument("--base-url", default="http://localhost:8000/v1")
    ap.add_argument("--json-out", default=None, help="把指标写入 JSON 文件")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    prompt_path = args.prompt or os.path.join(root, "prompts", "rca_v1_good.txt")
    dataset_path = args.dataset or os.path.join(here, "dataset.jsonl")

    with open(prompt_path, encoding="utf-8") as f:
        system_prompt = f.read()
    rows = load_dataset(dataset_path)

    client = make_client(args.backend, model=args.model,
                         base_url=args.base_url, json_schema=RCAOutput.JSON_SCHEMA)
    agent = RCAAgent(client, system_prompt)

    print(f"后端={args.backend}  prompt={os.path.basename(prompt_path)}  样本={len(rows)}")
    per_sample = evaluate(agent, rows)
    metrics = compute_metrics(per_sample)
    print_report(metrics, per_sample, args.verbose)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump({"metrics": metrics, "per_sample": per_sample},
                      f, ensure_ascii=False, indent=2)
        print(f"\n指标已写入 {args.json_out}")


if __name__ == "__main__":
    main()
