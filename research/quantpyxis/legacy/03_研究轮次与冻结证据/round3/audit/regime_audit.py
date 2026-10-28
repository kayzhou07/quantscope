# -*- coding: utf-8 -*-
"""Item 1 + 2: regime fidelity (RUN_TEXT in S0 vs qrun_1416/os/out/regime_v1.csv)
and in-window state distribution. Read-only on qrun_1416; writes only under
winrate_backtest_20261020/round3/audit/.
"""
import re, json, bisect, hashlib, os
import pandas as pd

WS = r"C:\Users\汤禹恒\OneDrive\文档\deepseek-harness\default-workspace"
S0 = os.path.join(WS, r"winrate_backtest_20261020\SuperMind_S0_月度基线_胜率诊断_v03.py")
S1 = os.path.join(WS, r"winrate_backtest_20261020\SuperMind_S1_整手候选补位_待验证_v03.py")
Q0 = os.path.join(WS, r"winrate_backtest_20261020\SuperMind_Q0_季度胜率诊断_v03.py")
R2 = os.path.join(WS, r"winrate_backtest_20261020\SuperMind_R2_v02.py")
REG = os.path.join(WS, r"qrun_1416\os\out\regime_v1.csv")
OUT = os.path.join(WS, r"winrate_backtest_20261020\round3\audit")

MAP = {"BULL": "B", "NEUTRAL": "N", "BEAR": "E", "CRASH": "C"}


def extract_run_text(path):
    txt = open(path, encoding="utf-8").read()
    m = re.search(r"^RUN_TEXT='([^']*)'", txt, re.M)
    if not m:
        raise SystemExit("RUN_TEXT not found in " + path)
    return m.group(1)


def parse_runs(run_text):
    toks = run_text.split()
    dates = [t[:8] for t in toks]
    letters = [t[8:] for t in toks]
    assert len(dates) == len(letters)
    for d, l in zip(dates, letters):
        assert len(d) == 8 and d.isdigit(), d
        assert l in "BNEC", l
    assert dates == sorted(dates), "run tokens not sorted"
    return toks, dates, letters


