"""Data-contract layer tests (calendar, panel pivots, universe, indices, errors)."""

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

from fixture_utils import TOY, make_tmp_dir  # noqa: E402

from engine.data import (  # noqa: E402
    DataBundle,
    DataNotReadyError,
    data_status,
    normalize_code,
)


class TestNormalize(unittest.TestCase):
    def test_normalize_code(self):
        self.assertEqual(normalize_code("sh600000"), "600000")
        self.assertEqual(normalize_code("600000.SH"), "600000")
        self.assertEqual(normalize_code(1), "000001")
        self.assertEqual(normalize_code("000001"), "000001")


class TestDataStatusAndBundle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = DataBundle.load(TOY, start="2020-06-01", end="2021-12-31",
                                     warmup_days=40, verbose=False)
        cls.status = data_status(TOY)

    def test_status(self):
        self.assertTrue(self.status["index_ready"])
        self.assertTrue(self.status["panel_ready"])
        self.assertEqual(self.status["raw_years"], [2020, 2021])
        self.assertEqual(self.status["qfq_years"], [2020, 2021])

    def test_axes(self):
        self.assertEqual(len(self.bundle.dates), 415)
        self.assertEqual(len(self.bundle.codes), 11)
        self.assertEqual(self.bundle.date_at(0), "2020-06-01")
        self.assertEqual(self.bundle.date_at(len(self.bundle.dates) - 1), "2021-12-31")
        self.assertEqual(self.bundle._start_i, 0)
        self.assertEqual(self.bundle._end_i, 414)
        self.assertTrue(self.bundle.panel.qfq_available)

    def test_fields_are_pivoted(self):
        p = self.bundle.panel
        T, N = len(self.bundle.dates), len(self.bundle.codes)
        for name in ("open", "high", "low", "close", "preclose", "volume", "amount",
                     "turn", "pctchg", "isst", "tradestatus",
                     "qfq_open", "qfq_close", "qfq_high", "qfq_low"):
            arr = p.field(name)
            self.assertEqual(arr.shape, (T, N))
        j = self.bundle.code_index["600001"]
        i = self.bundle.i_of("2020-07-01")
        self.assertGreater(float(p.field("close")[i, j]), 0.0)
        self.assertAlmostEqual(float(p.field("isst")[i, self.bundle.code_index["600007"]]), 1.0)

    def test_missing_bar_is_nan_and_tradestatus_zero(self):
        j = self.bundle.code_index["600005"]
        i = self.bundle.i_of("2020-08-05")   # suspended (missing rows)
        self.assertTrue(np.isnan(float(self.bundle.field("close")[i, j])))
        self.assertEqual(float(self.bundle.field("tradestatus")[i, j]), 0.0)
        j2 = self.bundle.code_index["600006"]
        i2 = self.bundle.i_of("2020-09-02")  # tradestatus=0 rows exist
        self.assertEqual(float(self.bundle.field("tradestatus")[i2, j2]), 0.0)

    def test_qfq_vs_raw(self):
        j = self.bundle.code_index["600010"]
        i = self.bundle.i_of("2021-05-10")
        raw = float(self.bundle.field("close")[i, j])
        qfq = float(self.bundle.field("qfq_close")[i, j])
        self.assertNotAlmostEqual(raw, qfq, places=2)   # split-adjusted series differs

    def test_universe(self):
        uni = self.bundle.universe
        j = self.bundle.code_index["600008"]
        i_before = self.bundle.i_of("2020-12-31")
        i_after = self.bundle.i_of("2021-01-05")
        mask = uni.listed_mask()
        self.assertFalse(bool(mask[i_before, j]))
        self.assertTrue(bool(mask[i_after, j]))
        j_del = self.bundle.code_index["600009"]
        self.assertEqual(uni.out_date(j_del), "2021-03-01")
        self.assertEqual(uni.board[self.bundle.code_index["300001"]], "chinext")

    def test_indices(self):
        self.assertIn("sh000300", self.bundle.index_dict)
        self.assertIn("sz399006", self.bundle.index_dict)
        close = self.bundle.index_close_series("sh000300")
        self.assertGreater(len(close), 400)
        self.assertAlmostEqual(float(close.loc["2020-06-01"]), 3800.0, places=2)

    def test_history_windows(self):
        i = self.bundle.i_of("2021-01-04")
        mat = self.bundle.history("close", i, 60, cols=self.bundle.col_index(["600001"]))
        self.assertEqual(mat.shape, (60, 1))
        self.assertTrue(np.isfinite(mat[-1, 0]))

    def test_error_on_missing_range(self):
        with self.assertRaises(DataNotReadyError):
            DataBundle.load(TOY, start="1990-01-01", end="1990-12-31", warmup_days=0)

    def test_missing_year_raises(self):
        tmp = make_tmp_dir("missing_year")
        try:
            for sub in ("meta", "panel"):
                (tmp / sub).mkdir()
            shutil.copy(TOY / "meta" / "calendar.csv", tmp / "meta" / "calendar.csv")
            shutil.copy(TOY / "meta" / "universe.csv", tmp / "meta" / "universe.csv")
            shutil.copy(TOY / "panel" / "index_daily.csv", tmp / "panel" / "index_daily.csv")
            # only 2020 bars published, but the requested range spans 2021
            shutil.copy(TOY / "panel" / "bars_2020.csv", tmp / "panel" / "bars_2020.csv")
            shutil.copy(TOY / "panel" / "bars_qfq_2020.csv",
                        tmp / "panel" / "bars_qfq_2020.csv")
            with self.assertRaises(DataNotReadyError):
                DataBundle.load(tmp, start="2020-06-01", end="2021-12-31",
                                warmup_days=0, require_panel=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
