# 回测可信度审计工具

把「回测偏差」从资深从业者的经验判断，变成可自动化、可量化、可复现的工程检查。

## 组成

| 文件 | 作用 |
|---|---|
| `backtest_auditor.py` | 9 条静态审计规则引擎（纯标准库，零依赖） |
| `llm_report.py` | LLM 叙事报告层（把结构化发现转成诊断书，provider 无关） |

## 9 条审计规则

| 规则 | 严重度 | 检测信号 |
|---|---|---|
| R1 幸存者偏差 | 高 | `get_index_stocks` 未传 date（用今天成分股回测过去） |
| R2 涨跌停成交假象 | 高 | `LimitOrderStyle(0.99/1.01×last_price)` 追板 |
| R3 流动性幻觉 | 高 | 小市值 + 持仓集中，忽略冲击成本 |
| R4 未来函数 | 高 | `end_date=context.current_dt` 用当天数据当天决策 |
| R5 成本/滑点缺失 | 中 | 未设 set_order_cost / set_slippage |
| R6 过度拟合 | 高 | 全局参数堆积（>150 个） |
| R7 参数挖掘 | 中 | 大量 enable/use 实验开关（p-hacking） |
| R8 样本外验证缺失 | 中 | 参数多但无 OOS/训练验证划分 |
| R9 基准不当 | 低 | 小市值策略用沪深300基准虚高超额 |

## 用法

```bash
# 规则审计（零依赖）
python3 backtest_auditor.py 你的策略.py

# LLM 报告（需配 DEEPSEEK_API_KEY 或改用任意 LLM）
DEEPSEEK_API_KEY=xxx python3 llm_report.py 你的策略.py --out 报告.md
```

## 实证案例

见 `../docs/S5审计报告_DeepSeek.md` —— 规则引擎对 S5 小市值策略检出 **11 处高严重度**偏差（幸存者偏差 2 + 涨跌停 6 + 流动性 1 + 未来函数 2），成功拆穿一个 +4212% 的回测假象。

## 设计理念

**规则是地基，LLM 是放大器。** 规则引擎能独立抓出真实问题（不依赖 LLM），LLM 层负责把干巴巴的 findings 变成"影响量级 + 修复优先级"的诊断书。规则阈值全部由真实代码的 ground truth 校准（如参数堆积阈值来自"267 参数 vs 12 参数"的实测差距），而非拍脑袋。
