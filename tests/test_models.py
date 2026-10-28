import datetime as dt
import unittest

try:
    from quantscope.engine import evaluate
    from quantscope.models.quantvela.rules import three_state, volatility_weights
    from quantscope.models.quantorion.rules import select_candidates
    from quantscope.models.quantlibra.rules import decide
    from quantscope.models.quantpyxis.rules import rank_candidates, state_at
except ImportError:
    evaluate = None


class Models(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(evaluate, "统一模型尚未实现")

    def test_vela_three_states_and_equal_boundary(self):
        self.assertEqual(three_state([100.0] * 199 + [110.0]), {"TQQQ.US": 1.0})
        self.assertEqual(three_state([100.0] * 199 + [90.0]), {"QQQ.US": 0.5})
        self.assertEqual(three_state([100.0] * 200), {"QQQ.US": 1.0})

    def test_vela_exposure_interpolation(self):
        w = volatility_weights(1.5)
        self.assertEqual(w, {"QQQ.US": 0.5, "QLD.US": 0.5})
        self.assertEqual(volatility_weights(2.5), {"QLD.US": 0.5, "TQQQ.US": 0.5})
        with self.assertRaises(ValueError):
            volatility_weights(3)

    def test_orion_threshold_and_volume(self):
        rows = [
            dict(
                symbol="600001.SH",
                pct_chg=9.95,
                prev_pct_chg=10,
                volume=90,
                prev_volume=100,
            ),
            dict(
                symbol="300001.SZ",
                pct_chg=19.9,
                prev_pct_chg=20,
                volume=100,
                prev_volume=100,
            ),
            dict(
                symbol="830001.BJ",
                pct_chg=30,
                prev_pct_chg=30,
                volume=1,
                prev_volume=100,
            ),
        ]
        self.assertEqual(select_candidates(rows), ["600001.SH"])
        self.assertEqual(select_candidates(rows, volume_ratio=0.7), [])

    def test_libra_cash_overrides_cooldown(self):
        closes = {
            c: [100 - i for i in range(26)]
            for c in ["510880", "159915", "513100", "518880"]
        }
        self.assertIsNone(
            decide(
                closes,
                {"holding": "518880", "entry_date": "2026-10-01"},
                dt.date(2026, 10, 2),
            )["target"]
        )

    def test_pyxis_rank_before_eligibility_and_ties(self):
        rows = [
            dict(symbol="600001.SH", ras=1, m6=1, inv=1, eligible=True, raw=10),
            dict(symbol="600002.SH", ras=3, m6=3, inv=3, eligible=False, raw=10),
            dict(symbol="600003.SH", ras=1, m6=1, inv=1, eligible=True, raw=10),
        ]
        ranked = rank_candidates(rows)
        self.assertEqual([x[0] for x in ranked], ["600001.SH", "600003.SH"])
        self.assertAlmostEqual(ranked[0][1], 0.5)

    def test_pyxis_frozen_date_expires(self):
        self.assertIn(state_at("2026-10-19"), ["B", "N", "E", "C"])
        with self.assertRaises(ValueError):
            state_at("2026-10-20")

    def test_invalid_data_is_not_cash(self):
        s = evaluate(
            {
                "model": "quantvela",
                "signal_date": "2026-10-19",
                "execute_after": "2026-10-20",
                "data": {"history": {}},
            }
        )
        self.assertEqual(s.status, "invalid_data")
        self.assertIsNone(s.cash_weight)
        self.assertEqual(s.targets, {})

    def test_missing_and_future_dates(self):
        for nxt in ["2026-10-19", "2026-10-18"]:
            s = evaluate(
                {
                    "model": "quantorion",
                    "signal_date": "2026-10-19",
                    "execute_after": nxt,
                    "data": {},
                }
            )
            self.assertEqual(s.status, "invalid_data")
