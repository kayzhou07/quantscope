# QuantOrion 网页设计 Prompt

## 英文完整版（DALL-E / Midjourney / v0 / Lovable）

```
Design a professional quantitative trading system landing page for "QuantOrion - A-Share Intelligent Quantitative Trading System".

COLOR PALETTE:
- Primary dark blue: #0F2447 (background)
- Secondary dark: #0a1a35 (cards/sections)
- Silver gray text: #E8EDF5 (primary text), #8899b4 (secondary)
- Data green accent: #22B573 (buttons, positive numbers, highlights)
- Warning red: #E54949 (negative numbers, alerts)
- Border: #1e3a6e (card borders)
- Dark/light mode toggle supported

LAYOUT (single page, scrollable):

1. FIXED TOP NAVIGATION BAR (56px height, semi-transparent dark background with blur):
   - Left: Brand logo "QuantOrion" with green triangle icon, monospace font
   - Center: Nav links (量化模型, 回测数据, 因子库, 实盘模拟, 功能优势)
   - Right: Dark/light theme toggle button (circular, bordered)

2. HERO SECTION (full viewport height):
   - Dynamic K-line chart background (semi-transparent green/blue flowing lines, low opacity ~0.12)
   - Canvas-animated data flow lines moving upward
   - Centered headline: "QuantOrion A股智能量化交易系统" in large bold white text
   - Subtitle: "基于缩量连板因子模型的短线量化策略，专业级数据回测验证，合规券商级风险管控"
   - 3 metric cards below: "95.2% 2026年周胜率" | "+3,733% 累计收益" | "+19.55% 平均周收益"
   - Green glowing accent on key numbers

3. QUANT MODELS SECTION:
   - Section title: "量化策略模型"
   - 4 cards in responsive grid (2x2 on desktop, 1 column on mobile):
     - Card 1: Lightning icon, "缩量连板模型" - core strategy description
     - Card 2: Chart icon, "动量因子引擎" - multi-factor scoring
     - Card 3: Brain icon, "AI情绪分析" - DeepSeek LLM sentiment
     - Card 4: Lock icon, "市场环境判断" - MA10/MA20 regime detection
   - Cards: Dark rounded corners (#132852), subtle border, hover lift effect with green border highlight

4. BACKTEST DASHBOARD SECTION (alternating darker background #0a1a35):
   - Section title: "回测数据看板"
   - 4 metric cards in row: Cumulative Return +3,733%, Win Rate 95.2%, Avg Weekly +19.55%, Max DD -5.42%
   - Below: Bar chart showing 21 weeks of returns (green bars for positive, red for negative)
   - Data: 2026 Jan-Jun, weekly reset 500K CNY, no fees

5. FACTOR LIBRARY SECTION:
   - Section title: "因子库"
   - 6 horizontal progress bars showing factor status:
     - 涨停连续性: 100% (green)
     - 成交量缩量: 75% (blue)
     - 3日动量: 60% (yellow)
     - 换手率: 55% (green)
     - 北向资金: 45% (blue)
     - A股过滤: 100% (green)
   - Clean labels with monospace values

6. LIVE TRADING SIMULATION SECTION (darker background):
   - Section title: "实盘模拟"
   - Input form card (centered, max-width 600px):
     - Capital input (number, default 500000)
     - Date picker
     - Green "运行策略" button with hover glow
   - Results card below (hidden until query):
     - Stock rows with code, name, volume
     - AI sentiment tags (optimistic/neutral/cautious with color coding)
     - Financial news list with timestamps

7. MARKET OVERVIEW SECTION:
   - 4 index cards: 上证指数, 深证成指, 创业板指, 科创50
   - Each shows closing price + daily change % (green/red)
   - Market regime tag (normal/weak/oscillation)
   - Up/down/flat/limit-up stock counts
   - AI-generated market summary paragraph
   - Financial news list (15 items with time + source)

8. FEATURES SECTION:
   - 6 feature cards in 3x2 grid: 极速响应, 全平台兼容, 合规设计, 实时快讯, 数据安全, 高度可扩展
   - Each with emoji icon + title + description

9. FOOTER:
   - Dark background (#0a1a35), centered
   - QuantOrion logo + version info
   - Risk disclaimer text
   - Copyright line

RESPONSIVE DESIGN:
- Desktop: Multi-column grid layouts
- Mobile (<768px): Single column, stacked cards, hidden nav links
- Smooth transitions on theme toggle (dark/light)

STYLE REQUIREMENTS:
- Clean fintech institutional look
- No clip art, no fake profit claims, no watermarks
- 8K quality, modern sans-serif fonts (Inter, SF Mono for numbers)
- Low saturation shadows, precise geometric layout
- Card-based design with subtle hover animations
- Professional Bloomberg Terminal / Wind-like aesthetic
- Dark mode primary, light mode as toggle option
```

## 中文精简版

```
设计一个专业量化交易系统官网页面"QuantOrion - A股智能量化交易系统"。

配色：深空蓝#0F2447背景 + 银灰#E8EDF5文字 + 数据绿#22B573强调 + 警示红#E54949
布局：单页滚动，固定顶部导航栏，首屏全屏大图，下方依次为量化模型介绍(4卡片)、回测数据看板(4指标+柱状图)、因子库(6进度条)、实盘模拟(输入表单)、市场全景(指数+涨跌+新闻+AI摘要)、功能优势(6卡片)、底部页脚。
风格：深色金融终端风，卡片式布局，Canvas动态K线背景，响应式PC+移动端，支持深色/浅色切换。
```

## 技术实现提示

```
Single HTML file with embedded CSS and JavaScript.
No external frameworks (no React, no Tailwind, no Bootstrap).
FastAPI backend serves this as static page.
CSS variables for theming, CSS Grid for layouts.
Canvas API for animated backgrounds and charts.
Fetch API for all data calls to backend endpoints.
15KB compressed, zero dependencies.
```
