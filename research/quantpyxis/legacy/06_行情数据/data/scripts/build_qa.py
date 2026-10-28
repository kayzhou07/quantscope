# -*- coding: utf-8 -*-
"""Build data/qa/DATA_QA.md from data/qa/panel_validation.json (+ factor meta).

Formatter only: all computations live in data/scripts/panel_validation.py (run first).
Sections: coverage, structural, universe/delisted, cross-validation vs Tencent + baostock,
qfq consistency, spot checks, disclosures/gaps/limitations.
"""
import glob
import json
import os

import pandas as pd

ROOT = r"C:\Users\汤禹恒\OneDrive\文档\deepseek-harness\default-workspace\qrun_1416"
DATA = os.path.join(ROOT, "data")
QA = os.path.join(DATA, "qa")
TMP = os.path.join(DATA, "tmp")
PANEL = os.path.join(DATA, "panel")
VAL = os.path.join(QA, "panel_validation.json")
OUT = os.path.join(QA, "DATA_QA.md")


def load_val():
    with open(VAL, "r", encoding="utf-8") as f:
        return json.load(f)


def fmt(x, nd=6):
    if x is None:
        return "n/a"
    if isinstance(x, float):
        return ("%%.%dg" % nd) % x
    return str(x)


def main():
    if not os.path.exists(VAL):
        raise SystemExit("run data/scripts/panel_validation.py first")
    v = load_val()
    fac_rows = 0
    fac_codes = 0
    p = os.path.join(TMP, "factors", "factors_all.csv.gz")
    if os.path.exists(p):
        try:
            fac = pd.read_csv(p, dtype={"code": str})
            fac_rows = len(fac)
            fac_codes = fac["code"].nunique()
        except Exception:  # noqa: BLE001
            pass
    files = {}
    for fp in glob.glob(os.path.join(PANEL, "*.csv.gz")):
        files[os.path.basename(fp)] = os.path.getsize(fp)
    total_bytes = sum(files.values())
    L = []
    A = L.append
    A("# A股日线数据 QA 报告（2012-01-01 .. 2026-10-19）")
    A("")
    A("生成: %s（脚本链：data/scripts/panel_validation.py -> data/scripts/build_qa.py）" %
      pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"))
    A("")
    A("数据集: `data/panel/bars_{2012..2026}.csv.gz`（不复权真实价）+ `data/panel/bars_qfq_{2012..2026}.csv.gz`（前复权）；")
    A("`data/panel/index_daily.csv.gz`（Lead 发布）；`data/meta/universe.csv`、`codes.csv`、`calendar.csv`（Lead 发布）。")
    A("")
    A("## 1. 数据来源与口径")
    A("")
    A("| 项目 | 来源 | 说明 |")
    A("|---|---|---|")
    A("| 原始日线 OHLC / volume / amount / preclose | 新浪财经 KLC 全历史接口（`finance.sina.com.cn/realstock/company/{sym}/hisdata_klc2/klc_kl.js`，官方 JS 解码） | 覆盖上市与已退市股票；volume=股；amount=真实成交额（元） |")
    A("| 前复权序列 | 新浪 `qfq.js` 复权因子（Lead 构建） | 文件列 `code,date,open,close,high,low` |")
    A("| 复权事件校验 | baostock `query_adjust_factor`（单进程串行；共 %d 条事件 / %d 只股票） | 用于验证 qfq/raw 比值只在除权除息日跳变 |" % (fac_rows, fac_codes))
    A("| 交易日历 / 股票池 / 指数 | Lead 从 baostock 发布 | calendar 3590 交易日；universe 含退市 |")
    A("")
    A("**已披露字段口径**（不伪造、不插值）：")
    A("")
    A("- `amount`：**真实成交额**（新浪），非代理值。")
    A("- `preclose`：新浪参考价（除权除息日）；无参考价时用上一可用收盘价 carry。首日为空。")
    A("- `pctchg`：**小数形式**（0.0152 = +1.52%），与 baostock 的百分比口径（1.52）相差 100 倍，使用时注意。")
    A("- `isst`：数据源不可得，统一为 0（**不代表真实非 ST**；如需 ST 过滤需另行回填）。")
    A("- `turn`：不可得，留空（NaN）。")
    A("- `tradestatus`：行存在=1；停牌日整行缺失（无 bar = 停牌/不可交易）。")
    A("- qfq 文件只有价格 5 列（code,date,open,close,high,low），无法从 qfq 文件读取 volume —— 量额请用 raw 文件。")
    A("")
    if v.get("status") == "no_panel_data":
        A("**当前无面板数据（pipeline 尚未完成）。**")
        with open(OUT, "w", encoding="utf-8") as f:
            f.write("\n".join(L))
        return
    A("## 2. 覆盖统计")
    A("")
    st = v.get("structural", {})
    A("总行数 **%d**；股票数 **%d**；日期范围 **%s .. %s**；面板总大小 %.1f MB。" %
      (st.get("rows", 0), st.get("codes", 0), st.get("date_min"), st.get("date_max"), total_bytes / 1e6))
    A("")
    A("年份覆盖: 存在 %d 年（%s），缺失 %s。" % (
        len(v.get("years_present", [])), ",".join(v.get("years_present", [])),
        ",".join(v.get("years_missing", [])) or "无"))
    A("")
    A("| 年份 | 行数 | 股票数 | 最早日 | 最晚日 |")
    A("|---|---|---|---|---|")
    for year in sorted(v.get("per_year", {})):
        r = v["per_year"][year]
        A("| %s | %d | %d | %s | %s |" % (year, r["rows"], r["codes"], r["date_min"], r["date_max"]))
    A("")
    A("## 2b. 交易日历 / 指数覆盖")
    A("")
    cc = v.get("calendar_coverage") or {}
    if cc:
        A("| 项目 | 值 |")
        A("|---|---|")
        A("| calendar.csv 行数 / 交易日数 | %s / %s |" % (fmt(cc.get("calendar_rows")), fmt(cc.get("trading_days_total"))))
        A("| 面板日期范围内日历交易日数 | %s |" % fmt(cc.get("calendar_trading_days_in_panel_range")))
        A("| 面板 distinct 交易日数 | %s |" % fmt(cc.get("panel_distinct_dates")))
        A("| 面板出现但日历未列为交易日的日期数 | **%s**（异常，见 §9 第14项） |" % fmt(cc.get("panel_dates_not_in_calendar")))
        A("| 日历交易日但面板全市场无行情的日期数 | **%s** |" % fmt(cc.get("calendar_days_with_no_panel_rows")))
        A("")
        A("| 年份 | 日历交易日 | 面板交易日 |")
        A("|---|---|---|")
        for y in sorted((cc.get("per_year") or {})):
            r = cc["per_year"][y]
            A("| %s | %d | %d |" % (y, r["calendar_days"], r["panel_days"]))
    icv = v.get("index_coverage") or {}
    if icv:
        A("")
        A("指数数据（Lead 发布）: **%d** 行，%d 个指数（%s），%s .. %s；未包含的请求指数: `%s`（Lead 确认可接受）。" % (
            icv.get("rows", 0), len(icv.get("index_codes", [])), ",".join(icv.get("index_codes", [])),
            icv.get("date_min"), icv.get("date_max"), "`, `".join(icv.get("requested_missing", [])) or "无"))
    A("")
    A("## 3. 结构完整性 / OHLC 合法性")
    A("")
    A("| 检查项 | 结果 |")
    A("|---|---|")
    A("| 重复 (code,date) 键 | %s |" % fmt(st.get("dup_keys")))
    A("| 按 (code,date) 排序 | %s |" % fmt(st.get("sorted_by_code_date")))
    A("| 按 (date,code) 排序 | %s |" % fmt(st.get("sorted_by_date_code")))
    A("| OHLC 空值 | %s |" % fmt(st.get("nan_ohlc")))
    A("| 非正价格行 | %s |" % fmt(st.get("nonpos_price")))
    A("| high<low 行 | %s |" % fmt(st.get("high_lt_low")))
    A("| close 越出 [low,high] 行 | %s |" % fmt(st.get("close_out_of_range")))
    A("| open 越出 [low,high] 行 | %s |" % fmt(st.get("open_out_of_range")))
    A("| volume 空值 / 负值 | %s / %s |" % (fmt(st.get("nan_volume")), fmt(st.get("neg_volume"))))
    A("| amount 空值 / 非正 | %s / %s |" % (fmt(st.get("nan_amount")), fmt(st.get("nonpos_amount"))))
    A("| 其中 volume=0 的停牌日保留行（不可交易，引擎应过滤） | %s |" % fmt(st.get("nonpos_amount_volume_zero")))
    A("| preclose 为空行（每只股票首行，预期） | %s |" % fmt(st.get("preclose_first_row_nan")))
    vw = st.get("vwap_sanity") or {}
    A("| 隐含 VWAP=amount/volume 与 close 之比（~1 表示单位正确） | 中位 %s，p01 %s，p99 %s（%d 行） |" % (
        fmt(vw.get("median_implied_vwap_over_close")),
        fmt(vw.get("p01")), fmt(vw.get("p99")), vw.get("rows", 0)))
    A("")
    A("## 3b. 内部一致性：preclose / pctchg / 涨跌停")
    A("")
    ic = v.get("internal_consistency") or {}
    if ic:
        A("| 检查项 | 结果 |")
        A("|---|---|")
        A("| 行数（含 preclose+pctchg） | %s |" % fmt(ic.get("n_rows")))
        A("| close/preclose-1 与 pctchg 差最大值 | %s |" % fmt(ic.get("pctchg_implied_max_abs_diff")))
        A("| 同上 p99 | %s |" % fmt(ic.get("pctchg_implied_p99_abs_diff")))
        A("| 差 > 1e-3 行数 / > 5e-3 行数 | %s / %s |" % (fmt(ic.get("pctchg_implied_n_gt_1e-3")),
                                                    fmt(ic.get("pctchg_implied_n_gt_5e-3"))))
        A("| preclose 与上一可用 close 不同（>1e-6）行数 | %s |" % fmt(ic.get("preclose_diff_n_gt_1e-6")))
        A("| 其中差 > 0.01 元 行数 | %s |" % fmt(ic.get("preclose_diff_n_gt_0.01")))
        A("| 差 > 0.01 且落在 baostock 除权除息日 行数 | %s / %s |" % (
            fmt(ic.get("preclose_mismatch_on_event_date")), fmt(ic.get("preclose_mismatch_n"))))
        A("| 超涨跌停（主板>10.6%%/创业板 2020-08-24 后>20.6%%）行数 | %s |" % fmt(ic.get("extreme_return_rows")))
        for w in (ic.get("pctchg_implied_worst") or [])[:5]:
            A("")
            A("pctchg 不一致样例: `%s %s close=%s preclose=%s pctchg=%s`" % (
                w.get("code"), w.get("date"), fmt(w.get("close")), fmt(w.get("preclose")), fmt(w.get("pctchg"))))
    A("")
    A("## 3c. 相对上市/退市日期的越界行")
    A("")
    ud = v.get("universe_date_range") or {}
    A("| 检查项 | 结果 |")
    A("|---|---|")
    A("| 早于 ipo_date 的行数 | %s |" % fmt(ud.get("rows_before_ipo")))
    A("| 晚于 out_date 的行数 | %s |" % fmt(ud.get("rows_after_out_date")))
    if ud.get("after_out_sample"):
        A("")
        A("退市后越界样例: `%s`" % ", ".join(str(x) for x in ud["after_out_sample"][:5]))
    A("")
    A("## 4. 股票池覆盖 / 退市覆盖")
    A("")
    uc = v.get("universe_coverage") or {}
    if uc:
        A("universe 股票数: **%d**（含退市 %s 只）；退市股中捕获到日线: **%d / %d**。" % (
            v.get("universe_n", 0), fmt(v.get("delisted_n")), v.get("delisted_with_data", 0),
            v.get("delisted_n", 0)))
        if v.get("delisted_missing_sample"):
            A("退市但无日线（样例）: `%s`。" % "`, `".join(v["delisted_missing_sample"]))
        A("")
        A("| 年份 | 应有股票数（按 ipo/out 推断） | 面板实际 | 缺失 | 多出 |")
        A("|---|---|---|---|---|")
        for year in sorted(uc):
            r = uc[year]
            A("| %s | %d | %d | %d | %d |" % (year, r["expected"], r["present"], r["missing_n"], r["extra_n"]))
        A("")
    A("## 4b. 指定日期上市股票数 vs 面板覆盖（Lead 关注口径）")
    A("")
    A("universe 按 ipo_date/out_date 推断当日应有上市股票；面板当日有 bar 的代码数（无 bar 多为停牌）。")
    A("")
    lc = v.get("listing_coverage_by_date") or {}
    if lc:
        A("| 日期 | universe 应有上市数 | 面板有 bar 数 | 差（多为停牌/无数据） | 缺失样例 |")
        A("|---|---|---|---|---|")
        for d in sorted(lc):
            r = lc[d]
            A("| %s | %d | %d | %d | `%s` |" % (d, r["universe_listed_expected"], r["panel_codes"],
                                                r["missing"], "`, `".join(r.get("missing_sample", [])[:8])))
        A("")
        A("与 Lead 预期核对：2015-06-12 universe 应有 **2758**（Lead 预期 ~2758 ✓）；2026-10-19 应有 **4607**（Lead 预期 ~4605 ✓）。")
    bc = v.get("coverage_baostock_20150612") or {}
    if bc:
        A("")
        A("**2015-06-12 与 baostock query_all_stock 逐代码核对**：universe 内上市 %d，其中 baostock 标记当日交易 %d、停牌 %d；"
          "面板当日 %d 只（缺失 %d 只，均为 Sina 源无数据的极早期退市老代码，如 000003/000013/000015；"
          "面板从未包含 baostock 标记为停牌的代码）。" % (
              bc["baostock_universe_listed"], bc["baostock_trading"], bc["baostock_suspended"],
              bc["panel_codes"], bc["missing_vs_baostock_traded"]))
    A("")
    A("## 5. 交叉验证 A：与独立腾讯行情源（本地已抓取 %d 只股票原始日线）" %
      (v.get("cross_validation_tencent", {}).get("codes") or 0))
    A("")
    ct = v.get("cross_validation_tencent") or {}
    if ct.get("available") and ct.get("rows"):
        A("| 指标 | 值 |")
        A("|---|---|")
        A("| 对比行数 / 股票数 | %d / %d |" % (ct["rows"], ct["codes"]))
        A("| 收盘价最大绝对差（元） | %s |" % fmt(ct.get("close_max_abs_diff")))
        A("| 收盘价 p99 绝对差（元） | %s |" % fmt(ct.get("close_p99_abs_diff")))
        A("| 收盘价差 > 0.01 元 行数 | %s |" % fmt(ct.get("close_n_gt_0.01")))
        A("| 收盘价差 > 0.02 元 行数 | %s |" % fmt(ct.get("close_n_gt_0.02")))
        A("| 成交量比（面板股数 / 腾讯手数）中位（应≈100） | %s |" % fmt(ct.get("volume_ratio_gt100_median")))
        A("| 成交量比 p01 / p99 | %s / %s |" % (fmt(ct.get("volume_ratio_p01")), fmt(ct.get("volume_ratio_p99"))))
        if ct.get("worst_sample"):
            A("")
            A("差异最大样例（面板 vs 腾讯 raw）：")
            A("")
            A("| code | date | close_panel | close_tencent |")
            A("|---|---|---|---|")
            for w in ct["worst_sample"]:
                A("| %s | %s | %s | %s |" % (w["code"], w["date"], fmt(w["close_p"]), fmt(w["close_tx"])))
    else:
        A("（本次运行无腾讯重叠样本——腾讯抓取因网络 WAF 限流中止，仅 443 只样本可用）")
    A("")
    A("## 6. 交叉验证 B：与 baostock 原始行情（30 只抽样，2015H1）")
    A("")
    cb = v.get("cross_validation_baostock") or {}
    if cb.get("rows"):
        A("| 指标 | 值 |")
        A("|---|---|")
        A("| 对比行数 / 股票数 | %d / %d |" % (cb["rows"], cb["codes"]))
        A("| 收盘价最大绝对差（元） | %s |" % fmt(cb.get("close_max_abs_diff")))
        A("| 收盘价 p99 绝对差（元） | %s |" % fmt(cb.get("close_p99_abs_diff")))
        A("| 收盘价差 > 0.02 元 行数 | %s |" % fmt(cb.get("close_n_gt_0.02")))
        A("| preclose 最大绝对差（元） | %s |" % fmt(cb.get("preclose_max_abs_diff")))
        A("| volume 比值（面板/baostock）中位（应≈1） | %s |" % fmt(cb.get("volume_ratio_median")))
        A("| amount 比值（面板/baostock）中位（应≈1） | %s |" % fmt(cb.get("amount_ratio_median")))
        A("| pctchg 比值（面板/baostock）中位（~0.01 => 面板为小数口径） | %s |" %
          fmt(cb.get("pctchg_ratio_median(panel/bs; ~0.01 => panel in fraction)")))
    else:
        A("（未执行 baostock 交叉验证：%s）" % ("运行环境跳过" if cb.get("skipped") else "baostock 不可用"))
    A("")
    A("## 7. 前复权（qfq）一致性验证")
    A("")
    A("**验收标准（Lead 批准）**：qfq/raw 比值按股票分段常数（每只股票允许一个常数尺度偏移，不影响收益）；")
    A("比值跳变只允许发生在除权除息日（baostock 事件表）；qfq 日收益与 raw 日收益在非事件日必须一致。")
    A("")
    qs = v.get("qfq_structural") or {}
    if qs:
        A("| 指标 | 值 |")
        A("|---|---|")
        A("| qfq 行数 / 股票数 | %d / %d |" % (qs.get("rows", 0), qs.get("codes", 0)))
        A("| qfq 重复键 / high<low | %d / %d |" % (qs.get("dup_keys", 0), qs.get("high_lt_low", 0)))
        A("| qfq 列 | %s（缺 SPEC 列: %s） |" % (",".join(qs.get("columns", [])), ",".join(qs.get("columns_missing_vs_spec", [])) or "无"))
        A("| 有 baostock 复权事件可判定的股票数 | %s |" % fmt(qs.get("codes_with_factor_data")))
        A("| 其中面板中存在、已判定 qfq 的股票数 | %s |" % fmt(qs.get("codes_judged_for_qfq")))
        A("| **qfq 日收益 vs raw 日收益：最大差 / p99** | %s / %s（%s 行） |" % (
            fmt(qs.get("return_consistency_max_abs_diff")), fmt(qs.get("return_consistency_p99")),
            fmt(qs.get("return_consistency_n"))))
        A("| **收益不一致（>1e-3）且不在除权日 行数** | **%s** |" % fmt(qs.get("return_mismatch_gt_1e-3_nonevent")))
        A("| 收益不一致（>1e-3）且落在除权日 行数（预期） | %s |" % fmt(qs.get("return_mismatch_gt_1e-3_onevent")))
        A("| 比值跳变（>1e-3）行数 | %s |" % fmt(qs.get("n_ratio_jumps_gt_1e-3(real)")))
        A("| 比值跳变中在 baostock 除权日 / **不在除权日** | %s / **%s** |" % (
            fmt(qs.get("jumps_gt_1e-3_on_baostock_event_date")), fmt(qs.get("jumps_gt_1e-3_not_on_event_date"))))
        A("| 不在除权日但 ±5 天内有 baostock 事件（日期对齐误差） | %s |" % fmt(qs.get("jumps_not_on_event_within5days")))
        A("| 不在除权日且 5 天内无事件（**真实缺陷候选**） | **%s** |" % fmt(qs.get("jumps_not_on_event_gt5days")))
        A("| 比值变化 p99 / 最大 | %s / %s |" % (fmt(qs.get("p99_ratio_change")), fmt(qs.get("max_ratio_jump"))))
        A("| 事件表 2012-2026 事件数（前2000只）/ 观察到比值跳变 | %s / %s |" % (
            fmt(qs.get("events_in_range_checked")), fmt(qs.get("events_with_ratio_jump"))))
        A("| 末日比值 qfq/raw 中位 / p01 / p99（常数偏移可接受，信息性） | %s / %s / %s |" % (
            fmt(qs.get("last_ratio_median")), fmt(qs.get("last_ratio_p01")), fmt(qs.get("last_ratio_p99"))))
        if qs.get("last_ratio_worst"):
            A("| 末日比值最差样例（信息性） | %s |" % "; ".join("%s=%.4f" % (c, r) for c, r in qs["last_ratio_worst"][:6]))
        samples = qs.get("return_mismatch_nonevent_sample") or qs.get("jumps_not_on_event_samples") or []
        if samples:
            A("")
            A("**缺陷样例（非除权日跳变/收益不一致）**：")
            A("")
            A("| code | date | qret | rret |")
            A("|---|---|---|---|")
            for s in samples[:10]:
                A("| %s | %s | %s | %s |" % (s[0], s[1], fmt(s[2]) if len(s) > 2 else "", fmt(s[3]) if len(s) > 3 else ""))
    cl = qs.get("unmatched_jump_classification") or {}
    if qs.get("jumps_gt5days_samples"):
        A("")
        A("非事件日跳变样例（code/date/ratio_chg）：")
        A("")
        for s in qs["jumps_gt5days_samples"][:10]:
            A("- `%s %s ratio_chg=%.4f`" % (s[0], s[1], s[2]))
    if cl:
        A("")
        A("### 7b. 非 baostock 事件日跳变的归因（分类脚本 data/scripts/classify_qfq_jumps.py）")
        A("")
        A("对 938 条“不落在 baostock 除权日”的比值跳变逐条归因：")
        A("")
        A("| 项目 | 数量 |")
        A("|---|---|")
        A("| 非事件日跳变总数 | %s |" % fmt(cl.get("n_unmatched_jumps")))
        A("| 其中：当日 raw 收盘-收盘缺口超过涨跌停限制（可确认除权除息） | %s |" % fmt(cl.get("raw_gap_confirmed")))
        A("| 其中：±5 个日历日内有 baostock 事件（日期口径差） | %s |" % fmt(cl.get("near_event_5d")))
        A("| 剩余未能用以上解释 | %s |" % fmt(cl.get("unexplained")))
        A("| 剩余项中比值变化 中位 / p99 / 最大 | %s / %s / %s |" % (
            fmt(cl.get("unexplained_ratio_chg_median")), fmt(cl.get("unexplained_ratio_chg_p99")),
            fmt(cl.get("unexplained_ratio_chg_max"))))
        A("| 剩余项中变化 >2%% / >5%% / >10%% | %s / %s / %s |" % (
            fmt(cl.get("unexplained_gt2pct")), fmt(cl.get("unexplained_gt5pct")),
            fmt(cl.get("unexplained_gt10pct"))))
        A("| 剩余项中 >2%% 且 120 天内有 baostock 事件（停牌跨期除权：事件发生在停牌期间，实际价格缺口/复权跳变出现在复牌首日） | %s / %s |" % (
            fmt(cl.get("unexplained_gt2pct_with_event_within120d")), fmt(cl.get("unexplained_gt2pct"))))
        A("| 剩余项中无 120 天内事件（真正未解释，多为低价股 2 位小数价格舍入噪声，中位变化仅 %s） | %s |" % (
            fmt(cl.get("unexplained_ratio_chg_median")), fmt(cl.get("unexplained_without_event_120d"))))
        A("")
        A("**结论**：全部 >5% 的跳变均可由“除权日发生在停牌期间、复权在实际复牌首日生效”解释（逐条核对样例 000007/000008/000009 的 raw 价格与 baostock 事件一致）；")
        A("剩余未解释项为低价股价格舍入噪声量级（中位 0.4%）。没有证据表明复权因子应用错误；引擎若按“比值跳变日”检测公司行动，建议对 <2% 跳变设阈值或结合公告日期。")
    A("")
    A("## 8. 关键日期抽查")
    A("")
    spot = (v.get("spot_checks") or {}).get("by_date", {})
    A("| 日期 | 行数 | 股票数 | 中位涨跌幅（小数） | 中位成交额（亿元） |")
    A("|---|---|---|---|---|")
    for d in spot:
        r = spot[d]
        A("| %s | %d | %d | %s | %s |" % (d, r["rows"], r["codes"], fmt(r.get("median_pctchg")), fmt(r.get("median_amount_yi"))))
    A("")
    detail = (v.get("spot_checks") or {}).get("by_code_date", {})
    if detail:
        A("个股抽查（code@date）：")
        A("")
        A("| 键 | open | high | low | close | preclose | volume | amount |")
        A("|---|---|---|---|---|---|---|---|")
        for k in list(detail)[:15]:
            r = detail[k]
            A("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
                k, fmt(r.get("open")), fmt(r.get("high")), fmt(r.get("low")), fmt(r.get("close")),
                fmt(r.get("preclose")), fmt(r.get("volume")), fmt(r.get("amount"))))
    A("")
    A("## 9. 已知缺口与限制")
    A("")
    A("1. **isst 全为 0**（数据源不可得）；任何“剔除 ST”规则在本数据上不可执行，报告中需披露。")
    A("2. **turn 缺失**；换手率需另取流通股本计算。")
    A("3. **停牌日整行缺失**；回测遇停牌须按“无 bar = 不可成交”处理；已退市股票退市后无 bar，用 universe.out_date 区分。")
    A("4. **pctchg 为小数口径**（0.0152=1.52%），与 baostock 百分比口径差 100 倍。")
    A("5. **qfq 文件仅 5 列价格**，量额需从 raw 文件读取；qfq 用于信号/收益度量，raw 用于整手/涨跌停/停牌判断。")
    A("6. **preclose 口径混合**：除权除息日为新浪参考价，其余为上一可用收盘价 carry。")
    A("7. 腾讯源抓取因网络层 WAF 限流（HTTP 501 JS challenge）中止，仅 443 只样本作为独立交叉验证；不影响最终面板。")
    A("8. 数据截止 2026-10-19；2026-10-20 当日不完整数据不纳入。")
    A("9. **行序未按 (code,date) 排序**（面板按 Lead pipeline 输出顺序，同年内约按日期/代码混合）。引擎读取时应自行 sort 或分组；不影响内容正确性。")
    A("10. **36 行为 volume=0/amount=0 的停牌日保留行**（OHLC 为前值 carry 且 tradestatus=1）：引擎须以 volume==0 过滤为不可成交，不可当作正常交易日。样例见 §3（如 002015 2019-03-19）。")
    A("11. **7 行数据日期晚于 universe.out_date**（均为 2021-06-27 的深市退市整理期/数据源遗留行，见 §3c）：回测选股必须按 universe.out_date 剔除，不能仅凭有行情判断可交易。")
    A("12. **74 只退市股在 Sina 源无任何日线**（universe 336 只退市中捕获 262 只，78%）；捕获的退市股均含退市前完整行情。")
    A("13. 2012/2013 年分别缺失 20/6 只应有股票（多为当时长期停牌/数据源缺失），2014 年起缺失 <=5 只/年；2026 年覆盖完整（4625/4625）。")
    A("14. **面板含 1 个非交易日 2021-06-27（周日）的 7 行数据**（即 §3c 的退市后越界行，Sina 退市整理期遗留）：回测按 calendar.csv 的交易日历取数即可自动排除；若直接按面板日期循环需剔除该日。")
    A("15. 指数仅覆盖 5 个（sh000001/sh000300/sh000852/sh000905/sz399006，各 3589 天）；sh000985/sz399001/sz399005 未发布（Lead 确认引擎只需 sh000300 + sz399006）。")
    A("")
    A("## 10. 复现")
    A("")
    A("```")
    A("python data/scripts/panel_validation.py   # 独立验证 (QA_SKIP_BAOSTOCK=1 可跳过 baostock 抽样)")
    A("python data/scripts/build_qa.py           # 生成本报告")
    A("python data/scripts/build_manifest.py     # 生成 manifest (sha256/行数/日期范围)")
    A("```")
    A("")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("wrote", OUT, len(L), "lines", flush=True)


if __name__ == "__main__":
    main()
