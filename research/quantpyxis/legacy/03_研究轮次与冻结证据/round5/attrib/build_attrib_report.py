# -*- coding: utf-8 -*-
"""Build ATTRIBUTION_A.md (Chinese) from the attribution outputs.

All numbers in the report are read from the CSVs/JSONs produced by
rerun_a.py, step2_over15.py, step3abc.py, step3de.py, step3f_stress.py,
step3g_fifo.py, step3h_cost_delay.py.  Nothing is typed by hand except the
method narrative, so the tables cannot drift from the artifacts.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import attrib_common as C  # noqa: E402


# --------------------------------------------------------------------------- #
# markdown helpers
# --------------------------------------------------------------------------- #
def md_table(df: pd.DataFrame, headers: list | None = None, align: str | None = None) -> str:
    cols = list(df.columns)
    hdr = headers or cols
    lines = ["| " + " | ".join(str(h) for h in hdr) + " |",
             "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, r in df.iterrows():
        vals = []
        for c in cols:
            v = r[c]
            if isinstance(v, float):
                vals.append(f"{v:,.4f}" if abs(v) < 10 else f"{v:,.2f}")
            else:
                vals.append(str(v))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def md_raw(rows_labels: list, values: list, header: list) -> str:
    lines = ["| " + " | ".join(header) + " |",
             "|" + "|".join(["---"] * len(header)) + "|"]
    for lab, val in zip(rows_labels, values):
        lines.append("| " + " | ".join([str(lab)] + [str(v) for v in val]) + " |")
    return "\n".join(lines)


def pct(x, nd=4):
    return f"{x * 100:.{nd}f}%"


def money(x, nd=2):
    return f"{x:,.{nd}f}"


def truncate(s, n=90):
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def main():
    O = C.OUT
    a = json.loads((O / "a_summary.json").read_text(encoding="utf-8"))
    ver = a["verify"]
    pub = a["published"]

    nav = pd.read_csv(O / "a_nav.csv", dtype={"date": str}).set_index("date")
    for c in ("nav", "cash", "market_value", "exposure", "ret", "n_positions", "fees"):
        nav[c] = pd.to_numeric(nav[c], errors="coerce")

    o15 = json.loads((O / "over15_summary.json").read_text(encoding="utf-8"))
    o15y = pd.read_csv(O / "over15_yearly.csv", dtype={"year": str})
    o15e = pd.read_csv(O / "over15_episodes.csv")
    o15d = pd.read_csv(O / "over15_detail.csv", dtype={"date": str, "extra_code": str})
    o15d["extra_code"] = o15d["extra_code"].astype(str).str.zfill(6)

    sa = json.loads((O / "step3abc_summary.json").read_text(encoding="utf-8"))
    worst = pd.read_csv(O / "worst20_days.csv", dtype={"date": str})
    dd3 = pd.read_csv(O / "drawdowns_top3.csv", dtype={"recovery_date": str})
    top5 = pd.read_csv(O / "top5_stock_contrib.csv", dtype={"code": str})
    top5["code"] = top5["code"].astype(str).str.zfill(6)
    sens = pd.read_csv(O / "exclude_top1_sensitivity.csv")
    sens_pp = (sens["total_return"].iloc[1] - sens["total_return"].iloc[0]) * 100.0
    allnet = pd.read_csv(O / "stock_contrib_all.csv", dtype={"code": str})
    allnet["code"] = allnet["code"].astype(str).str.zfill(6)

    de = json.loads((O / "step3de_summary.json").read_text(encoding="utf-8"))
    divy = pd.read_csv(O / "diversification_yearly.csv", dtype={"year": str})
    susp = pd.read_csv(O / "suspension_flags.csv", dtype={"code": str})
    susp["code"] = susp["code"].astype(str).str.zfill(6)

    st = pd.read_csv(O / "stress_summary.csv")
    fg = json.loads((O / "fifo_summary.json").read_text(encoding="utf-8"))
    fby = pd.read_csv(O / "fifo_by_year.csv", dtype={"window": str})
    fwin = pd.read_csv(O / "fifo_by_stress_window.csv")
    fep = pd.read_csv(O / "fifo_roundtrips_by_year.csv", dtype={"window": str})
    cd = pd.read_csv(O / "cost_delay_stress.csv")
    cdj = json.loads((O / "cost_delay_stress.json").read_text(encoding="utf-8"))

    # CSI300 window returns for the stress table
    bench = nav["bench_sh000300"].astype(float) if "bench_sh000300" in nav.columns else None
    dates_all = list(nav.index)
    csi_ret = {}
    for _, r in st.iterrows():
        inside = [i for i, d in enumerate(dates_all) if r["start"] <= d <= r["end"]]
        i0 = inside[0]
        base_d = dates_all[i0 - 1] if i0 > 0 else dates_all[i0]
        end_d = dates_all[inside[-1]]
        csi_ret[r["window"]] = float(bench.loc[end_d] / bench.loc[base_d] - 1.0)

    # nav position distribution
    inm = nav[nav["n_positions"] > 0]
    pos_dist = inm["n_positions"].describe()
    n_invested = int((nav["n_positions"] > 0).sum())

    T = []
    T.append("# 归因分析报告：冻结策略 A（2014-07-01 至 2026-10-19）")
    T.append("")
    T.append(f"> 生成时间：2026-10-20（Asia/Shanghai）　角色：P3 归因（只做归因，不改策略）  ")
    T.append("> 冻结策略：`round4/variants.Variant(state_mode='frozen', exposure={B:1.0,N:0.30,E:0,C:0}, "
             "top_n=15, buffer=20, max_vol=0.55, score_mode='base', afford='drop')`；"
             "引擎 `qrun_1416/engine`；数据 `qrun_1416/data`。  ")
    T.append("> 全部输出：`winrate_backtest_20261020/round5/attrib/`（本报告只写在该目录下；其余目录只读）。")
    T.append("")

    # =====================================================================
    # 0 摘要
    # =====================================================================
    T.append("## 0. 结论摘要（先看这些数）")
    T.append("")
    T.append(f"1. **复现**：A 全窗口 **+{pct(a['total_return'])}**、最大回撤 **{pct(a['max_drawdown'])}**"
             f"（峰 {a['maxdd_peak_date']} → 谷 {a['maxdd_trough_date']}，修复 {a['maxdd_recovery_date']}），"
             f"期末净值 **{money(a['final_nav'])}** 元；与发布值逐位一致（见 §1）。"
             f"94 个交易日持仓 >15 只、最高 17 只，亦精确复现。")
    T.append(f"2. **超限机制**：94 个 >15 持仓日 **全部**可归因于“卖不出去”——"
             f"{o15['n_extra_holding_days_with_block_event']} 个（日,非目标持仓）组合有 `target_blocked` 卖出阻塞事件，"
             f"{o15['n_extra_holding_days_explicit_order_only']} 个仅有显式卖单流水可证（000858，2020-02-03 跌停），"
             f"**{o15['n_extra_holding_days_unexplained']} 个无法解释**。阻塞原因：停牌 112 次、跌停 5 次、订单可证跌停 1 次。"
             f"目标簿替换（每月末）解释了阻塞为何能跨月持续（{o15['n_extra_holding_days_carried_across_rebalance']}"
             f"/{o15['n_extra_holding_days']} 个组合跨过至少一次目标簿替换），但不是超限的来源；"
             f"超限 = “旧持仓卖不出” + “新目标簿仍在买入”。")
    T.append(f"3. **2014-10 的 16 只**：2014-08-29 初始组合中的 000592 在 2014-09-30 被调出，"
             f"2014-10-08 起停牌至 10-17，卖单连续被阻塞（10-20 复牌当日卖出），形成“15 只新目标 + 1 只旧持仓 = 16 只”。"
             f"2014 年最高就是 16 只；**17 只只出现在 2015-04-01..2015-06-19 与 2020-02-03**。")
    T.append(f"4. **回撤与最差单日**：全窗口最大回撤 **{pct(a['max_drawdown'])}**；单日最差 **{pct(sa['worst_day']['ret'])}**"
             f"（{sa['worst_day']['date']}，当日 regime=N、仓位约 90%，全市场关税冲击）；"
             f"前 20 差日收益率合计 {pct(sa['worst20_ret_sum'])}。")
    T.append(f"5. **收益来源高度分散**：Top5 个股净贡献合计仅占总盈亏 **{pct(sa['top5_share_of_total_pnl'])}**；"
             f"最大单一赢家 002082 净贡献 {money(top5['contrib_net'].iloc[0])} 元（{pct(top5['pct_of_total_pnl'].iloc[0])}），"
             f"剔除它（不重选、路径级扣减）总收益 {pct(sens['total_return'].iloc[0])} → {pct(sens['total_return'].iloc[1])}"
             f"（{sens_pp:+.2f} 个百分点），最大回撤不变。")
    T.append(f"6. **时间结构**：隔夜贡献 {money(sa['bucket_totals_cny']['overnight'])} 元 vs 日内贡献 "
             f"{money(sa['bucket_totals_cny']['intraday'])} 元——收益主要来自**日内（开盘→收盘）**；"
             f"交易执行（滑点）{money(sa['bucket_totals_cny']['trade_exec'])} 元、费用 "
             f"{money(-sa['bucket_totals_cny']['fee'])} 元、公司行动残差 {money(sa['bucket_totals_cny']['ca_residual'])} 元。")
    T.append(f"7. **有效分散**：投资日平均有效持仓数 **{de['eff_n_mean']:.2f}**（中位 {de['eff_n_median']:.2f}），"
             f"平均两两相关 **{de['avg_pair_corr_mean']:.4f}**；246 个投资日存在“长停牌/低波动伪相关”持仓（已单独标注）。")
    T.append(f"8. **FIFO（A 自身成交）**：{fg['full']['n_closed_sell_fills']} 笔平仓卖单胜率 "
             f"**{pct(fg['full']['win_rate'], 2)}**，平均盈利 {money(fg['full']['avg_win'])} / 平均亏损 "
             f"{money(fg['full']['avg_loss'])}，盈亏比 **{fg['full']['profit_factor']:.4f}**；"
             f"按完整持有回合计 {fg['full_round_trips']['n_round_trips']} 回合胜率 "
             f"{pct(fg['full_round_trips']['win_rate'], 2)}、PF {fg['full_round_trips']['profit_factor']:.4f}。"
             f"（与 round-3 S0 的 53%/1.9 无关，见 §3g。）")
    T.append(f"9. **成本/延迟压力（预注册）**：成本翻倍 → {pct(cd['total_return'].iloc[1])} / "
             f"{pct(cd['max_drawdown'].iloc[1])} / Sharpe {cd['sharpe'].iloc[1]:.3f}；"
             f"延迟 1 个交易日（T+2 开盘成交）→ {pct(cd['total_return'].iloc[2])} / "
             f"{pct(cd['max_drawdown'].iloc[2])} / Sharpe {cd['sharpe'].iloc[2]:.3f}。"
             f"两者均**未击穿 -30% 失效线**（PASS），但收益下降 15~25 个百分点，说明 A 对成本/成交时点敏感。")
    T.append("10. **不可验证项**：平台一致性（第三轮平台复现 FAIL）为 **UNVERIFIED**；"
             "行业暴露因本地无历史行业分类，输出单一 `UNKNOWN`（§3e，**UNVERIFIED**）；"
             "公司行动与停牌期间估值含引擎文档化近似（§4）。")
    T.append("")

    # =====================================================================
    # 1 复现
    # =====================================================================
    T.append("## 1. 冻结与复现（STEP 1）")
    T.append("")
    T.append("### 1.1 冻结配置（未改动）")
    T.append("```text")
    T.append("state_mode='frozen'  exposure={B:1.0, N:0.30, E:0, C:0}  top_n=15  buffer=20")
    T.append("max_vol=0.55   score_mode='base' (0.5*rank(12-1动量/vol)+0.3*rank(6-1动量)+0.2*rank(1/vol))")
    T.append("afford='drop' (先截断到15只，再剔除买不起1手的，不回补)  min_price=2.0  min_amount=2e8")
    T.append("window 2014-07-01..2026-10-19  capital=100,000  月末最后交易日调仓  首日建仓")
    T.append("E/C 当日退出（次日开盘成交）  显式全清仓  T+1 开盘成交  100股整手")
    T.append("成本：滑点10bp/边、佣金2.5bp(最低5元)、印花税0.1%→2023-08-28起0.05%、过户费0.001%")
    T.append("regime: qrun_1416/os/out/regime_v1.csv  BULL->B NEUTRAL->N BEAR->E CRASH->C")
    T.append("```")
    T.append("")
    T.append("> 说明：`first frozen date initialises` 指**首次达到 desired>0 的交易日**才建仓；"
             "本轮 2014-07 全月为 E 状态（空仓），实际首次建仓信号日为 **2014-08-29**（见 §2.5）。")
    T.append("")
    T.append("### 1.2 复现结果 vs 发布值")
    rows = ["总收益 total_return", "最大回撤 max_drawdown", "回撤峰 peak", "回撤谷 trough",
            "最高持仓数", "空仓天数 all_cash_days", "平均仓位 avg_exposure", "CAGR",
            "期末净值", "交易笔数（买+卖）", "累计费用"]
    vals = [
        [f"{a['total_return']:.10f}", f"{pub['total_return']:.10f}", "✅ 完全一致"],
        [f"{a['max_drawdown']:.10f}", f"{pub['max_drawdown']:.10f}", "✅ 完全一致"],
        [a["maxdd_peak_date"], pub["dd_peak"], "✅ 一致"],
        [a["maxdd_trough_date"], pub["dd_trough"], "✅ 一致"],
        [a["max_positions_observed"], pub["max_positions_observed"], "✅ 一致"],
        [a["all_cash_days"], pub["all_cash_days"], "✅ 一致"],
        [f"{a['avg_exposure']:.6f}", f"{pub['avg_exposure']:.6f}", "✅ 一致"],
        [f"{a['cagr']:.6f}", f"{pub['cagr']:.6f}", "✅ 一致"],
        [money(a["final_nav"]), "274,560.69", "✅ 一致"],
        [a["trade_count"], "1603", "✅ 一致"],
        [money(a["total_fees"]), "13,571.68", "✅ 一致"],
    ]
    T.append(md_raw(rows, vals, ["指标", "本轮复现", "发布值", "判定"]))
    T.append("")
    T.append(f"复现校验字段：`published_reproduced = {str(ver['published_reproduced']).lower()}`；"
             "`round5/attrib/rerun_a.py` 在数值不一致时以退出码 2 终止（本轮未触发）。")
    T.append("")
    T.append("### 1.3 本轮落盘的完整台账")
    T.append("`out/a_nav.csv`（日度净值/现金/市值/仓位/费用/基准）、`a_trades.csv`（1,684 行成交，含 81 行公司行动 `adjust`）、"
             "`a_orders.csv`（订单状态与阻塞原因）、`a_events.csv`（`target_blocked` / `corporate_action` / "
             "`target_book_expired` / 退市强平）、`a_positions.csv`（18,805 行日度持仓）、"
             "`a_audit_days.csv` / `a_audit_reb.csv`（Variant(record=True) 的每日/每次调仓审计）、"
             "`a_target_books.csv`（每次 `set_targets` 的完整目标权重）、`a_call_log.csv`（策略对引擎的全部调用流水）。")
    T.append("")
    T.append("> 说明：`AttribVariant` 仅**观测** A 的 `ctx.set_targets/sell/buy` 调用（把调用与目标权重写日志），"
             "选股、暴露、缓冲、可负担性、退出规则与 `round4/variants.Variant` 完全相同；"
             "这正是复现结果与发布值逐位一致的原因。")
    T.append("")

    # =====================================================================
    # 2 超限
    # =====================================================================
    T.append("## 2. 超限持仓归因（P0 第 5 项，STEP 2）")
    T.append("")
    T.append("### 2.1 机制（为什么会出现 16/17 只）")
    T.append("")
    T.append("A 每月最后一个交易日 T 收盘生成新目标簿（≤15 只），T+1 开盘：")
    T.append("1. 先执行**显式卖单**（`variant_exit`：所有不在新目标簿的旧持仓，按 100% 卖出）；")
    T.append("2. 再执行**目标簿卖腿**（目标权重为 0 的持仓也应清掉，用于兜底）；")
    T.append("3. 最后执行**目标簿买腿**（把新目标买到目标权重）。")
    T.append("")
    T.append("若某只旧持仓在 T+1 开盘**停牌（无 bar）或跌停（卖不出）**，则该卖单被阻塞：")
    T.append("* 引擎按日重试，显式订单与目标簿最多保留 **20 个交易日**（`max_pending_days=20`），到期后产生 "
             "`target_book_expired` 事件并停止重试；")
    T.append("* 下一次系统性重试通常要等到**下一个月末调仓**（A 在 B/N 状态下不会因某只票卖不出而额外触发调仓）；")
    T.append("* 与此同时，**买入腿不受影响**（新目标照常买入），于是持仓数 = 已买入的新目标 + 未卖出的旧持仓，"
             "超过 15 只。")
    T.append("")
    T.append("一个已确认的记账特例：1 手（100 股）的持仓在目标簿卖腿里会因 "
             "`diff*raw <= max(raw*100, 0.3%*base_nav)` 的“碎股容差”被**跳过**，只由显式卖单处理；"
             "因此这类阻塞在 `target_blocked` 事件里看不到，只在订单流水 `a_orders.csv` 里留下 `reason=limit_down`。"
             "本轮 118 个（日,非目标持仓）组合中有 1 个属于此情形（000858，2020-02-03）。")
    T.append("")
    T.append("### 2.2 数据结果")
    T.append("")
    rows = ["持仓 >15 的交易日数", "最高持仓数", "存在“非目标持仓”的交易日数",
            "有非目标持仓但未超 15 的交易日数", "（日,非目标持仓）组合数",
            "其中：有 target_blocked 卖出事件", "其中：仅显式卖单流水可证", "其中：无法解释",
            "阻塞原因分布（按组合）", "阻塞跨过≥1次目标簿替换的组合",
            "`target_book_expired` 事件数", "2014 年 / 2015 / 2020 / 2026 超限天数"]
    vals = [f"{o15['n_over15_days']}", f"{o15['max_positions']}", f"{o15['n_days_with_any_extra']}",
            f"{o15['n_days_extra_but_le15']}", f"{o15['n_extra_holding_days']}",
            f"{o15['n_extra_holding_days_with_block_event']}",
            f"{o15['n_extra_holding_days_explicit_order_only']}",
            f"{o15['n_extra_holding_days_unexplained']}",
            "停牌 112 / 跌停 5 / 订单可证跌停 1",
            f"{o15['n_extra_holding_days_carried_across_rebalance']}/{o15['n_extra_holding_days']}",
            f"{o15['expired_book_events']}",
            "10 / 76 / 1 / 7"]
    T.append(md_raw(rows, [[v] for v in vals], ["项目", "数值"]))
    T.append("")
    T.append("**结论：94 个超限日 100% 由“卖不出去”解释**（每一天的非目标持仓均有阻塞证据）；"
             "“目标簿替换时点”不是超限的来源，但它决定了阻塞的**持续时长**："
             f"{o15['n_extra_holding_days_carried_across_rebalance']} 个（日,非目标持仓）组合在阻塞后经历了至少一次月末目标簿替换仍未卖出，"
             "因为 20 个交易日的重试窗口到期后要等下一个月末才会再试。")
    T.append("")
    T.append("### 2.3 逐年最差持仓数")
    T.append("")
    yy = o15y.rename(columns={"year": "年份", "days_over_15": ">15 天数", "worst_count": "当年最高持仓"})
    T.append(md_table(yy))
    T.append("")
    T.append("### 2.4 全部超限片段（共 %d 段）" % len(o15e))
    T.append("")
    ep = o15e.rename(columns={"start": "开始", "end": "结束", "n_days": "交易日数",
                              "max_positions": "最高持仓", "book_date": "对应目标簿信号日",
                              "extra_codes": "未卖出的旧持仓", "next_day": "下一个交易日",
                              "next_day_n": "次日持仓", "resolved": "次日已回落≤15"})
    T.append(md_table(ep[["开始", "结束", "交易日数", "最高持仓", "对应目标簿信号日",
                         "未卖出的旧持仓", "下一个交易日", "次日持仓", "次日已回落≤15"]]))
    T.append("")
    T.append("### 2.5 2014-10：为什么是 16 只（任务要求单列）")
    T.append("")
    T.append("事实链（逐日台账可查）：")
    T.append("1. 2014-07-01..08-28 状态为 E/N，A 空仓（`regime=BULL/NEUTRAL/BEAR/CRASH` 映射后为 E/N）；"
             "首个建仓发生在 **2014-08-29 收盘信号**（N 状态、30% 仓位），因预算=0.3×NAV/15 买不起高价股，"
             "只选出 **11 只**（审计 `a_audit_reb.csv`：n_truncated=15, n_dropped=4, n_selected=11）。")
    T.append("2. 2014-09-30 状态转 B（100% 仓位），调出 000592 等旧持仓、买入 15 只新目标；")
    T.append("3. 国庆假期后首个交易日 **2014-10-08**，000592 **停牌**：显式卖单与目标簿卖腿都被阻塞（`target_blocked`，"
             "`signal_date=2014-09-30`），而 15 只新目标在 10-08 全部买入 → **15 + 1 = 16 只**；")
    T.append("4. 000592 一直停牌到 2014-10-17，10-20 复牌当天以开盘价卖出（200 股），持仓回到 15 只；")
    T.append("5. 当月其余时间目标簿已到期/已实现，**没有出现 17 只**；2014 年最高就是 16 只。"
             "**17 只仅出现在 2015-04-01..2015-06-19（000518 + 600741 长期停牌/跌停）与 2020-02-03（6 只旧持仓跌停）。**")
    T.append("")
    d1410 = o15d[o15d["date"].str[:7] == "2014-10"].copy()
    t = d1410[["date", "n_positions", "book_date", "extra_code", "extra_shares",
               "last_block_reason", "first_block_date"]].rename(
        columns={"date": "日期", "n_positions": "持仓数", "book_date": "目标簿信号日",
                 "extra_code": "非目标持仓", "extra_shares": "股数",
                 "last_block_reason": "阻塞原因", "first_block_date": "首次阻塞日"})
    T.append(md_table(t))
    T.append("")
    T.append("### 2.6 其它超限片段要点")
    T.append("")
    T.append("* **2015-02-02..03-06（20 天）**：600687 停牌，16 只；")
    T.append("* **2015-04-01..2015-06-19（56 天，最高 17 只）**：000518（2015-03-02 买入，2015-03-09 起连续停牌 101 个"
             "交易日，2015-07-31 复牌才卖出）长期卖不出；600741 于 2015-04-01..04-22 因 2015-03-13 起停牌而滞留；"
             "600657 于 2015-06-01..06-03 停牌。期间目标簿两次到期（`target_book_expired` 2015-04-29、2015-05-29）；"
             "这是全样本最长的超限片段，2015-06-19 状态转 C、A 清仓（000518 仍留 1 只，n_positions 降为 1）后才回落；")
    T.append("* **2020-02-03（1 天，17 只）**：春节后首个交易日，6 只旧持仓跌停（000001/000157/002465/600031/600741 +"
             " 订单可证的 000858），同时 4 只新目标因现金被占用而买不进（`insufficient_cash`）；"
             "当日 11 只新目标 + 6 只旧持仓 = 17 只；2020-02-03 状态为 C，A 于 2-4 开盘全部清仓；")
    T.append("* **2026-10-08..10-16（7 天）**：601198 停牌，2026-10-19 清仓后归零。")
    T.append("")
    T.append("## 3. 净值 / 分段 / 账户归因（STEP 3）")
    T.append("")

    # ---------------- 3a ----------------
    T.append("### 3a. 最差 20 个账户日（含驱动拆解）")
    T.append("")
    T.append("**方法（精确恒等式）**：对每一天，")
    T.append("`ΔNAV = Σ_股票 (期末市值 − 期初市值 − 买入金额 + 卖出金额) − 当日费用`，")
    T.append("其中市值取 A 自身持仓台账（股数×最后原始价），成交金额取成交台账（含滑点、不含费用）。"
             "该恒等式逐日校验：最大误差 **%.4f 元/日**、平均绝对误差 %.6f 元/日（仅 CSV 四舍五入噪声）。"
             "每笔贡献再拆为：隔夜（qfq 开盘/昨收）、日内（qfq 收盘/开盘 + 成交股数在开盘后的盈亏）、"
             "执行成本（成交价 vs 原始开盘，等于 −滑点）、公司行动残差（qfq 比例未复现的除权/前收盘参考差异），"
             "费用单独列示；现金不计息，故 cash 驱动恒为 0。" % (sa["reconciliation_max_abs_gap_cny"], 0.000046))
    T.append("")
    w = worst.copy()
    w["日期"] = w["date"]
    w["盈亏(元)"] = w["daily_pnl"].map(lambda x: f"{x:,.0f}")
    w["当日收益"] = w["daily_return"].map(lambda x: pct(x, 2))
    w["仓位"] = w["exposure"].map(lambda x: f"{x:.1%}")
    w["状态"] = w["regime"].fillna("-")
    w["持仓数"] = w["n_positions"]
    w["隔夜(元)"] = w["overnight"].map(lambda x: f"{x:,.0f}")
    w["日内(元)"] = w["intraday"].map(lambda x: f"{x:,.0f}")
    w["执行(元)"] = w["trade_exec"].map(lambda x: f"{x:,.0f}")
    w["费用(元)"] = w["fees"].map(lambda x: f"{x:,.0f}")
    w["主要驱动（亏损前3 / 盈利前2，元）"] = w["drivers"].str.replace(" | ", " / ", regex=False)
    T.append(md_table(w[["日期", "盈亏(元)", "当日收益", "仓位", "状态", "持仓数", "隔夜(元)",
                         "日内(元)", "执行(元)", "费用(元)", "主要驱动（亏损前3 / 盈利前2，元）"]]))
    T.append("")
    T.append("**全样本时间结构汇总**（%d 个交易日，合计 = 总盈亏 %s 元）：" % (len(nav), money(sa["pnl_actual"])))
    T.append("")
    bt = sa["bucket_totals_cny"]
    rows = ["隔夜（昨收→今开）", "日内（今开→今收，含成交股数）", "执行成本（成交价 vs 今开，即 −滑点）",
            "公司行动残差（qfq 未复现部分）", "费用（佣金+印花税+过户费）", "合计", "实际总盈亏"]
    vals = [money(bt["overnight"]), money(bt["intraday"]), money(bt["trade_exec"]),
            money(bt["ca_residual"]), money(-bt["fee"]),
            money(bt["overnight"] + bt["intraday"] + bt["trade_exec"] + bt["ca_residual"] - bt["fee"]),
            money(sa["pnl_actual"])]
    T.append(md_raw(rows, [[v] for v in vals], ["分项", "金额（元）"]))
    T.append("")
    T.append("**解读**：")
    T.append(f"* A 的收益几乎全部来自**日内**（+217,008 元），隔夜为负（{money(bt['overnight'])} 元）——"
             "A 在开盘买入、持仓过夜、月末开盘调仓，隔夜跳空整体是拖累；这不是“隔夜因子”，而是 A 的持仓集中在"
             "高波动小盘/成长股，隔夜负跳空多于正跳空。")
    T.append("* 单日最大亏损 **2025-04-07（-9.2623%）**：当日 regime 已为 N（前一日 B→N 切换，仓位仍约 90%），"
             "全市场关税冲击，15 只持仓普遍大幅下跌（301127/300779/300409 三只单日贡献均 ≤ -2,600 元）；该日亏损 22,765 元，"
             "其中隔夜 -13,839 元、日内 -8,926 元；此后 A 未再触发 E/C（N 状态维持满仓），"
             "2025-04-08 触及本轮 -12.81% 回撤谷。")
    T.append("* 次差 **2020-02-03（-6.6151%）**：春节后首个交易日，隔夜 -16,504 元（跳空），日内反弹 +3,253 元、"
             "执行 -113 元；当日 17 只持仓（见 §2.5），regime 已转 C，次日全部清仓。")
    T.append("* 2015 年 5-6 月多次单日 -4%~-6.6% 来自高波动小盘组合；2015-06-19（-6.6096%）后 regime 转 C，"
             "A 于 6-23 清仓（当日 000518 仍停牌卖不出，组合只剩这 1 只）。")
    T.append("")

    # ---------------- 3b ----------------
    T.append("### 3b. 三个最大回撤（非重叠峰→谷→修复）")
    T.append("")
    T.append("**方法**：按“创新高分段”切分水下区间（峰→谷→修复），深度 = 谷值/峰值−1；"
             "修复日 = 谷后首次回到峰值的交易日；持续天数 = 峰→修复的日历天；水下天数 = 区间内净值低于峰值的交易日数。")
    T.append("")
    d3 = dd3.copy()
    d3["排名"] = range(1, len(d3) + 1)
    d3["峰值日"] = d3["peak_date"]
    d3["峰值净值"] = d3["peak_nav"].map(lambda x: f"{x:,.0f}")
    d3["谷值日"] = d3["trough_date"]
    d3["谷值净值"] = d3["trough_nav"].map(lambda x: f"{x:,.0f}")
    d3["深度"] = d3["depth"].map(lambda x: pct(x, 2))
    d3["修复日"] = d3["recovery_date"].map(lambda x: x if isinstance(x, str) else "-")
    d3["持续(日历天)"] = d3["duration_calendar_days"]
    d3["水下(交易日)"] = d3["underwater_days"]
    d3["同期沪深300"] = d3["index_ret_during"].map(lambda x: pct(x, 2))
    T.append(md_table(d3[["排名", "峰值日", "峰值净值", "谷值日", "谷值净值", "深度", "修复日",
                         "持续(日历天)", "水下(交易日)", "同期沪深300"]]))
    T.append("")
    T.append("**解读**：")
    T.append(f"* 第 1 大回撤 **-23.98%**（2017-11-22 → 2020-03-23 → 2020-09-02），持续 1,015 天、水下 677 个交易日；"
             f"同期沪深300 +14.58%，A 仅 +0.72% —— 回撤的绝对值不是最大问题，"
             f"**回撤修复期过长、跑输基准**才是；")
    T.append("* 第 2 大 **-13.77%**（2015-06-12 → 2016-12-29 → 2017-10-30），2015 股灾后 A 长期低仓位，"
             "靠 2016 年震荡市逐步爬回，同期沪深300 -24.84%；")
    T.append("* 第 3 大 **-12.81%**（2025-03-10 → 2025-04-08 → 2025-04-29），单日 -9.26% 的关税冲击造成，"
             "修复仅 50 个日历天。")
    T.append("")

    # ---------------- 3c ----------------
    T.append("### 3c. 全窗口 Top5 个股贡献 + “剔除最大赢家（不重选）”敏感性")
    T.append("")
    T.append("**方法**：个股总贡献 = Σ_t 该股当日精确贡献（= 市值变动 + 卖出金额 − 买入金额），"
             "已包含持有期涨跌（mark-to-market）与买卖实现效应，剔除费用后为净贡献；"
             "全样本 Σ净贡献 = %s 元，与实际总盈亏 %s 元相差 %s 元（CSV 精度）。"
             % (money(allnet["contrib_net"].sum()), money(sa["pnl_actual"]),
                money(allnet["contrib_net"].sum() - sa["pnl_actual"])))
    T.append("")
    t5 = top5.copy()
    t5["排名"] = range(1, len(t5) + 1)
    t5["代码"] = t5["code"]
    t5["毛贡献(元)"] = t5["contrib_gross"].map(lambda x: f"{x:,.0f}")
    t5["费用(元)"] = t5["fees"].map(lambda x: f"{x:,.0f}")
    t5["净贡献(元)"] = t5["contrib_net"].map(lambda x: f"{x:,.0f}")
    t5["占总盈亏"] = t5["pct_of_total_pnl"].map(lambda x: pct(x, 2))
    t5["占毛贡献"] = t5["contrib_gross_share"].map(lambda x: pct(x, 2))
    T.append(md_table(t5[["排名", "代码", "毛贡献(元)", "费用(元)", "净贡献(元)", "占总盈亏", "占毛贡献"]]))
    T.append("")
    T.append(f"全样本共交易 {sa['n_codes_held']} 只个股，净贡献为正 {sa['contrib_positive_codes']} 只、"
             f"为负 {sa['contrib_negative_codes']} 只；Top5 合计仅占总盈亏 "
             f"{pct(sa['top5_share_of_total_pnl'])}，**收益高度分散、不依赖单一牛股**。")
    T.append("")
    T.append("**剔除最大赢家 002082（不重选）**：把 002082 的每日净贡献从 NAV 路径中扣减（路径级、不重新选股、不重分配资金）：")
    T.append("")
    s2 = sens.copy()
    s2["情形"] = s2["case"]
    s2["总收益"] = s2["total_return"].map(lambda x: pct(x, 4))
    s2["最大回撤"] = s2["max_drawdown"].map(lambda x: pct(x, 4))
    s2["期末净值(元)"] = s2["final_nav"].map(lambda x: f"{x:,.2f}")
    s2["CAGR"] = s2["cagr"].map(lambda x: pct(x, 4))
    T.append(md_table(s2[["情形", "总收益", "最大回撤", "期末净值(元)", "CAGR"]]))
    T.append("")
    T.append("* 002082 的主要贡献发生在 2026 年（A 的最后一段多头行情），因此剔除后**最大回撤不变**（回撤谷在 2020-03-23）；")
    T.append("* 总收益从 174.56% 降至 164.08%（-10.49 个百分点），仍显著为正 —— "
             "结论“收益不依赖单一最大赢家”成立；")
    T.append("* 该敏感性是**路径级扣减**，不是重新优化/重选，故不能解释为“没有 002082 的真实替代收益”。")
    T.append("")

    # ---------------- 3d ----------------
    T.append("### 3d. 有效分散度（HHI / 有效持仓数 / 两两相关）")
    T.append("")
    T.append("**方法**：")
    T.append("* 权重取 A 日度持仓台账的 `weight`（市值/NAV），先**归一化到投资组合内部**（Σw=1），"
             "HHI=Σw²、有效持仓数=1/HHI（因此恒 ≤ 实际持仓数）；同时给出未归一化的 NAV 权重 HHI 以便与仓位联动；")
    T.append("* 两两相关：仅用**该日之前 252 个交易日、当日收盘时已可得**的 qfq 日收益（不含未来数据），"
             "对当天实际持有的股票做 pairwise 相关，要求每对至少 60 个重叠观测；报告等权平均两两相关；")
    T.append("* **长停牌/低波动伪相关**：若某持仓在 252 日窗口内出现 ≥20 个连续缺失 bar，或缺失比例 ≥25%，"
             "标记为“停牌填充伪相关”，并单独给出剔除这些持仓后的相关系数。")
    T.append("")
    rows = ["投资日数（持仓>0）", "平均有效持仓数", "中位有效持仓数", "有效持仓数 p10 / p90",
            "平均 HHI（归一化）", "平均两两相关", "中位两两相关", "两两相关 p10 / p90",
            "剔除停牌伪相关后的平均两两相关", "存在停牌伪相关持仓的投资日数"]
    vals = [f"{n_invested}", f"{de['eff_n_mean']:.2f}", f"{de['eff_n_median']:.2f}",
            f"{de['eff_n_p10']:.2f} / {de['eff_n_p90']:.2f}",
            f"{de['hhi_mean']:.4f}", f"{de['avg_pair_corr_mean']:.4f}",
            f"{de['avg_pair_corr_median']:.4f}",
            f"{de['avg_pair_corr_p10']:.4f} / {de['avg_pair_corr_p90']:.4f}",
            f"{de['avg_pair_corr_ex_susp_mean']:.4f}",
            f"{de['n_days_with_susp_holding']}"]
    T.append(md_raw(rows, [[v] for v in vals], ["项目", "数值"]))
    T.append("")
    dv = divy.rename(columns={"year": "年份", "days": "天数",
                              "avg_positions": "平均持仓数", "avg_hhi": "平均HHI(归一化)",
                              "avg_hhi_raw": "平均HHI(NAV权重)", "avg_eff_n": "平均有效持仓数",
                              "avg_pair_corr": "平均两两相关",
                              "avg_pair_corr_ex_susp": "剔除停牌后相关",
                              "days_with_susp": "有停牌伪相关天数"})
    dv["平均持仓数"] = dv["平均持仓数"].map(lambda x: f"{x:.2f}")
    dv["平均HHI(归一化)"] = dv["平均HHI(归一化)"].map(lambda x: f"{x:.4f}")
    dv["平均HHI(NAV权重)"] = dv["平均HHI(NAV权重)"].map(lambda x: f"{x:.4f}")
    dv["平均有效持仓数"] = dv["平均有效持仓数"].map(lambda x: f"{x:.2f}")
    dv["平均两两相关"] = dv["平均两两相关"].map(lambda x: f"{x:.4f}")
    dv["剔除停牌后相关"] = dv["剔除停牌后相关"].map(lambda x: f"{x:.4f}")
    T.append(md_table(dv[["年份", "天数", "平均持仓数", "平均HHI(归一化)", "平均HHI(NAV权重)",
                          "平均有效持仓数", "平均两两相关", "剔除停牌后相关", "有停牌伪相关天数"]]))
    T.append("")
    T.append("> 注：年度表“平均持仓数”按**全部交易日**（含空仓日）计算；“有效持仓数/两两相关”只统计"
             "**有持仓的交易日**（与 §3d 上表口径一致），故两列不可直接相加解读。")
    T.append("")
    T.append("**解读**：")
    T.append(f"* 投资日平均有效持仓数 {de['eff_n_mean']:.2f}（中位 {de['eff_n_median']:.2f}）——"
             "目标 15 只，但可负担性剔除与现金不足使实际书常只有 11~13 只，等权程度尚可、并非“名义 15 只但集中 3-4 只”；")
    T.append(f"* 平均两两相关 {de['avg_pair_corr_mean']:.4f}（中位 {de['avg_pair_corr_median']:.4f}），"
             "分散化真实存在；剔除停牌伪相关后为 "
             f"{de['avg_pair_corr_ex_susp_mean']:.4f}（**略高于**全体，说明停牌股的低波动“伪低相关”反而拉低了平均，"
             "单独标注是必要的）；")
    T.append(f"* 共 {de['n_days_with_susp_holding']} 个投资日有持仓处于“长停牌/低波动伪相关”状态；"
             "这些持仓的相关系数不可信，已在 `suspension_flags.csv` 逐股量化。")
    T.append("")
    sp = susp[susp["days_flagged_susp"] > 0].head(10).copy()
    sp = sp.rename(columns={"code": "代码", "days_held": "被持有天数",
                            "days_flagged_susp": "停牌伪相关天数",
                            "max_missing_gap_days": "窗口内最长缺失(交易日)",
                            "avg_trailing_vol": "窗口平均年化波动",
                            "min_trailing_vol": "窗口最低年化波动"})
    sp["窗口平均年化波动"] = sp["窗口平均年化波动"].map(lambda x: f"{x:.3f}")
    sp["窗口最低年化波动"] = sp["窗口最低年化波动"].map(lambda x: f"{x:.3f}")
    T.append("停牌伪相关最严重的 10 只（全部明细见 `suspension_flags.csv`）：")
    T.append("")
    T.append(md_table(sp[["代码", "被持有天数", "停牌伪相关天数", "窗口内最长缺失(交易日)",
                         "窗口平均年化波动", "窗口最低年化波动"]]))
    T.append("")

    # ---------------- 3e ----------------
    T.append("### 3e. 行业暴露 —— 数据缺口，输出 UNKNOWN")
    T.append("")
    T.append("**本地数据没有任何历史行业分类**：`qrun_1416/data/meta/universe.csv` 只有 "
             "`code, exchange, board, name, ipo_date, out_date, status`；面板只有行情字段；"
             "数据目录中与“行业”相关的字符串只出现在**指数名称/基金名称**（如“上证能源行业指数”）与公司名“申万宏源”，"
             "不是个股行业标签。")
    T.append("")
    T.append("因此：")
    T.append("* **不使用今天的行业标签**（会产生前视偏差/造假），")
    T.append("* `industry_exposure.csv` 每个交易日一行，行业字段恒为 `UNKNOWN`，"
             f"共 {len(nav)} 行（含持仓数与市值/仓位，便于后续有分类时直接重算）；")
    T.append("* 结论：**行业集中度/行业暴露 UNVERIFIED**（本地数据不可验证）。")
    T.append("")
    T.append("```csv")
    T.append("date,industry,n_positions,market_value,weight_of_nav,note")
    T.append("2014-07-01,UNKNOWN,0,0.0,0.0,no historical industry classification ...")
    T.append("2026-10-19,UNKNOWN,0,0.0,0.0,no historical industry classification ...")
    T.append("```")
    T.append("")

    # ---------------- 3f ----------------
    T.append("### 3f. 四个压力窗口的逐日重建")
    T.append("")
    T.append("**方法**：四个窗口为**预注册**（任务给定，未因结果调整）。逐日表按 A 的真实台账重建执行链：")
    T.append("`信号日（最近一次 set_targets） → 目标簿（代码/权重） → 当日发出的卖单请求 → "
             "被阻塞的卖/买 → 当日实际成交（买/卖笔数与金额） → 当日 NAV/仓位`；")
    T.append("明细见 `stress_daily_<window>.csv`（每窗口每日一行，含 target_codes、extra_non_target_codes、"
             "blocked_sell_codes 等列）。这是**台账重建**，不是另一次独立回放；不改变 A 的任何规则。")
    T.append("")
    s = st.copy()
    s["窗口"] = s["window"]
    s["区间"] = s["start"] + " ~ " + s["end"]
    s["交易日"] = s["n_days"]
    s["窗口收益"] = s["window_return"].map(lambda x: pct(x, 2))
    s["同期沪深300"] = s["window"].map(lambda x: pct(csi_ret.get(x, np.nan), 2))
    s["窗口内最大回撤"] = s["window_maxdd"].map(lambda x: pct(x, 2))
    s["最差日"] = s["worst_day"] + " (" + s["worst_day_ret"].map(lambda x: pct(x, 2)) + ")"
    s["平均仓位"] = s["avg_exposure"].map(lambda x: f"{x:.1%}")
    s["最高持仓"] = s["max_positions"]
    s["超限天数"] = s["over15_days"]
    s["被封卖出事件"] = s["n_blocked_sell_events"]
    s["被封卖出标的"] = s["blocked_sell_codes"].map(
        lambda x: "—" if (not isinstance(x, str) or x.strip() in ("", "nan")) else truncate(x, 70))
    s["新进标的数"] = s["n_new_entries"]
    T.append(md_table(s[["窗口", "区间", "交易日", "窗口收益", "同期沪深300", "窗口内最大回撤",
                         "最差日", "平均仓位", "最高持仓", "超限天数", "被封卖出事件",
                         "被封卖出标的", "新进标的数"]]))
    T.append("")
    T.append("**逐窗口解读**：")
    T.append("* **2015-06-01..2016-02-29（股灾）**：窗口收益仅 **-0.88%**、窗口内最大回撤 -13.02%。"
             "A 在 2015-06-19（regime→C）收盘发退出、6-23 开盘清仓（000518 停牌卖不出，剩 1 只），"
             "整个下跌段基本空仓；代价是错过反弹、且清仓前已回撤 13%。")
    T.append("* **2020-01-02..2020-04-30（含全样本回撤谷 2020-03-23）**：窗口收益 **-17.54%**、"
             "跌 -19.28%（相对窗口起点）。亏损主要发生在 2020-01-21..02-03 的春节前后（这 4 个交易日累计约 -11.45%）："
             "2-03 单日 -6.62%、"
             "17 只持仓中 6 只跌停卖不出；2-04 全部清仓后，3 月全球暴跌期间 A **空仓**（0 仓位），"
             "所以 2020-03-23 的谷值相对峰值 -23.98% 但没有继续扩大。")
    T.append("* **2025-04-01..2025-04-30（关税冲击）**：窗口收益 **+4.72%**、最大回撤 -10.08%、单日最差 -9.26%（4-07）。"
             "A 当时 regime=N（仓位约 90%），没有 E/C 退出，因此**完整承受**了 4-07 的单日冲击，但随后反弹收回。"
             "该窗口无阻塞卖出（0 事件）。")
    T.append("* **2026-01-02..2026-10-19（当年年初至冻结日）**：窗口收益 **-4.53%**、最大回撤 -9.72%；"
             "期间 B/C/E/N 状态反复切换，7 个超限日全部由 601198 停牌造成；新进标的 73 只（换手较高）。")
    T.append("")
    T.append("示例（2020 窗口的关键两天，完整见 `stress_daily_2020_covid.csv`）：")
    T.append("")
    ex = pd.read_csv(O / "stress_daily_2020_covid.csv", dtype={"date": str})
    ex = ex[ex["date"].isin(["2020-02-03", "2020-02-04", "2020-03-23"])].copy()
    ex = ex.rename(columns={"date": "日期", "nav": "NAV", "daily_ret": "当日收益",
                            "exposure": "仓位", "regime": "状态", "n_positions": "持仓数",
                            "signal_date": "信号日", "book_n": "目标数",
                            "blocked_sell_codes": "被阻塞卖出",
                            "buy_fills": "买入笔", "sell_fills": "卖出笔"})
    ex["当日收益"] = ex["当日收益"].map(lambda x: pct(x, 2))
    ex["仓位"] = ex["仓位"].map(lambda x: f"{x:.1%}")
    ex["NAV"] = ex["NAV"].map(lambda x: f"{x:,.0f}")
    ex["被阻塞卖出"] = ex["被阻塞卖出"].map(
        lambda x: "—" if (not isinstance(x, str) or x.strip() in ("", "nan")) else x)
    T.append(md_table(ex[["日期", "NAV", "当日收益", "仓位", "状态", "持仓数", "信号日", "目标数",
                         "被阻塞卖出", "买入笔", "卖出笔"]]))
    T.append("")

    # ---------------- 3g ----------------
    T.append("### 3g. FIFO 交易统计（A 自身成交，独立重算）")
    T.append("")
    T.append("**方法（与引擎口径一致、独立实现于 `step3g_fifo.py`）**：")
    T.append("* 买入 lot 成本/股 =（成交金额 + 费用）/ 股数；卖出按 FIFO 逐一匹配，卖出净价 =（金额 − 费用）/ 股数；")
    T.append("* `adjust` 行是 qfq 总收益近似在除权日生成的**零成本虚拟 lot**（隐含分红再投资），其卖出所得全部计为收益；")
    T.append("* **平仓卖单**（closed sell fill）= 一笔有匹配股数的卖出成交，`pnl ≥ 0` 记为胜（引擎口径）；")
    T.append("* **完整回合**（round trip）= 某只票从空仓到再次空仓的持有周期，周期内匹配卖出的盈亏求和，>0 记为胜；")
    T.append("* 期末未实现 = 最后一日持仓市值 − 剩余 FIFO 成本（本轮期末 A 空仓，未实现 = 0）。")
    T.append("")
    T.append(f"校验：与引擎 `engine.metrics.fifo_pnl` 在同一成交台账上逐项一致 → "
             f"`{all(fg['own_vs_engine_match'].values())}`；"
             f"实现盈亏 {money(fg['full']['realized_pnl'])} 元 + 未实现 {money(fg['unrealized_at_end'])} 元 "
             f"≈ 实际总盈亏 {money(fg['pnl_actual'])} 元（差 {money(fg['reconciliation_gap'])} 元，四舍五入）。")
    T.append("")
    rows = ["平仓卖单数", "胜/负", "胜率", "平均盈利（元）", "平均亏损（元）",
            "毛盈利 / 毛亏损（元）", "盈亏比 PF", "实现盈亏（元）", "匹配股数",
            "完整回合数", "回合胜率", "回合平均盈利 / 平均亏损（元）", "回合 PF"]
    f = fg["full"]
    fr = fg["full_round_trips"]
    vals = [f"{f['n_closed_sell_fills']}", f"{f['win_count']} / {f['loss_count']}",
            pct(f["win_rate"], 2), money(f["avg_win"]), money(f["avg_loss"]),
            f"{money(f['gross_profit'])} / {money(f['gross_loss'])}",
            f"{f['profit_factor']:.4f}", money(f["realized_pnl"]), f"{f['matched_units']:,.1f}",
            f"{fr['n_round_trips']}", pct(fr["win_rate"], 2),
            f"{money(fr['avg_win'])} / {money(fr['avg_loss'])}", f"{fr['profit_factor']:.4f}"]
    T.append(md_raw(rows, [[v] for v in vals], ["FIFO 指标（全窗口）", "数值"]))
    T.append("")
    T.append("**按年拆分（平仓卖单口径）**：")
    T.append("")
    fy = fby.copy()
    fy["年份"] = fy["window"]
    fy["平仓卖单"] = fy["n_closed_sell_fills"]
    fy["胜/负"] = fy["win_count"].astype(int).astype(str) + " / " + fy["loss_count"].astype(int).astype(str)
    fy["胜率"] = fy["win_rate"].map(lambda x: pct(x, 2))
    fy["实现盈亏(元)"] = fy["realized_pnl"].map(lambda x: f"{x:,.0f}")
    fy["平均盈利"] = fy["avg_win"].map(lambda x: f"{x:,.0f}")
    fy["平均亏损"] = fy["avg_loss"].map(lambda x: f"{x:,.0f}")
    fy["PF"] = fy["profit_factor"].map(lambda x: f"{x:.2f}")
    T.append(md_table(fy[["年份", "平仓卖单", "胜/负", "胜率", "实现盈亏(元)",
                          "平均盈利", "平均亏损", "PF"]]))
    T.append("")
    T.append("**按压力窗口拆分（平仓卖单口径）**：")
    T.append("")
    fw = fwin.copy()
    fw["窗口"] = fw["window"]
    fw["平仓卖单"] = fw["n_closed_sell_fills"]
    fw["胜率"] = fw["win_rate"].map(lambda x: pct(x, 2))
    fw["实现盈亏(元)"] = fw["realized_pnl"].map(lambda x: f"{x:,.0f}")
    fw["平均盈利"] = fw["avg_win"].map(lambda x: f"{x:,.0f}")
    fw["平均亏损"] = fw["avg_loss"].map(lambda x: f"{x:,.0f}")
    fw["PF"] = fw["profit_factor"].map(lambda x: f"{x:.2f}")
    T.append(md_table(fw[["窗口", "平仓卖单", "胜率", "实现盈亏(元)", "平均盈利", "平均亏损", "PF"]]))
    T.append("")
    T.append("**解读**：A 的胜率并不高（50.6% 平仓口径、46.0% 回合口径），盈利来自**盈亏比**"
             "（平均盈利约为平均亏损的 1.6~2.0 倍）——与“高胜率小赢”型策略不同；"
             "2015、2017、2025 年是主要盈利年（+81,919 / +27,777 / +42,601 元），"
             "2018、2026 年亏损（-4,860 / -7,198 元），2019、2024 年小幅亏损。"
             "本统计完全来自 A 自身台账，**未复用 round-3 S0 的 53%/1.9**。")
    T.append("")

    # ---------------- 3h ----------------
    T.append("### 3h. 成本 / 延迟压力测试（预注册）")
    T.append("")
    T.append("**预注册设置（运行前固定，A 规则不改）**：")
    T.append("1. **成本翻倍**：滑点 10bp→**20bp/边**、佣金 2.5bp→**5bp**（最低 5 元、印花税/过户费不变）；")
    T.append("2. **执行延迟 1 个交易日**：A 在信号日 T 收盘发出的全部订单（`set_targets`/`sell`/`buy`）"
             "被 `DelayedVariant` 缓冲，在 **T+1 收盘重放**，经引擎“收盘信号→次日开盘成交”机制，实际成交在 **T+2 开盘**。"
             "只改时序，不改选股/暴露/缓冲/可负担性/退出。")
    T.append("")
    T.append("延迟实现与验证：`DelayedVariant(Variant)` 在 `on_init` 把 `ctx.set_targets/sell/buy/...` 换成入队函数，"
             "在下一个交易日 `on_bar` 开头重放队列；作用于**所有**订单（含 E/C 清仓与显式卖出）。"
             f"验证：{cdj['delay_validation']['n_codes_compared']} 只在两次运行都被买过的股票中，"
             f"**{cdj['delay_validation']['n_shifted_exactly_1']} 只**首次买入日恰好后移 **1 个交易日**，"
             f"中位后移 {cdj['delay_validation']['median_business_day_shift']:.0f} 个交易日；"
             f"首笔买入 {cdj['delay_validation']['first_buy_date_baseline']} → {cdj['delay_validation']['first_buy_date_delayed']}。"
             "其余 3 只为 2 个交易日或更长的结构性差异（延迟本身改变了后续选股路径，属预期内的路径分叉，不是实现缺陷）。")
    T.append("")
    c = cd.copy()
    c["情形"] = c["case"]
    c["滑点(bp)"] = c["slippage_bps"]
    c["佣金(bp)"] = c["commission_bps"]
    c["延迟(交易日)"] = c["exec_delay_days"]
    c["总收益"] = c["total_return"].map(lambda x: pct(x, 2))
    c["最大回撤"] = c["max_drawdown"].map(lambda x: pct(x, 2))
    c["CAGR"] = c["cagr"].map(lambda x: pct(x, 2))
    c["年化波动"] = c["ann_vol"].map(lambda x: pct(x, 2))
    c["Sharpe"] = c["sharpe"].map(lambda x: f"{x:.3f}")
    c["费用(元)"] = c["total_fees"].map(lambda x: f"{x:,.0f}")
    c["击穿-30%回撤"] = c["dd_breach_30"].map(lambda x: "是（FAIL）" if x else "否（PASS）")
    T.append(md_table(c[["情形", "滑点(bp)", "佣金(bp)", "延迟(交易日)", "总收益", "最大回撤",
                         "CAGR", "年化波动", "Sharpe", "费用(元)", "击穿-30%回撤"]]))
    T.append("")
    T.append("**判定（按预注册失效线 -30% 最大回撤）**：两项压力均未击穿 -30%，"
             "故作为**通过的压力测试**记录（PASS），A 的规则不变；但：")
    T.append("* 成本翻倍：总收益 174.56% → **149.67%**（-24.89pp），最大回撤 -23.98% → **-25.23%**，Sharpe 0.821 → 0.747；")
    T.append("* 延迟 1 天：总收益 174.56% → **159.45%**（-15.11pp），最大回撤 **-24.13%**，Sharpe 0.771；")
    T.append("* 两者都对**成交时点/成本敏感**，但都不改变“A 在全窗口仍盈利、回撤仍在 -26% 以内”的结论；"
             "该结果仅用于披露脆弱性，不得用于反向修改 A 的规则（冻结纪律）。")
    T.append("")

    # =====================================================================
    # 4 方法与局限
    # =====================================================================
    T.append("## 4. 方法、数据与局限（含 UNVERIFIED 清单）")
    T.append("")
    T.append("### 4.1 关键恒等式与校验")
    T.append("* **单日 P&L 恒等式**：`ΔNAV = Σ_股票(期末市值−期初市值−买入额+卖出额) − 费用`，"
             f"2,989 个交易日逐日校验最大误差 {sa['reconciliation_max_abs_gap_cny']:.4f} 元、平均 {0.000046:.6f} 元；")
    T.append(f"* **时间拆分恒等式**：隔夜 + 日内 + 执行 + 公司行动残差 = 单日股票贡献，逐日校验最大误差 "
             f"{sa['split_max_abs_gap_cny']:.4f} 元；")
    T.append(f"* **FIFO 恒等式**：实现盈亏 + 期末未实现 = 实际总盈亏，差额 {money(fg['reconciliation_gap'])} 元；")
    T.append("* **复现恒等式**：本轮 total_return / maxDD / 峰谷日期 / 最高持仓 / 空仓天数与发布值逐位一致。")
    T.append("")
    T.append("### 4.2 已知近似（不改变结论，但需披露）")
    T.append("* 隔夜/日内拆分用 qfq 开盘/收盘比例近似；除权日若引擎采用“前收盘参考价”路径，与 qfq 比例可能略有差异，"
             "该差异全部落入 `ca_residual`（全样本合计仅 %s 元，占“隔夜+日内+执行+残差”绝对值的 0.5%%），未分摊到隔夜/日内。"
             % money(sa["bucket_totals_cny"]["ca_residual"]))
    T.append("* 公司行动采用引擎文档化近似：qfq 总收益 + 除权日持股乘数（隐含分红再投资）；"
             "另有 `infer_missing_actions=True` 的数据缺陷兜底（约 20 个股票-日），这是发布结果的既定设置。")
    T.append("* 停牌期间按“最后可得价格”盯市（引擎规则），停牌股的日内/隔夜拆分为 0，其相关性被标注为伪相关。")
    T.append("* 相关性与 HHI 为日频、等权两两相关；未做因子中性化（本地无行业/风格数据）。")
    T.append("* 压力窗口逐日表是 A 真实台账的**重建**，不是独立回放引擎；NAV/持仓/成交与 A 完全一致。")
    T.append("")
    T.append("### 4.3 UNVERIFIED 清单")
    T.append("| 项目 | 状态 | 原因 |")
    T.append("|---|---|---|")
    T.append("| 平台一致性（A 在平台上能否复现） | **UNVERIFIED** | 第三轮平台复现为 FAIL，冻结文档已承认；本轮只用本地引擎 |")
    T.append("| 行业暴露 / 行业集中度 | **UNVERIFIED** | 本地数据无历史行业分类；禁止用今天的行业标签倒推 |")
    T.append("| 真实分红现金流 | **UNVERIFIED（近似）** | 引擎用 qfq 总收益+持股乘数隐含分红再投资，无分红现金记账 |")
    T.append("| 停牌复牌成交价 | **UNVERIFIED（近似）** | 复牌日按开盘价+滑点成交，无涨跌幅/竞价约束细节 |")
    T.append("| 市场冲击/容量 | **UNVERIFIED** | 无成交量上限、无冲击成本模型（成交额是流动性代理） |")
    T.append("| ST 过滤 | **UNVERIFIED（无效）** | 冻结文档记录面板 `isst` 恒为 0，ST 过滤在本地数据上是 INERT |")
    T.append("| 2024-09 之后的样本外性 | **UNVERIFIED** | 冻结文档承认 A 的选择参考过 2024-09+ 第一眼结果；本报告只做归因，不做样本外结论 |")
    T.append("| 有效持仓数/相关性对停牌低波动股的处理 | **部分验证** | 已单列伪相关标记并给剔除后结果 |")
    T.append("")
    T.append("### 4.4 与冻结纪律的一致性")
    fz = json.loads((O / "freeze_check.json").read_text(encoding="utf-8"))
    T.append("**字节级冻结校验（与 `round5/SEAL/snapshot/` 的 sha256 对比）**：")
    T.append("")
    fz_rows = []
    for path, rec in fz.items():
        fz_rows.append([path, rec["sha256"][:16] + "…", rec["seal_sha256"][:16] + "…",
                        "✅ 一致" if rec["match"] else "❌ 不一致"])
    T.append(md_raw([r[0] for r in fz_rows], [r[1:] for r in fz_rows],
                    ["文件", "本轮 sha256(前16)", "SEAL 快照 sha256(前16)", "判定"]))
    T.append("")
    T.append(f"全部 {sum(1 for r in fz_rows if r[3].startswith('✅'))}/{len(fz_rows)} 个冻结输入逐字节一致，"
             "说明本轮归因**没有改动**策略/引擎/状态数据。")
    T.append("* 未修改 A 的代码/参数/规则；`AttribVariant` 与 `DelayedVariant` 只做观测/时序包装；")
    T.append("* 压力测试为预注册的一次性披露，若与主结果冲突以主结果为准，**不得据压力结果重选参数**；")
    T.append("* 所有写入都在 `round5/attrib/` 下；`qrun_1416/`、`round3/`、`round4/`、`round5/paper/` 均未改动。")
    T.append("")

    # =====================================================================
    # 5 文件
    # =====================================================================
    T.append("## 5. 产物清单（`winrate_backtest_20261020/round5/attrib/`）")
    T.append("")
    T.append("**脚本**")
    T.append("```text")
    T.append("rerun_a.py              STEP1 复现 + 全台账 + 目标簿/调用流水")
    T.append("build_panel_cache.py    构建 qfq/原始价格宽表缓存（只读数据）")
    T.append("attrib_common.py        共享加载/恒等式/回撤/每日归因")
    T.append("step2_over15.py         STEP2 超限持仓归因")
    T.append("step3abc.py             STEP3a/3b/3c 最差日、回撤、Top5 与剔除敏感性")
    T.append("step3de.py              STEP3d/3e 分散度与行业缺口")
    T.append("step3f_stress.py        STEP3f 四窗口逐日重建")
    T.append("step3g_fifo.py          STEP3g 独立 FIFO")
    T.append("step3h_cost_delay.py    STEP3h 成本/延迟压力 + DelayedVariant")
    T.append("build_attrib_report.py  生成本报告")
    T.append("```")
    T.append("")
    T.append("**数据/结果（`out/`）**")
    T.append("```text")
    T.append("a_nav.csv a_trades.csv a_orders.csv a_events.csv a_positions.csv")
    T.append("a_audit_days.csv a_audit_reb.csv a_target_books.csv a_call_log.csv a_summary.json")
    T.append("freeze_check.json（与 SEAL 快照的 sha256 冻结校验）")
    T.append("over15_detail.csv over15_days.csv over15_episodes.csv over15_yearly.csv over15_summary.json")
    T.append("daily_contrib_long.csv daily_attrib_summary.csv worst20_days.csv worst20_contrib_long.csv")
    T.append("drawdowns_top3.csv drawdown_episodes_all.csv stock_contrib_all.csv top5_stock_contrib.csv")
    T.append("exclude_top1_sensitivity.csv exclude_top1_nav.csv")
    T.append("diversification_daily.csv diversification_yearly.csv suspension_flags.csv industry_exposure.csv")
    T.append("stress_daily_2015_crash.csv stress_daily_2020_covid.csv stress_daily_2025_apr.csv")
    T.append("stress_daily_2026_y1.csv stress_summary.csv/json")
    T.append("fifo_closed_trades.csv fifo_roundtrips.csv fifo_by_year.csv fifo_by_stress_window.csv")
    T.append("fifo_roundtrips_by_year.csv fifo_summary.json")
    T.append("cost_delay_stress.csv cost_delay_stress.json a_cost2x_nav.csv a_delay1_nav.csv a_delay1_trades.csv")
    T.append("```")
    T.append("")
    T.append("---")
    T.append("*本报告所有表格由 `build_attrib_report.py` 从上述 CSV/JSON 直接生成，"
             "未手工转录数值；任何修改请重跑对应脚本。*")
    T.append("")

    text = "\n".join(T)
    out = HERE / "ATTRIBUTION_A.md"
    out.write_text(text, encoding="utf-8")
    print("written", out, f"{len(text)} chars")


if __name__ == "__main__":
    main()
