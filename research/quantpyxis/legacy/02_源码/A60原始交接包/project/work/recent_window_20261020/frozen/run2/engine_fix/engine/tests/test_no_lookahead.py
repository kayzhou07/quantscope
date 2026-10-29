"""No-lookahead tests: perturbing future data must not change past signals/trades."""

import hashlib
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

from fixture_utils import (  # noqa: E402
    TOY,
    make_tmp_dir,
    perturb_fixture,
    run_toy,
)

from engine.core import Strategy  # noqa: E402

CUTOFF = "2021-01-01"


class SignalRecorder(Strategy):
    """Records everything a strategy could base a decision on, plus trades monthly."""

    name = "signal_recorder"
    warmup = 0
    rebalance = "M"

    def on_init(self, ctx):
        self.records = []
        self._record(ctx, phase="init")

    def _record(self, ctx, phase):
        mat = ctx.history_matrix("close_qfq", 60)[1]
        mat = np.asarray(mat, dtype=np.float64)
        digest = hashlib.sha1(np.nan_to_num(mat, nan=-999.0).tobytes()).hexdigest()[:16]
        idx_ret = ctx.index_returns(60, "sh000300")
        dates = ctx.dates()
        self.records.append({
            "date": ctx.date,
            "phase": phase,
            "digest": digest,
            "nav": round(float(ctx.nav), 6),
            "hist_last_date": dates[-1] if dates else "",
            "n_dates": len(dates),
            "index_ret_sum": round(float(idx_ret.sum()) if len(idx_ret) else 0.0, 8),
        })

    def on_bar(self, ctx):
        self._record(ctx, phase="bar")

    def on_rebalance(self, ctx):
        self._record(ctx, phase="rebalance")
        codes = ctx.top_by("amount", 2, 20)
        if codes:
            ctx.set_targets({c: 1.0 / len(codes) for c in codes})


def run_with_recorder(data_dir, cache_dir=None):
    strat = SignalRecorder()
    from engine.api import run_backtest
    result = run_backtest(strat, start="2020-06-01", end="2021-12-31",
                          data_dir=data_dir, warmup_days=40, slippage_bps=0.0,
                          cache_dir=cache_dir, rebalance="M")
    return result, pd.DataFrame(strat.records)


class TestNoLookahead(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = make_tmp_dir("lookahead")
        try:
            cls.perturbed = perturb_fixture(TOY, cls.tmp / "toy_perturbed",
                                            cutoff=CUTOFF, factor=1.7)
            cls.cache_a = cls.tmp / "cache_a"
            cls.cache_b = cls.tmp / "cache_b"
            cls.base_result, cls.base_records = run_with_recorder(TOY, cls.cache_a)
            cls.pert_result, cls.pert_records = run_with_recorder(cls.perturbed, cls.cache_b)
        except Exception:
            shutil.rmtree(cls.tmp, ignore_errors=True)
            raise

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_history_never_contains_future_rows(self):
        for rec in self.base_records.itertuples():
            self.assertLessEqual(rec.hist_last_date, rec.date)

    def test_signals_up_to_cutoff_identical(self):
        base = self.base_records[self.base_records["date"] <= CUTOFF].reset_index(drop=True)
        pert = self.pert_records[self.pert_records["date"] <= CUTOFF].reset_index(drop=True)
        self.assertGreater(len(base), 100)
        self.assertEqual(len(base), len(pert))
        pd.testing.assert_frame_equal(base, pert, check_dtype=False)

    def test_nav_up_to_cutoff_identical(self):
        a = self.base_result["nav"].loc[:CUTOFF]
        b = self.pert_result["nav"].loc[:CUTOFF]
        self.assertEqual(len(a), len(b))
        np.testing.assert_allclose(a["nav"].to_numpy(), b["nav"].to_numpy(), rtol=0, atol=1e-9)
        np.testing.assert_allclose(a["cash"].to_numpy(), b["cash"].to_numpy(), rtol=0, atol=1e-9)

    def test_trades_up_to_cutoff_identical(self):
        ta = self.base_result["trades"]
        tb = self.pert_result["trades"]
        ta = ta[ta["date"] <= CUTOFF].reset_index(drop=True)
        tb = tb[tb["date"] <= CUTOFF].reset_index(drop=True)
        self.assertGreater(len(ta), 0)
        pd.testing.assert_frame_equal(ta, tb, check_dtype=False)

    def test_future_data_does_change_later_signals(self):
        # sanity: the perturbation must actually matter after the cutoff
        a = self.base_records[self.base_records["date"] > CUTOFF]["nav"].to_numpy()
        b = self.pert_records[self.pert_records["date"] > CUTOFF]["nav"].to_numpy()
        self.assertFalse(np.allclose(a, b))


if __name__ == "__main__":
    unittest.main()
