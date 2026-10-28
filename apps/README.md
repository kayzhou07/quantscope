# Web and mobile terminal integration

本次提供可运行的统一HTTP研究接口；旧Web页面保留在prototypes。apps/web与apps/mobile只记录终端接入边界，不伪装成已交付的统一生产Web或原生App。

共享契约见docs/data-contract.md。客户端展示四模型角色、日期、版本、风险状态、目标和失败原因，失败状态不得展示为“空仓建议”。组合视图必须区分目标与实际持仓；没有成交回读时不能展示“已成交”。

生产远程访问、身份认证、权限、密钥管理、持仓和成交同步尚未实现。Web优先，之后移动端复用同一API；禁止在移动端硬编码行情/AI密钥。
