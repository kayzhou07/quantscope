# -*- coding: utf-8 -*-
"""Build the wide qfq/raw price cache for the codes strategy A ever held.

Reads only qrun_1416/data/panel/bars_qfq_*.csv.gz and bars_*.csv.gz (read-only);
writes out/cache/panel_wide.pkl under round5/attrib.
"""
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import attrib_common as C  # noqa: E402


def main():
    led = C.load_ledgers()
    codes = (set(led["positions"]["code"]) | set(led["books"]["code"])
             | set(led["trades"]["code"]))
    print(f"union codes: {len(codes)}")
    data = C.build_panel_cache(codes)
    print("cached:", sorted(data["fields"]), "->", C.CACHE / "panel_wide.pkl")


if __name__ == "__main__":
    main()
