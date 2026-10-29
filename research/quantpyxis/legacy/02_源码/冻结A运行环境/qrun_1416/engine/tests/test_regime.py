"""Regime-label tests: lag semantics, no lookahead, frozen classification."""

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

from fixture_utils import TOY, make_tmp_dir, perturb_fixture  # noqa: E402

from engine.regime import (  # noqa: E402
    CRASH_RET20,
    build_regime_labels,
    classify_frame,
    yearly_counts,
)

CUTOFF = "2021-01-01"
START, END = "2020-06-01", "2021-12-31"
COMPONENTS = ["trend", "slope", "dd250_score", "vol_regime"]
EXTRA = ["breadth", "nhnl", "turnover"]


class TestRegimeLabels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = make_tmp_dir("regime")
        cls.labels = build_regime_labels(TOY, start=START, end=END, warmup_days=40,
                                         out_csv=cls.tmp / "labels.csv",
                                         include_breadth=True, verbose=False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_basic_shape(self):
        df = self.labels
        self.assertGreater(len(df), 250)
        self.assertEqual(df["date"].min(), START)
        self.assertEqual(df["date"].max(), END)
        self.assertEqual(set(df["regime"].unique()) - {"bull", "bear", "neutral", "unknown"}, set())
        self.assertTrue((df["variant"] == "full").all())
        self.assertTrue((self.tmp / "labels.csv").exists())
        required = ["date", "regime", "score", "variant", "trend", "slope",
                    "dd250_score", "vol_regime", "pct_above_ma60", "turnover_z"]
        for col in required:
            self.assertIn(col, df.columns)

    def test_label_is_shifted_by_one_day(self):
        """label at T must equal the classification of the indicators known at T-1."""
        score = self.labels["score"].to_numpy(dtype=float)
        expected = []
        for idx in range(len(self.labels)):
            if idx == 0:
                expected.append(np.nan)
                continue
            row = self.labels.iloc[idx - 1]
            total = sum(float(row[c]) for c in COMPONENTS)
            if np.isfinite(row["breadth"]):
                total += float(row["breadth"])
            if np.isfinite(row["nhnl"]):
                total += float(row["nhnl"])
            if np.isfinite(row["turnover"]):
                total += float(row["turnover"])
            if not np.isfinite(row[COMPONENTS].astype(float)).any():
                expected.append(np.nan)
            else:
                expected.append(total)
        got = score[1:]
        exp = np.array(expected[1:], dtype=float)
        mask = np.isfinite(exp) & np.isfinite(got)
        self.assertGreater(int(mask.sum()), 100)
        np.testing.assert_allclose(got[mask], exp[mask], atol=1e-9)

    def test_classification_thresholds_are_frozen(self):
        # the first 250 bars are warmup (score NaN -> 'unknown'); after that every row
        # must have an actionable non-unknown label
        self.assertGreaterEqual(int(self.labels["score"].notna().sum()), 100)
        warmup_rows = int(self.labels["regime"].eq("unknown").sum())
        self.assertGreater(warmup_rows, 200)
        tail = self.labels.iloc[warmup_rows:]
        self.assertTrue((tail["regime"] != "unknown").all())
        self.assertTrue((tail["variant"] == "full").all())

    def test_no_lookahead_under_future_perturbation(self):
        tmp = make_tmp_dir("regime_pert")
        try:
            pert = perturb_fixture(TOY, tmp / "toy_perturbed", cutoff=CUTOFF, factor=1.9)
            labels_b = build_regime_labels(pert, start=START, end=END, warmup_days=40,
                                           include_breadth=True, verbose=False)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        a = self.labels[self.labels["date"] <= CUTOFF].reset_index(drop=True)
        b = labels_b[labels_b["date"] <= CUTOFF].reset_index(drop=True)
        self.assertGreater(len(a), 100)
        self.assertEqual(len(a), len(b))
        pd.testing.assert_series_equal(a["regime"], b["regime"])
        np.testing.assert_allclose(a["score"].to_numpy(dtype=float),
                                   b["score"].to_numpy(dtype=float), atol=1e-12)
        for col in ("trend", "slope", "dd250_score", "vol_regime", "breadth", "nhnl"):
            np.testing.assert_allclose(a[col].to_numpy(dtype=float),
                                       b[col].to_numpy(dtype=float), atol=1e-12)

    def test_yearly_counts(self):
        counts = yearly_counts(self.labels)
        self.assertIn("bull", counts.columns)
        self.assertEqual(int(counts["total"].sum()), len(self.labels))
        self.assertTrue((counts["total"] > 0).all())


class TestClassifyFrame(unittest.TestCase):
    def _frame(self, rows):
        idx = [f"2021-01-{i + 4:02d}" for i in range(len(rows))]
        df = pd.DataFrame(rows, index=idx)
        for col in COMPONENTS + EXTRA:
            if col not in df.columns:
                df[col] = 0.0
        df["ret20"] = 0.0
        return df

    def test_bull_and_bear_full_rule(self):
        df = self._frame([
            {"trend": 1, "slope": 1, "dd250_score": 1, "vol_regime": 0},
            {"trend": -1, "slope": -1, "dd250_score": -1, "vol_regime": 0},
            {"trend": 0, "slope": 0, "dd250_score": 0, "vol_regime": 0},
        ])
        reg, score = classify_frame(df, "full")
        self.assertEqual(reg.iloc[0], "bull")
        self.assertEqual(reg.iloc[1], "bear")
        self.assertEqual(reg.iloc[2], "neutral")
        self.assertEqual(score.iloc[0], 3.0)

    def test_index_only_variant_thresholds(self):
        df = self._frame([
            {"trend": 1, "slope": 1, "dd250_score": 0, "vol_regime": 0},
            {"trend": -1, "slope": -1, "dd250_score": 0, "vol_regime": 0},
            {"trend": 1, "slope": 0, "dd250_score": 0, "vol_regime": 0},
        ])
        reg, _ = classify_frame(df, "index_only")
        self.assertEqual(reg.iloc[0], "bull")
        self.assertEqual(reg.iloc[1], "bear")
        self.assertEqual(reg.iloc[2], "neutral")   # score=1 < 2

    def test_crash_override(self):
        df = self._frame([{"trend": 1, "slope": 1, "dd250_score": 1, "vol_regime": 1}])
        df["ret20"] = CRASH_RET20 - 0.01
        reg, _ = classify_frame(df, "full")
        self.assertEqual(reg.iloc[0], "bear")

    def test_unknown_when_history_missing(self):
        df = self._frame([{"trend": np.nan, "slope": np.nan, "dd250_score": np.nan,
                           "vol_regime": np.nan}])
        for col in COMPONENTS:
            df[col] = np.nan
        reg, _ = classify_frame(df, "full")
        self.assertEqual(reg.iloc[0], "unknown")


if __name__ == "__main__":
    unittest.main()
