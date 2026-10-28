# QuantLibra · 全天候 ETF 轮动投资建议

仿 QuantOrion 风格（深蓝科技风）的本地网站，在 **V6 双动量轮动** 与 **S123 三策略组合** 之间切换，一键获取当前持仓信号、市场情绪、具体股数建议与财经快讯。

## 启动（本地）

```bash
cd ~/quant_web
export TUSHARE_TOKEN=你的token
./start.sh                    # 一键启动：检查 token → 后台启动 → 打开浏览器
# 或直接：
python3 server.py             # 自动打开 http://localhost:8088
```

## 功能全景

- 选策略（V6 / S123）+ 设投入资金
- 实时信号：V6 排名 / S123 三腿（S1 轮动 + S2 红利配比 + S3 折溢价套利）
- 市场情绪温度计（北向资金 + 三大指数 + 成交额放量，三因子）
- 具体股数建议（100 股整数手）
- 历史回测曲线（2019 至今，含沪深300 基准，悬停/缩放/平移）
- 同花顺财经快讯（可跳转 + 分类筛选 + 每 60 秒自动刷新）
- 移动端响应式适配

## 技术栈

- **后端**：Python 标准库 `http.server`（零第三方依赖，服务器只装 python3 即可跑）
- **数据**：Tushare（行情/净值/北向资金，curl 直调）+ 同花顺（财经快讯，公开接口）
- **前端**：单文件 `static/index.html`，仿 QuantOrion 深蓝 + 绿色主色 + 青色辅助

## 文件结构

```
quant_web/
├── start.sh              # 一键启动脚本
├── server.py             # 多线程 HTTP 服务器 + API 路由
├── strategies.py         # V6/S123 信号、情绪、快讯、回测、资金建议
├── static/index.html     # 前端页面
├── quantlibra.service  # systemd 开机自启服务（服务器部署用）
├── 阿里云部署指南.md      # 部署文档
└── README.md
```

## API

| 端点 | 返回 |
|---|---|
| `GET /api/signal?strategy=v6\|s123` | 实时信号 |
| `GET /api/advice?strategy=v6&capital=300000` | 信号 + 情绪 + 建议 + 快讯（综合） |
| `GET /api/backtest?strategy=v6\|s123` | 回测曲线 + 沪深300 基准 + 指标 |
| `GET /api/news` | 同花顺快讯（独立，用于自动刷新） |

## 部署

见 [阿里云部署指南.md](阿里云部署指南.md)。零依赖，服务器上只需 `python3` + `TUSHARE_TOKEN`，支持 systemd 开机自启。

## 已知限制

- **S3 回测为近似**：QDII 折溢价套利的历史回测用"QDII 等权买入持有"近似（真实套利需逐日净值对齐，工作量大）。
- **不复权价**：`fund_daily` 返回不复权收盘价，与聚宽前复权口径在 25 天短窗口下信号一致（已验证）。
- **仅研究参考**：所有信号与建议基于历史行情，不构成任何投资建议。