def state_at(dates, letters, day):
    i = bisect.bisect_right(dates, day) - 1
    if i < 0:
        return None
    return letters[i]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    run_text = extract_run_text(S0)
    toks, dates, letters = parse_runs(run_text)

    # cross-script frozen-regime consistency
    cross = {}
    for name, p in [("S1", S1), ("Q0", Q0), ("R2", R2)]:
        try:
            rt = extract_run_text(p)
            cross[name] = {"identical_to_S0": rt == run_text,
                           "len": len(rt), "n_tokens": len(rt.split())}
        except SystemExit:
            cross[name] = {"identical_to_S0": None, "note": "no RUN_TEXT"}

    # regime CSV, read as strings so date/state are exact
    reg = pd.read_csv(REG, dtype=str)
    reg["csv_letter"] = reg["state"].map(MAP)
    unmapped = int(reg["csv_letter"].isna().sum())

    rows = []
    mismatches = []
    for _, r in reg.iterrows():
        d = r["date"].replace("-", "")
        run_letter = state_at(dates, letters, d)
        cl = r["csv_letter"]
        match = (run_letter is not None and cl is not None and run_letter == cl)
        rows.append({"date": r["date"], "run_letter": run_letter if run_letter else "",
                     "csv_state": r["state"], "csv_letter": cl if cl else "", "match": match})
        if not match:
            mismatches.append((r["date"], run_letter, r["state"], cl))

    cmp_df = pd.DataFrame(rows, columns=["date", "run_letter", "csv_state", "csv_letter", "match"])
    cmp_df.to_csv(os.path.join(OUT, "regime_compare.csv"), index=False, encoding="utf-8")

    total = len(cmp_df)
    n_match = int(cmp_df["match"].sum())
    n_mis = total - n_match

    # distribution of RUN_TEXT letters over full CSV span (for context)
    reg["run_letter"] = [state_at(dates, letters, d.replace("-", "")) for d in reg["date"]]
    # state runs inside the CSV: count blocks where letter changes
    full_runs = 0
    prev = None
    for l in reg["run_letter"]:
        if l != prev:
            full_runs += 1
            prev = l

    # item 2: backtest window
    reg["d"] = pd.to_datetime(reg["date"])
    w = reg[(reg["d"] >= "2014-07-01") & (reg["d"] <= "2023-12-29")].copy()
    w["run_letter"] = [state_at(dates, letters, d) for d in w["date"].str.replace("-", "")]
    cnt = w["run_letter"].value_counts().to_dict()
    exposure = {"B": 0.60, "N": 0.25, "E": 0.0, "C": 0.0}
    w["exposure"] = w["run_letter"].map(exposure)
    exp_counts = w["exposure"].value_counts().sort_index().to_dict()

    blocks = []
    prev = None
    for _, r in w.iterrows():
        if r["run_letter"] != prev:
            blocks.append({"date": r["date"], "letter": r["run_letter"],
                           "exposure": exposure[r["run_letter"]]})
            prev = r["run_letter"]
    # end date of each block
    for i, b in enumerate(blocks):
        b["end_date"] = blocks[i + 1]["date"] if i + 1 < len(blocks) else w["date"].iloc[-1]

    # token set used inside window (tokens whose effective date is within window)
    win_tokens = [(d, l) for d, l in zip(dates, letters)
                  if "20140701" <= d <= "20231229"]

    out = {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "s0_script": r"winrate_backtest_20261020\SuperMind_S0_月度基线_胜率诊断_v03.py",
        "regime_csv": r"qrun_1416\os\out\regime_v1.csv",
        "regime_csv_sha256": sha256(REG),
        "run_text_sha256_of_literal": hashlib.sha256(run_text.encode("utf-8")).hexdigest(),
        "run_text_n_tokens": len(toks),
        "run_text_first": toks[0],
        "run_text_last": toks[-1],
        "run_text_token_dates_min": dates[0],
        "run_text_token_dates_max": dates[-1],
        "cross_script_run_text": cross,
        "csv_rows": int(total),
        "csv_date_min": reg["date"].iloc[0],
        "csv_date_max": reg["date"].iloc[-1],
        "csv_state_counts": reg["state"].value_counts().to_dict(),
        "csv_rows_with_unmapped_state": unmapped,
        "comparison": {
            "total_rows": int(total),
            "matches": n_match,
            "mismatches": int(n_mis),
            "first_20_mismatches": [
                {"date": d, "run_letter": rl, "csv_state": cs, "csv_letter": cl}
                for (d, rl, cs, cl) in mismatches[:20]
            ],
            "claim_all_3448_rows_match": n_mis == 0 and total == 3448,
        },
        "state_runs_in_csv_span": full_runs,
        "window": {
            "start": "2014-07-01",
            "end": "2023-12-29",
            "trading_rows": int(len(w)),
            "state_counts": {k: int(cnt.get(k, 0)) for k in ["B", "N", "E", "C"]},
            "exposure_tier_day_counts": {f"{k:.2f}": int(v) for k, v in exp_counts.items()},
            "exposure_tier_implied_by_state": {
                "B->0.60": int(cnt.get("B", 0)),
                "N->0.25": int(cnt.get("N", 0)),
                "E->0.00": int(cnt.get("E", 0)),
                "C->0.00": int(cnt.get("C", 0)),
            },
            "distinct_state_runs": len(blocks),
            "tokens_effective_inside_window": len(win_tokens),
            "blocks": blocks,
        },
    }
    # also record regime rows by year in window for sanity
    out["window"]["rows_by_year"] = {str(k): int(v) for k, v in
                                     w["d"].dt.year.value_counts().sort_index().items()}

    # calendar cross-check: regime CSV rows should be exactly the open trading days in its span
    cal = pd.read_csv(os.path.join(WS, r"qrun_1416\data\meta\calendar.csv"), dtype=str)
    cal_open = cal.loc[cal["is_open"] == "1", "date"]
    span_open = set(cal_open[(cal_open >= reg["date"].iloc[0]) & (cal_open <= reg["date"].iloc[-1])])
    reg_dates = set(reg["date"])
    out["calendar_cross_check"] = {
        "calendar_open_days_in_regime_span": len(span_open),
        "regime_rows": len(reg_dates),
        "regime_dates_not_calendar_open": sorted(reg_dates - span_open),
        "calendar_open_days_missing_from_regime": sorted(span_open - reg_dates),
        "calendar_and_regime_date_sets_equal": reg_dates == span_open,
    }
    # run tokens: are effective dates trading days present in the CSV?
    tok_in_csv = [d for d in dates if d[:4] + "-" + d[4:6] + "-" + d[6:] in reg_dates]
    tok_not_trading = [d for d in dates if d[:4] + "-" + d[4:6] + "-" + d[6:] not in span_open]
    out["run_tokens"] = {
        "n_tokens": len(dates),
        "token_dates_present_in_regime_csv": len(tok_in_csv),
        "token_dates_not_a_calendar_trading_day": tok_not_trading,
        "consecutive_tokens_same_letter": [
            (dates[i], dates[i + 1]) for i in range(len(letters) - 1) if letters[i] == letters[i + 1]
        ],
    }
    with open(os.path.join(OUT, "regime_audit.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(json.dumps({k: out[k] for k in ["csv_rows", "run_text_n_tokens", "comparison"]},
                     ensure_ascii=False, indent=2))
    print("window:", json.dumps(out["window"]["state_counts"]),
          "rows:", out["window"]["trading_rows"],
          "runs:", out["window"]["distinct_state_runs"])
    print("exposure:", json.dumps(out["window"]["exposure_tier_day_counts"]))
    print("mismatches:", n_mis, "of", total)


if __name__ == "__main__":
    main()
