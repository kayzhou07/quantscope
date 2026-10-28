# QuantScope 源码整理实施计划

**Goal:** 交付可提交GitHub的统一仓库及单独的研究资料归档。
**Architecture:** 四模型独立规则、统一信号契约、组合层、离线平台导出、统一CLI与HTTP接口；历史工程隔离。
**Tech Stack:** Python 3.11+、标准库、unittest、setuptools；原型第三方依赖独立。

- [x] 1. 源码盘点、原始哈希、排除清单；安全读取压缩包，所有文件落到新目录。
- [x] 2. tests/test_models.py、test_portfolio.py、test_interfaces.py 先覆盖正常规则及数据/仓位边界，运行确认缺少实现时失败。
- [x] 3. 实现 src/quantscope/core.py、data/validation.py 和 models 四模块；分别验证规则，量衡与旧函数逐例对照。
- [x] 4. 实现 portfolio/allocation.py 和 adapters/supermind.py，只输出目标快照且拒绝研究模型、错误日期及预算超限。
- [x] 5. 实现 interfaces/cli.py、interfaces/http.py；统一CLI、回环HTTP、JSON错误、确定性样例；验证无网络离线调用。
- [x] 6. 迁移原型及历史研究，移除密钥/个人信息/运行缓存，替换品牌与用户路径；保留算法与依赖边界，附迁移清单。
- [x] 7. 编写README、架构、参数、接口、运行说明及Git发布说明；配置pyproject、CI、.env.example、.gitignore、专有许可声明。
- [x] 8. 运行单测、离线示例、HTTP、干净安装、源码编译、扫描和打包校验；生成交付报告及清单，再初始化本地Git仓库。
