"""Public API tests: result schema, strategies, JSON export, regime labels, errors."""

import json
import shutil
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fixture_utils import END, START, TOY, make_tmp_dir, run_toy  # noqa: E402

from engine.api import (  # noqa: E402
    BuyHold,
    Cash,
    EqualWeight,
    MomentumTopN,
    result_to_json,
    run_backtest,
    run_many,
    save_result_json,
    save_results_json,
)
from engine.data import DataNotReadyError  # noqa: E402

REQUIRED_KEYS = {
    "name", "start", "end", "capital", "nav", "trades", "orders", "events",
    "positions", "holdings_final", "metrics", "monthly", "monthly_matrix",
    "exposure", "tail", "fifo", "benchmarks", "benchmark_compare",
    "regime_segments", "summary", "settings", "data_version", "warnings",
}
REQUIRED_METRICS = {
    "final_nav", "total_return", "cagr", "ann_vol", "sharpe", "sortino", "calmar",
    "max_drawdown", "maxdd_peak_date", "maxdd_trough_date", "maxdd_duration_days",
    "avg_exposure", "end_exposure", "all_cash_days", "turnover_annual", "trade_count",
    "trade_days", "total_fees", "fifo_realized_pnl", "fifo_win_rate",
    "implicit_dividends", "pnl_reconciliation_gap",
}
REQUIRED_NAV_COLS = {"nav", "cash", "market_value", "exposure", "ret", "n_positions",
                     "fees", "dividends", "turnover", "regime"}


class TestResultSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run_toy(EqualWeight(top_n=4))

    def test_required_keys(self):
        missing = REQUIRED_KEYS - set(self.result.keys())
        self.assertEqual(missing, set())

    def test_metrics(self):
        m = self.result["metrics"]
        missing = REQUIRED_METRICS - set(m.keys())
        self.assertEqual(missing, set())
        self.assertEqual(m["trading_days"], len(self.result["nav"]))
        self.assertAlmostEqual(m["final_nav"], float(self.result["nav"]["nav"].iloc[-1]),
                               places=6)
        self.assertLess(abs(m["pnl_reconciliation_gap"]), 1.0)
        self.assertTrue(np.isfinite(m["sharpe"]))
        self.assertLessEqual(m["max_drawdown"], 0.0)

    def test_nav_columns(self):
        nav = self.result["nav"]
        self.assertTrue(REQUIRED_NAV_COLS.issubset(set(nav.columns)))
        self.assertEqual(len(nav), 415)  # 2020-06-01 .. 2021-12-31 weekdays in fixture
        self.assertEqual(nav.index[0], START)
        self.assertEqual(nav.index[-1], END)

    def test_monthly_and_benchmarks(self):
        monthly = self.result["monthly"]
        self.assertGreater(len(monthly), 18)
        self.assertIn("ret", monthly.columns)
        bc = self.result["benchmark_compare"]
        self.assertIn("sh000300", set(bc["benchmark"]))
        self.assertIn("cash", set(bc["benchmark"]))
        self.assertIn("bench_sh000300", self.result["nav"].columns)
        self.assertGreater(self.result["exposure"]["avg_exposure"], 0.0)

    def test_summary(self):
        s = self.result["summary"]
        self.assertEqual(s["name"], "EqualWeight(top4)")
        self.assertEqual(s["start"], START)
        self.assertIn("maxdd_trough_date", s)


