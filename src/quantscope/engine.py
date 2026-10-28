"""四模型唯一离线入口。拒绝把异常当作成功空仓。"""

from quantscope.core import MODELS, Signal, dates
from quantscope.data.validation import history
from quantscope.models.quantvela import rules as vela
from quantscope.models.quantorion import rules as orion
from quantscope.models.quantlibra import rules as libra
from quantscope.models.quantpyxis import rules as pyxis


def evaluate(request):
    model = request.get("model")
    if model not in MODELS:
        raise ValueError("model必须为四个统一模型ID之一")
    day = request.get("signal_date", "")
    nxt = request.get("execute_after", "")
    try:
        dates(day, nxt)
        data = request["data"]
        diagnostics = {}
        count = {
            "quantvela": 200,
            "quantorion": 2,
            "quantlibra": 25 if data.get("variant") == "platform_v6_25" else 26,
            "quantpyxis": 253,
        }[model]
        histories = history(data, day, count)
        if model == "quantvela":
            weights, risk = vela.evaluate(data, histories)
        elif model == "quantorion":
            weights, risk = orion.evaluate(data, histories)
        elif model == "quantlibra":
            weights, diagnostics = libra.evaluate(data, histories, day)
            risk = "rotation"
        else:
            weights, diagnostics = pyxis.evaluate(data, histories, day)
            risk = diagnostics["state"]
        return Signal(
            model,
            MODELS[model]["version"],
            day,
            nxt,
            "ok" if weights else "no_candidates",
            weights,
            risk,
            "离线研究目标；未提交交易；执行日期须由市场日历核对",
            diagnostics,
        )
    except pyxis.FrozenRegimeExpired as e:
        return Signal(
            model, MODELS[model]["version"], day, nxt, "expired", reason=str(e)
        )
    except (ValueError, KeyError, TypeError, OverflowError) as e:
        return Signal(
            model, MODELS[model]["version"], day, nxt, "invalid_data", reason=str(e)
        )
