# JSON contract v1.0

所有文件UTF-8，日期YYYY-MM-DD，交易日与复权口径由数据供应方负责。资产统一为600000.SH、159915.SZ、QQQ.US；兼容XSHG/XSHE输入但输出统一。相同资产别名重复时拒绝。

请求必填model、signal_date、execute_after、data。execute_after严格晚于信号日，由外部日历确认，不等于程序承诺可在该时刻成交。data.history为资产→日线列表，按日期严格升序、不重复，各资产末日必须等于signal_date、所需窗口交易日必须对齐。close为正、有限的复权价格；所有行情均应已完成且当时可获得。

| 模型 | 最小观察数 | 其他数据 |
| --- | --- | --- |
| quantvela | 200 | QQQ.US；branch，月度分支另需rebalance_due |
| quantorion | 2 | 每日pct_chg（百分数，10表示10%）、volume（两日同单位）；variant |
| quantlibra | 26默认/25平台窗口 | 四只ETF；state.holding及entry_date；variant |
| quantpyxis | 253 | 末日raw_close、is_st/is_paused（0/1），末60日turnover（人民币元）；held、nav、regime_source |

响应字段：model、version、signal_date、execute_after、status、targets、risk_state、reason、diagnostics、cash_weight、schema_version。targets为非负资金权重、合计≤1；cash_weight=1−Σtargets。模型版本属于统一核心版本，不是替换原研究轮次编号。

status：ok有目标；no_candidates正常空仓；invalid_data输入不满足契约；expired冻结状态过期；error预留。后三类不能输出可执行目标，cash_weight=null。engine.evaluate对未知模型抛出ValueError，CLI返回2、HTTP返回400；已知模型数据失败HTTP422。

组合输入见examples/portfolio.json：signals是统一响应列表，budgets与模型键必须完全一致。limits为max_asset、sectors、max_sector。导出输入是统一Signal响应，输出mode=target_snapshot_only、can_submit_orders=false。

本地HTTP支持GET /health、GET /api/v1/models、POST /api/v1/signals；最大请求4MB，不接受远程下单，也未实现生产用户鉴权。
