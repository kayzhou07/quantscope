# -*- coding: utf-8 -*-
"""Frozen configuration for the round-5 paper-trading infrastructure (strategy A)."""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent               # round5/paper
R5 = HERE.parent                                      # round5
WS = HERE.parents[2]                                  # default-workspace
REPO = WS / "qrun_1416"
SEAL = R5 / "SEAL"
RUNS = HERE / "runs"
DATA = REPO / "data"
PANEL = DATA / "panel"
META = DATA / "meta"

with open(SEAL / "A_CONFIG.json", encoding="utf-8") as fh:
    A = json.load(fh)
with open(SEAL / "FREEZE_MANIFEST.json", encoding="utf-8") as fh:
    FREEZE = json.load(fh)

FREEZE_AT = FREEZE["freeze_at"]
FREEZE_ID = FREEZE["freeze_id"]
DATA_CUTOFF = FREEZE["data_cutoff"]
PRIMARY_ACCOUNT = "cash_100k_new"
SHADOW_ACCOUNT = "inherit_history_274560.69"
SHADOW_START_NAV = 274560.69

EXPOSURE = {k: float(v) for k, v in A["exposure"].items()}
TOP_N = int(A["selection"]["top_n"])
BUFFER = int(A["selection"]["retention_buffer_rank"])
VOL_CAP = float(A["selection"]["vol_cap"])
MIN_PRICE = float(A["selection"]["min_raw_close"])
MIN_AMOUNT = float(A["selection"]["min_amount_60d_mean"])
W_RAS, W_M6, W_INV = (float(A["selection"]["score"]["w_ras"]),
                      float(A["selection"]["score"]["w_m6"]),
                      float(A["selection"]["score"]["w_inv_vol"]))
SLIP = float(A["costs"]["slippage_bps_per_side"]) / 1e4
COMM = float(A["costs"]["commission_bps"]) / 1e4
MIN_COMM = float(A["costs"]["min_commission_cny"])
TRANSFER = float(A["costs"]["transfer_fee"])
STAMP_BEFORE = 0.001
STAMP_FROM = 0.0005
STAMP_SWITCH = "2023-08-28"
LOT = 100
MAX_PENDING_DAYS = int(A["engine"]["max_pending_days"])

SCHEMA_SIGNAL = "round5-signal-1"
SCHEMA_LEDGER = "round5-paper-ledger-1"
SCHEMA_AUDIT = "round5-daily-audit-1"
