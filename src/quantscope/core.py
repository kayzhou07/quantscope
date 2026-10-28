"""统一信号契约。目标权重是组合意图，不是已成交持仓。"""

from dataclasses import asdict, dataclass, field
from datetime import date
from math import isfinite
import re

MODELS = {
    "quantvela": {"name": "量寰 QuantVela", "role": "research", "version": "1.0.0"},
    "quantorion": {"name": "量曜 QuantOrion", "role": "model", "version": "1.0.0"},
    "quantlibra": {"name": "量衡 QuantLibra", "role": "model", "version": "1.0.0"},
    "quantpyxis": {"name": "量枢 QuantPyxis", "role": "model", "version": "1.0.0"},
}


def asset(code):
    code = str(code).upper()
    if re.fullmatch(r"[A-Z][A-Z0-9-]*\.US", code):
        return code
    suffix = {"XSHG": "SH", "XSHE": "SZ"}
    parts = code.split(".")
    if len(parts) == 2:
        code = parts[0] + "." + suffix.get(parts[1], parts[1])
    elif re.fullmatch(r"\d{6}", code):
        code += ".SH" if code[0] in "569" else ".SZ"
    if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code):
        raise ValueError("资产代码格式无效")
    return code


def dates(signal_date, execute_after):
    a = date.fromisoformat(signal_date)
    b = date.fromisoformat(execute_after)
    if b <= a:
        raise ValueError("execute_after必须晚于完整信号日；交易日由外部日历确认")


@dataclass
class Signal:
    model: str
    version: str
    signal_date: str
    execute_after: str
    status: str
    targets: dict[str, float] = field(default_factory=dict)
    risk_state: str = "unknown"
    reason: str = ""
    diagnostics: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.status not in (
            "ok",
            "no_candidates",
            "invalid_data",
            "expired",
            "error",
        ):
            raise ValueError("未知信号状态")
        normalized = {}
        for k, v in self.targets.items():
            k = asset(k)
            v = float(v)
            if k in normalized:
                raise ValueError("资产别名重复")
            if not isfinite(v) or v < 0:
                raise ValueError("权重必须有限且非负")
            normalized[k] = v
        if sum(normalized.values()) > 1 + 1e-9:
            raise ValueError("本接口禁止融资及权重合计超过1")
        if self.status not in ("ok", "no_candidates") and normalized:
            raise ValueError("失败信号不能携带可执行目标")
        self.targets = normalized

    @property
    def cash_weight(self):
        if self.status not in ("ok", "no_candidates"):
            return None
        return max(0.0, 1 - sum(self.targets.values()))

    def to_dict(self):
        return {
            **asdict(self),
            "cash_weight": self.cash_weight,
            "schema_version": "1.0",
        }
