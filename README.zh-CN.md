# 量见 QuantScope

三个量化模型与一个AI金融科技研究项目的统一源码仓库。

[English](README.md) · [架构](docs/architecture.md) · [规则与版本](docs/model-rules.md) · [英文计划书](docs/business-plan/business-plan.en.md) · [中文计划书](docs/business-plan/QuantScope-Business-Plan-ZH.pdf)

| 统一ID | 模型 | 分工 |
| --- | --- | --- |
| quantvela | 量寰 QuantVela | 全球配置与美股ETF研究，研究项目，暂不进入经营组合 |
| quantorion | 量曜 QuantOrion | A股短周期市场结构，前身为AlphaBoard |
| quantlibra | 量衡 QuantLibra | ETF双动量轮动及回测检查，前身为QuantBalance |
| quantpyxis | 量枢 QuantPyxis | A股中期动量选股、市场状态与仓位预算 |

## 贡献者与 AI 工具致谢

| 名称 | 角色 |
| --- | --- |
| [@kayzhou07](https://github.com/kayzhou07) | 项目所有者与维护者 |
| [OpenAI — Codex／ChatGPT](https://github.com/openai) | 源码整理、文档编写及开发辅助工具 |
| [DeepSeek](https://github.com/deepseek-ai) | 项目研究与报告工作流中的 AI 工具致谢 |

AI 工具列名不表示相应官方团队提交过代码、担任维护者或为项目背书。[贡献说明](CONTRIBUTORS.md)。

## 本地使用

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
quantscope models
quantscope signal examples/quantorion.json
quantscope signal examples/quantlibra.json
quantscope portfolio examples/portfolio.json
python -m unittest discover -s tests -v
```

统一核心使用Python 3.11+及标准库，不需要账户、不拉外部数据、不调用AI、不下单。样例全部为合成数据。原有Web原型另在prototypes中保留，第三方依赖不影响核心离线运行。

## 本次已经整理到什么程度

已实现四模型的统一离线信号入口、明确日期/版本/失败状态、三个经营模型的预算加权目标和集中度校验、仅导出目标的SuperMind适配层、共用HTTP研究接口。源码、研究规则、报告和历史变体按模型归档，附哈希清单、迁移说明、测试和GitHub CI。

量寰的纯规则函数是本次根据研究说明新增，不是找回了历史完整回测引擎。量衡25/26点窗口差异保留为不同版本。量枢完整月度调仓、退出及撮合逻辑仍在原平台脚本中，统一核心计算的是某日目标快照，不等于复刻历史回测。

SuperMind三模型原生接入、真实账户成交回读、生产级统一Web、原生App仍需后续研发；不把目录占位写成已经完成的功能。量枢冻结状态截至2026-10-19，过期会显式报错。

## 文档

- [快速开始与接口](docs/quickstart.md)
- [架构与数据流](docs/architecture.md)
- [四模型规则、公式与版本差异](docs/model-rules.md)
- [JSON契约](docs/data-contract.md)
- [迁移说明](docs/migration.md)
- [原型运行与历史环境](docs/legacy-runtime.md)
- [研究资料归档](docs/materials.md)
- [GitHub发布说明](docs/github.md)
- [英文商业计划书](docs/business-plan/business-plan.en.md)

公司业务顺序按计划书保留：先验证自有资金配置模式，工具成熟后再探索软件及付费技术服务。默认保留专有版权，不因放进GitHub而自动开源。资料归档与行情数据单独交付，不随源码提交。
