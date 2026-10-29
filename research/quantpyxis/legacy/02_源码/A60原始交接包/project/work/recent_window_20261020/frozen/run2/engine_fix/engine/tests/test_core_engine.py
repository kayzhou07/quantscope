"""End-to-end A-share rule tests on the synthetic toy fixture."""

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fixture_utils import END, START, events_of, load_toy_bundle, run_toy, trades_of  # noqa: E402

from engine.api import BuyHold, Cash, run_backtest  # noqa: E402
from engine.core import Backtest, coerce_strategy  # noqa: E402


def assert_nav_invariants(test: unittest.TestCase, result: dict):
    nav = result["nav"]
    test.assertGreater(len(nav), 0)
    lhs = nav["nav"].to_numpy(dtype=float)
    rhs = (nav["cash"] + nav["market_value"]).to_numpy(dtype=float)
    test.assertTrue(np.allclose(lhs, rhs, rtol=1e-9, atol=1e-6),
                    "nav must equal cash + market value on every day")
    test.assertTrue((nav["cash"].to_numpy(dtype=float) >= -1e-6).all(),
                    "cash may never go negative (no leverage)")
    ex = nav["exposure"].to_numpy(dtype=float)
    test.assertTrue((ex >= -1e-9).all() and (ex <= 1.0 + 1e-6).all(),
                    "exposure must stay within [0, 1]")
    # daily returns recompute from nav
    ret = nav["nav"].pct_change(fill_method=None).dropna()
    test.assertTrue(np.allclose(ret.to_numpy(), nav["ret"].to_numpy()[1:],
                                rtol=1e-9, atol=1e-12))


class TestAccountingInvariants(unittest.TestCase):
    def test_nav_identity_and_no_leverage_all_runs(self):
        runs = {
            "buyhold_stock": BuyHold("600001"),
            "buyhold_index": BuyHold("sh000300"),
            "cash": Cash(),
        }
        for label, strat in runs.items():
            with self.subTest(label=label):
                r = run_toy(strat)
                assert_nav_invariants(self, r)
                self.assertLess(abs(r["metrics"]["pnl_reconciliation_gap"]), 1.0,
                                "FIFO realized + unrealized must reconcile to total P&L")

    def test_buy_fees_are_sum_of_components(self):
        r = run_toy(BuyHold("600001"))
        trades = r["trades"]
        self.assertGreater(len(trades), 0)
        for _, t in trades.iterrows():
            self.assertAlmostEqual(
                t["fee_total"], t["commission"] + t["stamp"] + t["transfer"], places=6)
            if t["side"] == "buy":
                self.assertEqual(t["stamp"], 0.0)
        self.assertAlmostEqual(float(trades["fee_total"].sum()), r["total_fees"], places=6)

    def test_slippage_moves_execution_price(self):
        r0 = run_toy(BuyHold("600001"), slippage_bps=0.0)
        r1 = run_toy(BuyHold("600001"), slippage_bps=10.0)
        buy0 = trades_of(r0, side="buy").iloc[0]
        buy1 = trades_of(r1, side="buy").iloc[0]
        self.assertGreater(buy1["price"], buy0["price"])
        self.assertGreater(r1["total_slippage_cost"], 0.0)
        self.assertGreater(r1["total_fees"], 0.0)


class TestLots(unittest.TestCase):
    def test_buys_are_hundred_share_lots(self):
        for strat in (BuyHold("600001"), "equalweight:5"):
            r = run_toy(strat)
            buys = trades_of(r, side="buy")
            self.assertGreater(len(buys), 0)
            for shares in buys["shares"]:
                self.assertAlmostEqual(float(shares) % 100.0, 0.0, places=6)

    def test_no_trade_below_one_lot(self):
        bundle = load_toy_bundle()
        from engine.core import FeeModel
        bt = Backtest(bundle, coerce_strategy(Cash()), bundle._start_i, bundle._end_i,
                      capital=100_000.0)
        bt.i = bundle.i_of("2020-07-01")
        bt.date = bundle.date_at(bt.i)
        self.assertEqual(bt.max_affordable_shares(10.0, 100.0), 0)
        self.assertEqual(bt.max_affordable_shares(10.0, 1000.0), 0)   # 1000 + fees > cash
        self.assertEqual(bt.max_affordable_shares(10.0, 1100.0), 100)
        # a 50-CNY explicit buy cannot fill any lot and must not create a trade
        bt.cash = 100_000.0
        from engine.core import Context
        ctx = bt.ctx
        ctx.buy("600001", value=50.0)
        bt._collect_orders(bt.i, signal_nav=100_000.0, signal_date=bt.date)
        self.assertEqual(len(bt.pending_orders), 1)
        bt._execute_explicit_orders(bt.i, "open", sides=("buy",))
        self.assertEqual(len(bt.trades), 0)


