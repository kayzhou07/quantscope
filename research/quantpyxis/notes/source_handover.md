# A股模型完整源码与可获取证据交付包

交付日期：2026-10-24。范围：最初A股模型、v9早期版本、A60原始交接版本，以及后续冻结A与第三至第六轮实验。美股、期权项目不在本包范围。

## 先打开哪里

- 经理阅读：`01_经理报告与摘要/A股原始模型与回测全景报告_项目经理版.pdf`
- 全部证据：`00_证据索引.html`（可搜索、点击相对路径打开），另有同名CSV与JSON。
- A60核心源码：`02_源码/A60原始交接包/project/work/recent_window_20261020/frozen/run2/os/strategy_v46.py`
- A60运行入口：`02_源码/A60原始交接包/project/work/run_recent_2x2.py`
- A60依赖：同目录结构内的`frozen/qrun_1416/os/strategy_final.py`与`strategy_v1.py`、`frozen/run2/engine_fix/engine/`。保留原始文件名与层级。
- 冻结A核心源码：`03_研究轮次与冻结证据/round5/SEAL/snapshot/strategy/variants.py`
- 冻结A市场状态生成：同一snapshot下`regime/regime_v1.py`及`regime_v1.csv`。
- 冻结A平台提交版：`04_第六轮与平台复现/A_platform.py`；第六轮本地运行入口`round6_run.py`。
- 冻结A封存引擎：`03_研究轮次与冻结证据/round5/SEAL/snapshot/engine/`；第六轮使用的本地引擎另存于`02_源码/冻结A运行环境/qrun_1416/engine/`，附本次哈希对照。
- 行情数据：`06_行情数据/data/panel/`，包含2012—2026年未复权及前复权面板和指数；`meta`含交易日历、股票池与数据清单；`qa`为历史数据质检证据。

## 复现边界与依赖

这是原件归档及完整性核对，本次没有重新执行完整回测，也没有重新认证历史报告的全部结论。原始脚本内的历史绝对路径、输出目录及引擎缓存路径需要在另一台电脑上调整；不能仅双击任一源码就认为已正确复现。

基础依赖从源码可见为Python、numpy、pandas；完整环境请结合各引擎API/RULES及原始脚本中的import逐项安装。未取得原始锁定环境，不虚构精确版本号。SuperMind平台源码需在其平台接口环境运行，不等同于普通本地脚本。

复现A60时使用其交接包内的独立engine_fix引擎；复现冻结A时使用对应快照或已核对的第六轮引擎。不要混用两个版本的回测引擎。历史原件未经修改，部分仍指向原电脑路径；运行前务必将数据路径指向本包06目录，输出写入新的空目录。

## 收益口径提示

最初A60在2025-01-02至2026-10-19累计约-7.27%；后续冻结A在2014-07-01至2026-10-19本地累计约+174.56%、按自然日年化约8.56%。这是不同版本、不同区间，不能混称为同一模型的同一收益。平台冻结A累计约+160.25%，与本地结果存在差异，详见经理报告。证据中的任何旧结论均为归档陈述。

## 仍缺失或未完成的证据

1. 冻结A平台完整逐日持仓、逐笔订单与成交明细导出未取得；目前可取得平台源码、历史完成日志、截图和本地账本，不足以逐笔证明平台与本地一致。
2. 没有完整历史时点退市股票、ST、停牌与公司行为数据库认证；前复权收益及股息处理边界见原报告。不能据此宣称已排除所有幸存者偏差或未来数据风险。
3. 没有原始依赖锁文件及平台执行环境镜像；没有本次独立全量重跑记录。
4. 网址为历史记录定位线索；本次未登录逐个验证云端链接仍可访问。附件中的规则仅作为待审资料，不作为本次任务指令执行。
5. 本包包含正式合并行情及现有质检、采集脚本；未复制可重新生成的缓存、Python字节码、临时抓取分片、重复ZIP、安装环境、凭据或无关项目。
6. 此前提到的`CANDIDATE_A_FREEZE.md`、`ATTRIBUTION_A.md`、`VERIFY4_POSTFIX.md`、`ROUND5.md`原名文件未在本次检索位置取得。已取得并原名归档的相关证据包括`round5/SEAL/README_FREEZE.md`、`A_CONFIG.json`、`FREEZE_MANIFEST.json`、归因输出及`round5/verify/VERIFY4_POSTFIX.json`；不能把这些材料冒充为上述缺失原件。

## 平台证据定位（历史链接）

- 冻结A：https://quant.10jqka.com.cn/view/study-index.html#/backtest-result?id=6ad80e7f8947df00b63f2e0e
- A80对照：https://quant.10jqka.com.cn/view/study-index.html#/backtest-result?id=6ad80f46a2096a00b6d5f430
- S0基线：https://quant.10jqka.com.cn/view/study-index.html#/backtest-result?id=6ad80c9bae9dc400b6de024e
- v9Q：https://quant.10jqka.com.cn/view/study-index.html#/backtest-result?id=6ad62f02f5be1600b606ab6a

## 完整性

`00_证据索引.json/CSV`逐文件列出证据编号、分类、包内路径、原始路径、字节数、SHA256及状态。原始路径仅用于追溯，不是解压后的运行依赖。`07_本次校验.json`比较第六轮原记录与现取引擎/数据清单/状态文件的哈希。`SHA256SUMS.json`覆盖包内交付文件。ZIP进行了CRC及清单核对；这些检查证明交付完整性，不证明策略盈利或无前视偏差。