class TestStrategySpecs(unittest.TestCase):
    def test_shortcut_strings(self):
        for spec, name in (("cash", "Cash"), ("buyhold:sh000300", "BuyHold(sh000300)"),
                           ("equalweight:5", None), ("momentum:5", None)):
            with self.subTest(spec=spec):
                r = run_toy(spec)
                self.assertGreater(len(r["nav"]), 0)
                if name:
                    self.assertEqual(r["name"], name)

    def test_dict_strategy(self):
        seen = {}

        def on_bar(ctx):
            seen.setdefault("dates", []).append(ctx.date)

        r = run_toy({"name": "dicttest", "on_bar": on_bar})
        self.assertEqual(r["name"], "dicttest")
        self.assertEqual(len(seen["dates"]), len(r["nav"]))

    def test_run_many_and_json(self):
        tmp = make_tmp_dir("api")
        try:
            results = run_many({"a": BuyHold("sh000300"), "b": Cash()},
                               start=START, end=END, data_dir=TOY, warmup_days=40,
                               slippage_bps=0.0)
            self.assertEqual(set(results), {"a", "b"})
            path = save_results_json(results, tmp / "runs.json")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(set(payload), {"a", "b"})
            self.assertGreater(len(payload["a"]["nav"]), 0)
            self.assertAlmostEqual(payload["a"]["summary"]["final_nav"],
                                   results["a"]["final_nav"], places=2)
            r = run_toy(BuyHold("sh000300"), output_json=tmp / "one.json")
            self.assertTrue((tmp / "one.json").exists())
            payload1 = json.loads((tmp / "one.json").read_text(encoding="utf-8"))
            self.assertIn("metrics", payload1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_result_to_json_is_json_safe(self):
        r = run_toy(Cash())
        payload = result_to_json(r, include_nav=True, include_trades=True)
        json.dumps(payload)   # must not raise
        self.assertEqual(payload["name"], "Cash")


class TestRegimeLabelsIntegration(unittest.TestCase):
    def test_ctx_regime_and_nav_column(self):
        labels = {"2020-06-02": "bull", "2020-07-01": "bear", "2021-12-31": "neutral"}
        recorder = []

        def on_bar(ctx):
            recorder.append((ctx.date, ctx.regime))

        r = run_toy({"name": "regime_test", "on_bar": on_bar}, regime_labels=labels)
        got = dict(recorder)
        self.assertEqual(got["2020-06-02"], "bull")
        self.assertEqual(got["2020-07-01"], "bear")
        nav = r["nav"]
        self.assertEqual(nav.loc["2020-06-02", "regime"], "bull")
        self.assertTrue(nav["regime"].isna().sum() == len(nav) - 3)
        seg = r["regime_segments"]
        self.assertGreaterEqual(len(seg), 2)
        self.assertEqual(int(seg["days"].sum()), len(nav))


class TestIndexOnlyMode(unittest.TestCase):
    def test_index_only_backtest_without_panel(self):
        tmp = make_tmp_dir("indexonly")
        try:
            (tmp / "meta").mkdir()
            (tmp / "panel").mkdir()
            shutil.copy(TOY / "meta" / "calendar.csv", tmp / "meta" / "calendar.csv")
            shutil.copy(TOY / "meta" / "universe.csv", tmp / "meta" / "universe.csv")
            shutil.copy(TOY / "panel" / "index_daily.csv", tmp / "panel" / "index_daily.csv")
            r = run_backtest(BuyHold("sh000300"), start=START, end=END, data_dir=tmp,
                             warmup_days=40, require_panel=False, slippage_bps=0.0)
            self.assertGreater(len(r["nav"]), 0)
            self.assertGreater(r["metrics"]["avg_exposure"], 0.99)
            self.assertEqual(len(r["trades"]), 1)
            # a stock target has no data in this mode: it is dropped with an event
            r2 = run_backtest(BuyHold("600001"), start=START, end=END, data_dir=tmp,
                              warmup_days=40, require_panel=False)
            self.assertIn("unknown_target_code", set(r2["events"]["type"]))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestApiErrors(unittest.TestCase):
    def test_missing_data_dir_raises(self):
        with self.assertRaises(DataNotReadyError):
            run_backtest(Cash(), start=START, end=END, data_dir=TOY / "does_not_exist")

    def test_leverage_targets_rejected(self):
        def on_bar(ctx):
            if ctx.date == "2020-07-01":
                ctx.set_targets({"600001": 0.8, "600002": 0.8})

        with self.assertRaises(ValueError):
            run_toy({"name": "lev", "on_bar": on_bar})

    def test_negative_targets_rejected(self):
        def on_bar(ctx):
            if ctx.date == "2020-07-01":
                ctx.set_targets({"600001": -0.1})

        with self.assertRaises(ValueError):
            run_toy({"name": "neg", "on_bar": on_bar})

    def test_unknown_shortcut_rejected(self):
        with self.assertRaises(ValueError):
            run_toy("not_a_strategy")

    def test_no_panel_raises_by_default(self):
        tmp = make_tmp_dir("nopanel")
        try:
            (tmp / "meta").mkdir()
            (tmp / "panel").mkdir()
            shutil.copy(TOY / "meta" / "calendar.csv", tmp / "meta" / "calendar.csv")
            shutil.copy(TOY / "panel" / "index_daily.csv", tmp / "panel" / "index_daily.csv")
            with self.assertRaises(DataNotReadyError):
                run_backtest(BuyHold("sh000300"), start=START, end=END, data_dir=tmp,
                             warmup_days=40)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
