# -*- coding: utf-8 -*-
"""Short-window end-to-end test of run_round3.main() (does not touch out/)."""
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_round3 as R

R.START = "2019-01-01"
R.END = "2020-12-31"
R.OUT = HERE / "out_test"
rc = R.main()
print("rc =", rc)
