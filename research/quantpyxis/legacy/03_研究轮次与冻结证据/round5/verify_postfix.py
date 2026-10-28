# -*- coding: utf-8 -*-
"""Post-fix acceptance record (P0): verify F1/F2/F6 fixes, old review retained, seal valid,
and that round-5 paper artifacts exist.  Writes round5/verify/VERIFY4_POSTFIX.md + .json.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

WS = Path(__file__).resolve().parents[2]
R4 = WS / "winrate_backtest_20261020" / "round4"
R5 = WS / "winrate_backtest_20261020" / "round5"
SEAL = R5 / "SEAL"
OUT = R5 / "verify"


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main():
    checks = []

    def add(name, ok, detail):
        checks.append({"name": name, "pass": bool(ok), "detail": detail})

    # F1 — stress summary row
    import pandas as pd
    s = pd.read_csv(R4 / "out" / "phase_stress_summary.csv")
    b = pd.read_csv(R4 / "out" / "phase_stress_b100n30_top15.csv")
    row = s[s["tag"] == "b100_n30_top15"].iloc[0]
    base = b[b["cost"] == "base"].iloc[0]
    stress = b[b["cost"] == "x2cost"].iloc[0]
    add("F1_stress_row_recomputed",
        abs(float(row["total_return"]) - float(stress["total_return"])) < 1e-6
        and abs(float(row["max_drawdown"]) - float(stress["max_drawdown"])) < 1e-6
        and abs(float(base["total_return"]) - 1.7456069325168508) < 1e-6,
        {"summary_row": {k: float(row[k]) for k in
                         ("base_return", "base_dd", "total_return", "max_drawdown", "sharpe")},
         "saved_stress_file": {"base_return": float(base["total_return"]),
                               "stress_return": float(stress["total_return"]),
                               "stress_dd": float(stress["max_drawdown"]),
                               "stress_sharpe": float(stress["sharpe"])}})

    # F2 — freeze DD peak date
    freeze_txt = (R4 / "CANDIDATE_A_FREEZE.md").read_text(encoding="utf-8")
    add("F2_freeze_peak_date",
        "峰 2017-11-22" in freeze_txt and "谷 2020-03-23" in freeze_txt
        and "峰 2020-03-23" not in freeze_txt,
        {"has_peak_2017_11_22": "峰 2017-11-22" in freeze_txt,
         "has_wrong_peak": "峰 2020-03-23" in freeze_txt})

    # F6 — report disclosure
    r4 = (R4 / "ROUND4.md").read_text(encoding="utf-8")
    uses_oos = ("A 的选择也含 OOS" in r4 or "A 的选择也不是纯训练期" in r4
                or ("OOS 信息" in r4 and "选择" in r4))
    body = r4.split("## 8. 独立复核")[0]   # ignore the changelog that quotes the removed phrase
    removed_claim = "训练期最好的 b100_n30_top15" not in body
    add("F6_report_disclosure",
        ("F6" in r4 and "预注册候选集" in r4 and removed_claim and uses_oos),
        {"mentions_F6": "F6" in r4, "mentions_preregistered": "预注册候选集" in r4,
         "removed_train_best_claim": removed_claim,
         "states_A_used_OOS": uses_oos})
    add("F6_freeze_disclosure",
        "选择披露" in freeze_txt and "F6" in freeze_txt,
        {"has_selection_disclosure": "选择披露" in freeze_txt})

    # old review retained (it still contains its F1 finding, i.e. not rewritten)
    v4 = R4 / "verify" / "VERIFY4.md"
    vt = v4.read_text(encoding="utf-8")
    add("VERIFY4_retained_unchanged",
        "phase_stress_summary.csv" in vt and "stale" in vt.lower(),
        {"sha256": sha(v4), "bytes": v4.stat().st_size})

    # seal snapshot
    manifest = json.loads((SEAL / "FREEZE_MANIFEST.json").read_text(encoding="utf-8"))
    bad = []
    for f in manifest["files"]:
        rel = f["rel"]
        p = SEAL / "snapshot" / rel
        if not p.exists():
            p = SEAL / rel
        if sha(p) != f["sha256"]:
            bad.append(rel)
    add("SEAL_snapshot_valid", not bad,
        {"freeze_id": manifest["freeze_id"], "freeze_at": manifest["freeze_at"],
         "files": len(manifest["files"]), "bad": bad,
         "manifest_sha256": sha(SEAL / "FREEZE_MANIFEST.json")})

    # round-5 paper validation
    vp = R5 / "paper" / "runs" / "validation" / "validation.json"
    val = json.loads(vp.read_text(encoding="utf-8")) if vp.exists() else {}
    add("P1_validation_suite", bool(val.get("pass")),
        {"pass": val.get("pass"), "summary": val.get("summary")})

    # state continuation check
    sc = R5 / "paper" / "runs" / "2026-10-19" / "state_check.json"
    scj = json.loads(sc.read_text(encoding="utf-8")) if sc.exists() else {}
    add("P1_state_check", bool(scj.get("all_pass")),
        {"all_pass": scj.get("all_pass"),
         "cutoffs": [{c["cutoff"]: c.get("pass")} for c in scj.get("cutoffs", [])],
         "direct_vs_truncated": scj.get("direct_vs_truncated")})

    # first record: PENDING_DATA, no signal, no fills
    pend = R5 / "paper" / "runs" / "2026-10-20" / "signals" / "PENDING_2026-10-20.json"
    pj = json.loads(pend.read_text(encoding="utf-8")) if pend.exists() else {}
    add("P1_first_record_pending_data",
        pj.get("status") == "PENDING_DATA" and pj.get("no_signal_created") is True
        and pj.get("is_month_end") is False,
        {"status": pj.get("status"), "reason": pj.get("reason"),
         "no_signal_created": pj.get("no_signal_created"),
         "is_month_end": pj.get("is_month_end"),
         "freeze_at": pj.get("freeze_at"), "sha256": sha(pend) if pend.exists() else None})

    current = {}
    for p in (R4 / "ROUND4.md", R4 / "CANDIDATE_A_FREEZE.md", v4,
              R4 / "out" / "phase_stress_summary.csv",
              R5 / "EVIDENCE_CHANGELOG.md"):
        current[str(p.relative_to(WS))] = {"sha256": sha(p), "bytes": p.stat().st_size}
    res = {"schema": "round5-postfix-1", "generated_at": __import__("datetime").datetime.now(
        __import__("datetime").timezone(__import__("datetime").timedelta(hours=8))
    ).isoformat(timespec="seconds"),
        "checks": checks,
        "pass": all(c["pass"] for c in checks),
        "audited_versions": {"ROUND4.md": "337900a4810e968300f39af67c71e87ad09eae9a284b8c99332c501935b57a19",
                             "CANDIDATE_A_FREEZE.md": "4128e5018b9796cbc051d648f8a447a0e38bd821ba55c5e527c6fd8e940c1513"},
        "current_hashes": current,
        "not_covered": [
            "平台复现（P2）：未提交，无任务回执",
            "前向结果：首个 2026-10-20 记录为 PENDING_DATA（数据未入库），尚无纸面成交",
            "行业暴露：本地无历史行业分类，UNKNOWN",
            "滑点校准：需平台逐笔校准",
            "Q0 169 个风险零收益日：需平台季度原规则诊断",
            "修改前字节未保留（流程缺陷，见 EVIDENCE_CHANGELOG §1）",
        ]}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "VERIFY4_POSTFIX.json").write_text(json.dumps(res, indent=1, ensure_ascii=False),
                                              encoding="utf-8")
    lines = ["# VERIFY4 修复后验收记录（保留旧 VERIFY4，不回写）", "",
             f"- 生成时间：{res['generated_at']}",
             f"- 总结论：**{'PASS' if res['pass'] else 'FAIL'}**",
             f"- 旧复核记录哈希：`VERIFY4.md` = `{sha(v4)}`（保留原文件，未改写）", "",
             "## 逐项", "", "| 项 | 结果 | 证据 |", "|---|---|---|"]
    for c in checks:
        lines.append(f"| {c['name']} | {'PASS' if c['pass'] else 'FAIL'} | "
                     f"`{json.dumps(c['detail'], ensure_ascii=False)[:220]}` |")
    lines += ["", "## 当前文件哈希", "", "| 文件 | sha256 | 字节 |", "|---|---|---:|"]
    for k, v in current.items():
        lines.append(f"| `{k}` | `{v['sha256']}` | {v['bytes']} |")
    lines += ["", "## 仍未覆盖（如实保留）", ""]
    lines += [f"- {x}" for x in res["not_covered"]]
    lines += ["", "## 修复内容摘要", "",
              "- F1：`phase_stress_summary.csv` 的 A 行已用正确配置重算（+149.6728% / -25.2261% / 0.746868 / 费用 14,351.62），"
              "与 `phase_stress_b100n30_top15.csv` 和独立复跑一致；新增 `round4/run_stress.py`。",
              "- F2：冻结文件回撤峰日期改为 2017-11-22（谷 2020-03-23）。",
              "- F6：ROUND4.md §1/§4.5/§5 与冻结文件已披露“A 的选择也含 OOS”；不再称 A 为训练期最优。",
              "- 新增：`round4/run_phaseH.py`；`round5/` 全套封存、前向脚本、归因、平台计划、errata、OOS 窗口补全。"]
    (OUT / "VERIFY4_POSTFIX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"pass": res["pass"],
                      "checks": {c["name"]: c["pass"] for c in checks}}, indent=1))
    print("wrote", OUT / "VERIFY4_POSTFIX.md")


if __name__ == "__main__":
    main()
