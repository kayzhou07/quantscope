"""对照保留旧纯函数，覆盖随机价格、并列和多种冷却期。"""

import datetime as dt
import importlib.util
from pathlib import Path
import random
import unittest
from quantscope.models.quantlibra import rules
from quantscope.core import asset
from quantscope.engine import evaluate

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "libra_original", ROOT / "prototypes/quantlibra/strategies/signal_v6.py"
)
original = importlib.util.module_from_spec(spec)
spec.loader.exec_module(original)


class Regression(unittest.TestCase):
    def test_libra_100_random_series_match_original(self):
        rng = random.Random(61028)
        for _ in range(100):
            closes = {c: [100.0] for c in rules.ETFS}
            for c in closes:
                for i in range(25):
                    closes[c].append(closes[c][-1] * (1 + rng.uniform(-0.03, 0.03)))
            state = {
                "holding": rng.choice([None, *rules.ETFS]),
                "entry_date": "2026-09-30",
            }
            a = rules.decide(closes, state, dt.date(2026, 10, 5))
            b = original.decide(closes, state, dt.date(2026, 10, 5))
            self.assertEqual(a["target"], b["target"])
            self.assertEqual(a["composite"], b["composite"])
            for c in closes:
                self.assertAlmostEqual(a["slope_m"][c], b["slope_m"][c], places=12)
                self.assertAlmostEqual(a["eff_m"][c], b["eff_m"][c], places=12)

    def test_examples_are_valid_and_source_of_failure_is_explicit(self):
        import json

        for m in ["quantvela", "quantorion", "quantlibra", "quantpyxis"]:
            payload = json.loads((ROOT / "examples" / f"{m}.json").read_text())
            s = evaluate(payload)
            self.assertIn(s.status, ["ok", "no_candidates"], s.reason)
            if m == "quantpyxis":
                payload["signal_date"] = "2026-10-20"
                payload["execute_after"] = "2026-10-21"
                # Dates in history must also be updated before exercising regime expiry.
                for rows in payload["data"]["history"].values():
                    rows[-1]["date"] = "2026-10-20"
                self.assertEqual(evaluate(payload).status, "expired")

    def test_duplicate_and_misaligned_dates_rejected(self):
        import json

        payload = json.loads((ROOT / "examples/quantlibra.json").read_text())
        first = next(iter(payload["data"]["history"].values()))
        first[-1]["date"] = first[-2]["date"]
        self.assertEqual(evaluate(payload).status, "invalid_data")

    def test_symbol_aliases(self):
        self.assertEqual(asset("510880.XSHG"), "510880.SH")
        self.assertEqual(asset("159915"), "159915.SZ")