class TestT1(unittest.TestCase):
    def test_same_day_sell_is_rejected(self):
        bundle = load_toy_bundle()
        bt = Backtest(bundle, coerce_strategy(Cash()), bundle._start_i, bundle._end_i,
                      capital=100_000.0)
        i0 = bundle.i_of("2020-07-01")
        bt.i, bt.date = i0, bundle.date_at(i0)
        filled, status, reason = bt._fill_stock("600001", "buy", 1000, i0, "open", "test")
        self.assertEqual(filled, 1000)
        self.assertEqual(status, "filled")
        filled, status, reason = bt._fill_stock("600001", "sell", 1000, i0, "open", "test")
        self.assertEqual(filled, 0)
        self.assertEqual(reason, "t1_locked")
        # next trading day the shares are unlocked and the sell succeeds
        i1 = i0 + 1
        bt.i, bt.date = i1, bundle.date_at(i1)
        bt._release_t1(i1)
        filled, status, reason = bt._fill_stock("600001", "sell", 1000, i1, "open", "test")
        self.assertEqual(filled, 1000)
        self.assertEqual(status, "filled")

    def test_integration_sell_not_same_day(self):
        def on_init(ctx):
            ctx.set_targets({"600001": 0.5})

        def on_bar(ctx):
            if ctx.date == "2020-06-03":
                ctx.set_targets({})

        r = run_toy({"name": "t1", "on_init": on_init, "on_bar": on_bar})
        trades = trades_of(r, code="600001")
        buy = trades[trades["side"] == "buy"].iloc[0]
        sell = trades[trades["side"] == "sell"].iloc[0]
        bundle = load_toy_bundle()
        gap = bundle.i_of(sell["date"]) - bundle.i_of(buy["date"])
        self.assertGreaterEqual(gap, 1)


class TestLimitBlocks(unittest.TestCase):
    def test_limit_up_blocks_buy_and_carries(self):
        def on_bar(ctx):
            if ctx.date == "2020-09-14":
                ctx.set_targets({"600003": 0.5})

        r = run_toy({"name": "lu", "on_bar": on_bar})
        trades = trades_of(r, code="600003", side="buy")
        self.assertEqual(len(trades), 1)
        # signal 2020-09-14 -> blocked at the 2020-09-15 limit-up open -> filled 09-16
        self.assertEqual(trades.iloc[0]["date"], "2020-09-16")
        blocked = events_of(r, "target_blocked")
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked.iloc[0]["date"], "2020-09-15")
        self.assertEqual(blocked.iloc[0]["reason"], "limit_up")
        self.assertEqual(blocked.iloc[0]["side"], "buy")

    def test_limit_down_blocks_sell_and_carries(self):
        def on_bar(ctx):
            if ctx.date == "2020-10-01":
                ctx.set_targets({"600004": 0.5})
            if ctx.date == "2020-10-19":
                ctx.set_targets({})

        r = run_toy({"name": "ld", "on_bar": on_bar})
        buy = trades_of(r, code="600004", side="buy")
        sell = trades_of(r, code="600004", side="sell")
        self.assertEqual(len(buy), 1)
        self.assertEqual(len(sell), 1)
        self.assertEqual(sell.iloc[0]["date"], "2020-10-21")   # 10-20 limit-down carry
        blocked = events_of(r, "target_blocked")
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked.iloc[0]["date"], "2020-10-20")
        self.assertEqual(blocked.iloc[0]["reason"], "limit_down")
        self.assertEqual(blocked.iloc[0]["side"], "sell")


