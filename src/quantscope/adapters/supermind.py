"""离线目标导出。没有未经验证的SDK、远程端点或真实下单调用。"""

from quantscope.core import dates


def export_target(signal):
    if signal.model not in ("portfolio", "quantorion", "quantlibra", "quantpyxis"):
        raise ValueError("研究项目不进入SuperMind执行导出")
    if signal.status not in ("ok", "no_candidates"):
        raise ValueError("失败信号不能导出")
    dates(signal.signal_date, signal.execute_after)
    if any(not c.endswith((".SH", ".SZ")) for c in signal.targets):
        raise ValueError("本适配层仅支持沪深目标快照")
    return {
        "platform": "SuperMind",
        "mode": "target_snapshot_only",
        "can_submit_orders": False,
        "signal": signal.to_dict(),
    }
