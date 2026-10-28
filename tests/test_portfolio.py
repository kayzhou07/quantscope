import unittest

try:
    from quantscope.core import Signal
    from quantscope.portfolio.allocation import combine
    from quantscope.adapters.supermind import export_target
except ImportError:
    Signal = None


def signal(model="quantorion", targets=None, date="2026-10-19", status="ok"):
    return Signal(
        model=model,
        version="1.0.0",
        signal_date=date,
        execute_after="2026-10-20",
        status=status,
        targets=targets or {},
        risk_state="test",
        reason="test",
    )


class Portfolio(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(Signal, "统一组合接口尚未实现")

    def test_combination_and_cash(self):
        s = combine(
            [
                signal(targets={"600001.SH": 1}),
                signal("quantlibra", {"600001.SH": 0.5}),
            ],
            {"quantorion": 0.4, "quantlibra": 0.4},
            max_asset=1,
        )
        self.assertAlmostEqual(s.targets["600001.SH"], 0.6)
        self.assertAlmostEqual(s.cash_weight, 0.4)

    def test_reject_inconsistent_date_failed_signal_research_and_duplicate(self):
        cases = [
            [signal(), signal("quantlibra", date="2026-10-18")],
            [signal(status="invalid_data")],
            [signal("quantvela")],
            [signal(), signal()],
        ]
        for signals in cases:
            with self.assertRaises(ValueError):
                combine(signals, {s.model: 0.3 for s in signals}, max_asset=1)

    def test_reject_over_budget_and_missing_model(self):
        with self.assertRaises(ValueError):
            combine([signal()], {"quantorion": 1.1})
        with self.assertRaises(ValueError):
            combine([signal()], {"quantorion": 0.5, "quantlibra": 0.5})

    def test_asset_and_sector_limit(self):
        with self.assertRaises(ValueError):
            combine(
                [signal(targets={"600001.SH": 1})], {"quantorion": 1}, max_asset=0.2
            )
        with self.assertRaises(ValueError):
            combine(
                [signal(targets={"600001.SH": 0.5, "600002.SH": 0.5})],
                {"quantorion": 1},
                max_asset=1,
                sectors={"600001.SH": "A", "600002.SH": "A"},
                max_sector=0.6,
            )

    def test_export_is_not_an_order(self):
        d = export_target(signal(targets={"600001.SH": 0.2}))
        self.assertEqual(d["mode"], "target_snapshot_only")
        self.assertFalse(d["can_submit_orders"])
        with self.assertRaises(ValueError):
            export_target(signal("quantvela"))

    def test_weights_reject_nan_negative_or_oversum(self):
        for w in [{"600001.SH": float("nan")}, {"600001.SH": -0.1}, {"600001.SH": 1.1}]:
            with self.assertRaises(ValueError):
                signal(targets=w)