class TestSuspensionCarry(unittest.TestCase):
    def test_missing_bars_carry_until_resume(self):
        def on_bar(ctx):
            if ctx.date == "2020-07-31":
                ctx.set_targets({"600005": 0.4})

        r = run_toy({"name": "susp", "on_bar": on_bar})
        trades = trades_of(r, code="600005", side="buy")
        self.assertEqual(len(trades), 1)
        # 600005 has no bars 2020-08-03..2020-08-14 -> first fill at the 2020-08-17 open
        self.assertEqual(trades.iloc[0]["date"], "2020-08-17")

    def test_tradestatus_zero_rows_carry(self):
        def on_bar(ctx):
            if ctx.date == "2020-08-31":
                ctx.set_targets({"600006": 0.4})

        r = run_toy({"name": "susp2", "on_bar": on_bar})
        trades = trades_of(r, code="600006", side="buy")
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades.iloc[0]["date"], "2020-09-11")  # first day after suspension

    def test_suspension_carries_orders_instead_of_dropping_them(self):
        def on_bar(ctx):
            if ctx.date == "2020-07-31":
                ctx.set_targets({"600005": 0.4})

        r = run_toy({"name": "susp3", "on_bar": on_bar})
        blocked = events_of(r, "target_blocked")
        self.assertGreaterEqual(len(blocked), 1)
        self.assertEqual(blocked.iloc[0]["reason"], "suspended")


class TestRestrictedNames(unittest.TestCase):
    def test_st_not_buyable_by_default(self):
        def on_bar(ctx):
            if ctx.date == "2020-07-01":
                ctx.set_targets({"600007": 0.5})

        r = run_toy({"name": "st", "on_bar": on_bar})
        self.assertEqual(len(trades_of(r, code="600007")), 0)
        blocked = events_of(r, "target_blocked")
        self.assertEqual(blocked.iloc[0]["reason"], "st_not_buyable")
        self.assertEqual(r["final_nav"], 100_000.0)

    def test_st_buyable_when_enabled(self):
        def on_bar(ctx):
            if ctx.date == "2020-07-01":
                ctx.set_targets({"600007": 0.5})

        r = run_toy({"name": "st2", "on_bar": on_bar}, allow_st_buy=True)
        self.assertEqual(len(trades_of(r, code="600007", side="buy")), 1)

    def test_ipo_day_not_traded_by_default(self):
        r = run_toy(BuyHold("600008"), max_pending_days=1000)
        trades = trades_of(r, code="600008", side="buy")
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades.iloc[0]["date"], "2021-01-05")   # IPO day 01-04 blocked
        blocked = events_of(r, "target_blocked")
        reasons = set(blocked["reason"].astype(str))
        self.assertIn("not_listed", reasons)   # before the first bar
        self.assertIn("ipo_day", reasons)      # on the IPO day itself

    def test_delisting_forced_liquidation(self):
        r = run_toy(BuyHold("600009"))
        forced = trades_of(r, code="600009")
        forced = forced[forced["reason"] == "delisting_forced_liquidation"]
        self.assertEqual(len(forced), 1)
        self.assertEqual(forced.iloc[0]["date"], "2021-03-01")
        ev = events_of(r, "delisting_forced_liquidation")
        self.assertEqual(len(ev), 1)
        self.assertNotIn("600009", r["holdings_final"])
        self.assertGreater(r["final_cash"], 0.0)
        assert_nav_invariants(self, r)


class TestCashAndExposure(unittest.TestCase):
    def test_full_cash_run(self):
        r = run_toy(Cash())
        nav = r["nav"]
        self.assertEqual(r["final_nav"], 100_000.0)
        self.assertTrue((nav["nav"] == 100_000.0).all())
        self.assertEqual(r["metrics"]["trade_count"], 0)
        self.assertEqual(r["metrics"]["all_cash_days"], len(nav))
        self.assertEqual(r["metrics"]["avg_exposure"], 0.0)
        self.assertEqual(r["metrics"]["total_return"], 0.0)

    def test_index_proxy_buy_hold(self):
        r = run_toy(BuyHold("sh000300"))
        self.assertEqual(r["metrics"]["avg_exposure"], r["metrics"]["avg_exposure"])
        self.assertGreater(r["metrics"]["avg_exposure"], 0.99)
        self.assertGreater(len(trades_of(r)), 0)
        self.assertFalse(np.isnan(r["metrics"]["total_return"]))


if __name__ == "__main__":
    unittest.main()
