# -*- coding: utf-8 -*-
"""Extra internal-consistency checks on qrun_1416/os/out/regime_v1.csv (read-only).

Kept in its own small script so it is independently re-runnable; writes
winrate_backtest_20261020/round3/audit/regime_internal.json.
"""
import json, os
import pandas as pd

WS = r"C:\Users\汤禹恒\OneDrive\文档\deepseek-harness\default-workspace"
REG = os.path.join(WS, r"qrun_1416\os\out\regime_v1.csv")
CAL = os.path.join(WS, r"qrun_1416\data\meta\calendar.csv")
OUT = os.path.join(WS, r"winrate_backtest_20261020\round3\audit")
MAP = {"BULL": "B", "NEUTRAL": "N", "BEAR": "E", "CRASH": "C"}


def main():
    reg = pd.read_csv(REG, dtype=str)
    cal = pd.read_csv(CAL, dtype=str)
    cal_open = set(cal.loc[cal["is_open"] == "1", "date"])

    reg["letter"] = reg["state"].map(MAP)
    reg["sp"] = reg["state"].shift(1)
    prev_bad = reg[(reg["state_prev"] != reg["sp"]) & reg["state_prev"].notna() & reg["sp"].notna()]
    flags = {}
    for flag, st in [("crash_flag", "CRASH"), ("bear_flag", "BEAR"), ("bull_flag", "BULL")]:
        flags[flag] = {
            "rows_flag_1_but_state_not_" + st: int(((reg[flag] == "1") & (reg["state"] != st)).sum()),
            "rows_state_" + st + "_but_flag_0": int(((reg["state"] == st) & (reg[flag] != "1")).sum()),
        }

    span_open = set(d for d in cal_open if reg["date"].iloc[0] <= d <= reg["date"].iloc[-1])
    out = {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "regime_csv": "qrun_1416/os/out/regime_v1.csv",
        "rows": int(len(reg)),
        "date_monotonic_increasing": bool(reg["date"].is_monotonic_increasing),
        "duplicate_dates": int(reg["date"].duplicated().sum()),
        "unmapped_states": int(reg["letter"].isna().sum()),
        "state_runs_in_csv": int((reg["state"] != reg["sp"]).sum()),
        "state_prev_equals_previous_row_state": len(prev_bad) == 0,
        "state_prev_mismatch_rows": int(len(prev_bad)),
        "state_prev_checked_on_all_rows": int(len(reg) - 1),
        "flag_vs_state": flags,
        "note_flags": ("crash_flag/bear_flag/bull_flag are raw trigger indicators of the regime "
                       "state machine, so they do not one-to-one equal state by design; S0 reads "
                       "only the frozen RUN_TEXT / state column, never these flags"),
        "calendar_open_days_in_span": len(span_open),
        "regime_dates_outside_calendar_open": sorted(set(reg["date"]) - span_open),
        "calendar_open_missing_from_regime": sorted(span_open - set(reg["date"])),
    }
    with open(os.path.join(OUT, "regime_internal.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
