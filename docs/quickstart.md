# 快速开始

从仓库根目录运行README中的安装命令。Windows激活虚拟环境使用`.venv\Scripts\activate`。四个样例输入均为合成日线，没有从网络获取行情。

```bash
quantscope signal examples/quantvela.json
quantscope signal examples/quantorion.json
quantscope signal examples/quantlibra.json
quantscope signal examples/quantpyxis.json
quantscope portfolio examples/portfolio.json --output portfolio-target.json
quantscope export portfolio-target.json --output supermind-target.json
quantscope serve --port 8765
```

portfolio-target.json/supermind-target.json是本地输出，用完自行保存到runtime目录；不要把真实账户快照提交Git。导出仅是内部JSON规范，不是可直接上传到SuperMind执行的脚本。

运行校验：`python -m unittest discover -s tests -v`；发布前：`python scripts/release_check.py`。CI仅验证统一核心和发布规则，不运行需要行情、AI凭据及交易平台环境的历史脚本。

原Web项目使用方法见legacy-runtime.md。生产环境不能直接公开本地HTTP服务；此版本用于统一研发入口，后续需鉴权、限流、审计、反向代理、部署测试和授权平台数据接入。
