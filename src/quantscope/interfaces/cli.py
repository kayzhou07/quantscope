"""quantscope models | signal INPUT | portfolio INPUT | export INPUT | serve"""

import argparse
import json
from pathlib import Path
from quantscope.core import MODELS, Signal
from quantscope.engine import evaluate
from quantscope.portfolio.allocation import combine
from quantscope.adapters.supermind import export_target


def read(path):
    return json.loads(
        Path(path).read_text(encoding="utf-8"),
        parse_constant=lambda x: (_ for _ in ()).throw(ValueError("非有限JSON常量")),
    )


def load_signal(raw):
    return Signal(
        **{k: v for k, v in raw.items() if k not in ("schema_version", "cash_weight")}
    )


def main(argv=None):
    parser = argparse.ArgumentParser(prog="quantscope")
    parser.add_argument(
        "command", choices=["models", "signal", "portfolio", "export", "serve"]
    )
    parser.add_argument("input", nargs="?")
    parser.add_argument("--output")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            from .http import serve

            serve(args.port)
            return 0
        if args.command == "models":
            result = {"models": MODELS}
        else:
            if not args.input:
                parser.error("此命令需要JSON输入路径")
            raw = read(args.input)
            if args.command == "signal":
                result = evaluate(raw).to_dict()
            elif args.command == "export":
                result = export_target(load_signal(raw))
            else:
                result = combine(
                    [load_signal(s) for s in raw["signals"]],
                    raw["budgets"],
                    **raw.get("limits", {}),
                ).to_dict()
        text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
        else:
            print(text, end="")
        return 0 if result.get("status", "ok") in ("ok", "no_candidates") else 2
    except (ValueError, KeyError, TypeError, OSError) as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 2
