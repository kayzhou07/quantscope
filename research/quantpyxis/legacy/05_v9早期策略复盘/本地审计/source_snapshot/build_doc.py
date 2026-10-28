# -*- coding: utf-8 -*-
"""Build the strategy document (Markdown + PDF) from the backtest artefacts."""
import json, os, datetime

Q = 'C:<LOCAL_HOME>/WorkBuddy/2026-10-19-20-37-30/qrun/'
OUT = 'C:<LOCAL_HOME>/WorkBuddy/2026-10-19-20-37-30/deliverables/'
os.makedirs(OUT, exist_ok=True)

cur = json.load(open(Q + 'results_current.json', encoding='utf-8'))
full = json.load(open(Q + 'full_current.json', encoding='utf-8'))
sens = json.load(open(Q + 'sensitivity.json', encoding='utf-8'))
qcmp = json.load(open(Q + 'quarterly_compare.json', encoding='utf-8'))
res = json.load(open(Q + 'results_v9.json', encoding='utf-8'))
la = json.load(open(Q + 'loss_audit.json', encoding='utf-8'))
TRIPS, WORST = la['trips'], la['worst']
bench = cur['bench']

def pct(x, d=2, sign=True):
    if sign:
        return ('%+.' + str(d) + 'f%%') % (x * 100)
    return ('%.' + str(d) + 'f%%') % abs(x * 100)

def money(x):
    return '{:,.2f}'.format(x)

attr = sorted(cur['attribution'], key=lambda a: -(a['total_pnl'] - a['fees']))
net_total = sum(a['total_pnl'] - a['fees'] for a in attr)
winners = [a for a in attr if a['total_pnl'] - a['fees'] > 0]
losers = [a for a in attr if a['total_pnl'] - a['fees'] <= 0]

mon = {}
for e in full['equity']:
    mon[e['date'][:7]] = e['total']

reb = full['rebalance_log']
tr = full['trade_log']

# ---------------- content ----------------
B = []
B.append(('title', '量化一｜主板＋创业板动量轮动策略说明 v9'))
B.append(('sub', '回测区间 2026-04-01 至 2026-09-30　｜　初始资金 100,000 元　｜　整理日期 2026-10-19'))
B.append(('note', '本文档描述的是一段已经发生的历史回测，不是收益承诺，也不构成任何投资建议。所有结果都在同一段已看过的行情里选出，属于样本内研究。'))

B.append(('h1', '一、先说结论'))
B.append(('p', '用 10 万元、只买主板和创业板，按下面的规则跑完 2026 年 4 月 1 日到 9 月 30 日的 125 个交易日：期末资产 '
          + money(cur['final']) + ' 元，收益 ' + pct(cur['return']) + '，最大回撤 ' + pct(cur['max_drawdown'], 2, False)
          + '，全程 41 笔成交，手续费加税费合计 ' + money(cur['fees']) + ' 元，平均股票仓位 ' + pct(cur['mean_exposure'], 1, False)
          + '，16 个交易日有过交易。'))
B.append(('p', '同期对照：沪深300 ' + pct(bench['CSI300']) + '，创业板指 ' + pct(bench['ChiNext']) + '，上证综指 '
          + pct(bench['SSE']) + '，股票池内 ' + str(bench['equal_weight_n']) + ' 只股票等权买入持有 ' + pct(bench['equal_weight_universe']) + '。'))
B.append(('p', '但有三件事必须同时说清楚，否则这 47.74% 会被误读：'))
B.append(('ul', [
    '利润高度集中。12 只持仓里 3 只贡献了 ' + money(sum(a['total_pnl'] - a['fees'] for a in attr[:3])) + ' 元，几乎等于全部净利润；另外 4 只合计亏 ' + money(-sum(a['total_pnl'] - a['fees'] for a in losers)) + ' 元。',
    '行业高度集中。这半年买到的一直是同一条产业链：电子布—覆铜板—PCB—光模块—铜箔—锂电材料。5 只股票不是 5 个风险，是 1 个行业押注。',
    '这是样本内结果。我在这一段行情上先后试了 5 个主变体、7 个季度/月度组合、18 格参数网格，最终选出来的数字天然偏乐观。它证明这套规则能跑通、逻辑自洽，不证明下一年还能赚 47%。',
]))

B.append(('h1', '二、账户约束（这些是硬条件，不是假设）'))
B.append(('table', ['项目', '本次设定'], [
    ['初始资金', '100,000 元'],
    ['可买范围', '沪深主板 + 创业板；不买科创板（688/689）、北交所'],
    ['最小交易单位', '100 股（一手），只能整手买卖'],
    ['交收制度', 'T+1，当天买入次日才能卖'],
    ['杠杆与做空', '不允许'],
    ['交易成本', '佣金 2.5bp（最低 5 元）＋过户费 0.1bp（双边）＋印花税 5bp（仅卖出）＋滑点 10bp'],
    ['价格口径', '前复权日线，以 2026-10-19 为基准向前调整'],
    ['数据来源', '腾讯自选股行情接口（westock CLI），1590 只标的、各 424 个交易日日线'],
]))

B.append(('h1', '三、股票池'))
B.append(('p', '1590 只：沪深300（300 只）＋中证500（500 只）＋中证1000（1000 只）＋创业板指（100 只）去重，'
          '剔除 214 只科创板后剩下主板 1249 只、创业板 341 只。'))
B.append(('p', '为什么不是全市场 4000 多只：行情接口按标的逐个拉取，全市场要十几万次请求，且 10 万元账户也买不动尾部小票。'
          '用宽基指数成分近似“流动性合格、10 万元买得动”的池子，是工程上的取舍，代价是抽样偏差——'
          '指数成分用的是今天的名单，过去半年里跌到退市或被剔除的股票不在池子里，这会系统性抬高回测收益。'))
B.append(('p', '每个调仓日的实际可选数量约 1080–1160 只（成交额、价格、上市时长、正常交易四项筛完之后）。'))

B.append(('h1', '四、策略规则（可以照着执行）'))
B.append(('h2', '4.1 什么时候做决定'))
B.append(('p', '每月最后一个交易日收盘后。用当天及之前的全部数据算信号，第二天开盘执行，绝不当天收盘价成交。'))
B.append(('h2', '4.2 选什么：月线 12−1 动量'))
B.append(('p', '设 m 为最近一个已结束的自然月。动量分数 M ＝ P(m−1) ÷ P(m−12) − 1，P 为月最后一个交易日的前复权收盘价。'
          '等于回看一年、跳过最近一整个月，避免把“上个月刚拉起来”的短期情绪当成趋势。按 M 从大到小排序。'))
B.append(('h2', '4.3 硬性筛选（五条，缺一不进）'))
B.append(('ul', [
    '过去 20 个交易日平均成交额 ≥ 1 亿元（流动性，10 万元进出不影响价格）',
    '信号日收盘价 ≥ 2 元（排除低价股与退市整理）',
    '至少有 240 个交易日历史（新股、次新股不参与）',
    '信号日当天必须有成交（停牌的自然出局）',
    '一手市值 ≤ 当前净值的 25%（10 万元买不起一手的股票，规则上不碰）',
]))
B.append(('h2', '4.4 买几只、买多重'))
B.append(('table', ['大盘状态（信号日）', '持股只数', '单只权重', '合计股票仓位'], [
    ['沪深300 收盘 > 120 日均线', '5 只', '19%', '95%'],
    ['沪深300 收盘 ≤ 120 日均线', '3 只', '19%', '57%'],
]))
B.append(('p', '风险预算用“只数 × 单只权重”表达，而不是“先定总仓位再除以只数”。原因见第五节：10 万元账户里，'
          '总仓位除以 5 会得到 10%–11% 的单只预算，一手高价股就顶到 20%，仓位会在无意中被放大一倍。'))
B.append(('h2', '4.5 换手控制'))
B.append(('p', '持仓只要还留在动量排名前 6 名（目标 5 只 + 缓冲 1 名）就不动，只有掉出缓冲带才卖。'
          '本次六次调仓中，每次实际成交 3–7 笔，主要来自换股而不是微调。'))
B.append(('h2', '4.6 什么时候必须走：12% 移动止损'))
B.append(('p', '任何一只持仓，从买入之后的最高收盘价回撤 ≥ 12%，次一交易日开盘卖出，现金留到下一个月初再评估。'
          '这是本文唯一一个非月度、事件驱动的动作，也是这套策略真正的风险控制器：去掉它，同一条选股规则的最大回撤从 '
          + pct(cur['max_drawdown'], 1, False) + ' 变成 46.25%。'))
B.append(('h2', '4.7 怎么成交'))
B.append(('p', '次一交易日开盘价，先卖后买（先腾出现金），按 100 股向下取整，买不满一手就放弃；'
          '买入价加 10bp 滑点、卖出价减 10bp 滑点；佣金、过户费、印花税逐笔扣现金。'))

B.append(('h1', '五、为什么这样改（和上一版比，改了什么）'))
B.append(('p', '上一版（v8Q，在量化平台跑）在同一区间只做到 −0.02%～+0.003%，最大回撤 5.84%，半年只有 7 笔成交、平均仓位 21%。'
          '当时的结论是“策略太保守”，但复盘后发现真正的问题不是选股，是仓位表达方式：'))
B.append(('table', ['环节', 'v8Q 平台版', 'v9 本版'], [
    ['股票池', '历史沪深300 成分（约 300 只）', '主板+创业板 1590 只'],
    ['仓位表达', '20% 目标波动率 + 单股 15% 上限 + Beta/残差/尾部预算', '只数 × 19%，取消波动率目标'],
    ['入池门槛', '200 股 × 前收 + 10 元 ≤ 净值 4%', '一手市值 ≤ 净值 25%'],
    ['季度内风控', '无（季度之间不下单）', '12% 移动止损'],
    ['半年结果', '−0.02%～+0.003%，回撤 5.84%，7 笔', '+47.74%，回撤 15.92%，41 笔'],
    ['平均仓位', '21%', '41.5%'],
]))
B.append(('p', '三层限制叠在一起（20% 波动目标、单股 15% 上限、200 股门槛）把 10 万元切成了 5 份 10% 左右的小预算，'
          '再被 200 股门槛筛掉一半候选，最后 79% 的钱一直躺在账上。低回撤在这个语境里不是优点，是没参与。'
          'v9 把风险控制从“事前把仓位压小”改成“事后及时止损”，让钱先进场，再管住下行。'))

B.append(('h1', '六、回测结果'))
B.append(('h2', '6.1 总表'))
B.append(('table', ['指标', '数值', '说明'], [
    ['期末资产', money(cur['final']) + ' 元', '含未平仓 1 只（金安国纪 300 股，市值 ' + money([a for a in attr if a['shares_left']][0]['market_value']) + ' 元）'],
    ['区间收益', pct(cur['return']), '125 个交易日'],
    ['最大回撤', pct(cur['max_drawdown'], 2, False), '2026-06-26 至 2026-08-03'],
    ['年化波动率', pct(cur['ann_vol'], 2, False), '日收益标准差 × √252'],
    ['夏普比率', '%.2f' % cur['sharpe'], '无风险利率按 0 处理'],
    ['Calmar', '%.2f' % cur['calmar'], '收益 ÷ 最大回撤'],
    ['成交笔数', str(cur['trades']) + ' 笔', '分布在 16 个交易日'],
    ['费用合计', money(cur['fees']) + ' 元', '占初始资金 0.50%'],
    ['平均股票仓位', pct(cur['mean_exposure'], 1, False), '按 125 个交易日净值加权'],
]))
B.append(('chart', '净值曲线'))
B.append(('p', '曲线形状说明：4 月建仓后净值在 5—6 月快速抬升（4 月 11.5 万 → 6 月末 15.87 万），6 月末见顶 16.48 万，'
          '7 月初五个止损同时触发、回测内的最大回撤就发生在这段，8 月重新建仓小幅修复，9 月末收在 14.77 万。'
          '它不是一条平滑上行的线，是一次“赢在 4—6 月、7 月被打回一部分”的行情。'))

B.append(('h2', '6.2 月度净值'))
mrows = []
prev = 100000.0
names = {'2026-04': '4 月', '2026-05': '5 月', '2026-06': '6 月', '2026-07': '7 月', '2026-08': '8 月', '2026-09': '9 月'}
for k in sorted(mon):
    mrows.append([names[k], money(mon[k]), pct(mon[k] / prev - 1), pct(mon[k] / 100000 - 1)])
    prev = mon[k]
B.append(('table', ['月份', '月末资产', '当月收益', '累计收益'], mrows))

B.append(('h2', '6.3 六次调仓'))
rrows = []
for l in reb:
    rrows.append([l['exec_date'], '进攻' if l['regime_on'] else '防守', str(l['n_positions']),
                  '、'.join(l['names']), str(l['candidates'])])
B.append(('table', ['执行日', '大盘状态', '只数', '买入名单', '可选数量'], rrows))
B.append(('p', '4 月 1 日、8 月 3 日、9 月 1 日三次是防守状态（沪深300 在 120 日均线下方），只持 3 只；其余三次进攻，持 5 只。'
          '注意 5 月 6 日是五一假期后的第一个交易日，规则里“月初”指的是该月第一个交易日，不是 1 号。'))

B.append(('h2', '6.4 逐笔归因：钱是哪来的，亏在哪'))
arows = []
for a in attr:
    arows.append([a['name'], a['board'], money(a['total_pnl'] - a['fees']),
                  ('持有中 ' + str(a['shares_left']) + ' 股') if a['shares_left'] else '已清仓'])
B.append(('table', ['股票', '板块', '净盈亏（元）', '状态'], arows))
B.append(('p', '赚钱的三只：宏和科技 ' + money(attr[0]['total_pnl'] - attr[0]['fees']) + ' 元（电子布，4 月 75 元买入、5 月 131 元止损，6 月 182 元再买、7 月 268 元卖出），'
          '鼎泰高科 ' + money(attr[1]['total_pnl'] - attr[1]['fees']) + ' 元（PCB 刀具），铜冠铜箔 ' + money(attr[2]['total_pnl'] - attr[2]['fees']) + ' 元（锂电铜箔）。'
          '亏钱的四只合计 ' + money(-sum(a['total_pnl'] - a['fees'] for a in losers)) + ' 元，主要是 7 月 6—7 日那两天五个止损同时触发：'
          '永鼎股份、汇绿生态、国际复材、德福科技在两天内全部被打掉。'))
B.append(('p', '这不是“选股很准”的故事，是“选到了一条正在爆发的产业链，然后用 12% 止损把 7 月的回撤切掉”的故事。'
          '如果 4 月的动量榜单上没有那条链，这套规则在这半年就是负的——同池等权买入持有是 ' + pct(bench['equal_weight_universe']) + '。'))

W = [t for t in TRIPS if t['net'] > 0]
L = [t for t in TRIPS if t['net'] <= 0]
pf = sum(t['net'] for t in W) / -sum(t['net'] for t in L)
B.append(('h1', '七、最大亏损复盘：汇绿生态，4 个交易日亏 4,947 元'))
B.append(('p', '这一节只用一次回测的结果做归因，不再调参、不再换窗口，避免把复盘变成第二次过拟合。把 20 个完整买卖回合（同一只股票同一批股数算一个回合）按净盈亏排序，亏损最大的一笔是：'))
B.append(('table', ['项目', '数值'], [
    ['股票', '汇绿生态（sz001267，深市主板）'],
    ['买入', '2026-07-01 开盘 53.533 元 × 500 股 = 26,766.74 元（含费 6.96 元）'],
    ['卖出', '2026-07-07 开盘 43.686 元 × 500 股 = 21,843.13 元（含费 16.60 元）'],
    ['净亏损', money(WORST['net']) + ' 元（−18.39%），持有 4 个交易日'],
    ['卖出原因', '12% 移动止损触发'],
    ['当时账户净值', '约 156,900 元，这一笔约占总资产 3.2%'],
]))
B.append(('p', '它不是一次突发的黑天鹅，而是这套策略里最典型的失败样本：4 天内 −18.4%，买入之后最高只浮盈过 0.55%，连一次“先赚后回吐”的机会都没有——一进场就是错的。'))

B.append(('h2', '7.1 那五个交易日到底发生了什么'))
B.append(('table', ['日期', '开盘', '收盘', '涨跌', '当时发生的事'], [
    ['06-25', '61.39', '62.21', '+1.30%', '区间最高收盘价'],
    ['06-26', '60.98', '55.99', '−10.00%', '跌停。整条产业链开始松动'],
    ['06-29', '54.04', '52.86', '−5.59%', '继续下跌'],
    ['06-30', '53.98', '53.48', '+1.17%', '信号日：12−1 动量排第 3 名，+522.8%'],
    ['07-01', '53.48', '50.63', '−5.33%', '开盘买入 53.533；当天收盘已浮亏 5.4%'],
    ['07-02', '48.78', '46.83', '−7.51%', '低开低走，两天跌掉 12.5%'],
    ['07-03', '46.95', '45.68', '−2.46%', '收盘较成本已 −14.7%'],
    ['07-06', '45.99', '43.99', '−3.70%', '收盘较买入日收盘 −13.1%，触发止损'],
    ['07-07', '43.73', '41.59', '−5.46%', '开盘卖出 43.686，实际亏 18.4%'],
]))
B.append(('p', '注意 6 月 26 日那个跌停：它发生在买入之前 3 个交易日。也就是说，我们是在这条产业链已经裂开、个股已经跌了 14% 之后，才“按规则”买进去的。'))

B.append(('h2', '7.2 为什么规则会去买一只正在崩的股票'))
B.append(('p', '因为 12−1 动量只看到 5 月底为止的价格。这只票的信号计算是：2026 年 5 月月线收盘 57.92 ÷ 2025 年 6 月月线收盘 9.30 − 1 ＝ +522.8%。'
          '最近一个完整月（6 月）按定义被整个跳过，而 6 月的下跌、6 月 26 日的跌停、从 62.21 到 53.48 的 14% 回撤，在 7 月 1 日的信号里一个数字都没有。'))
B.append(('p', '这不是实现错误，是 12−1 这个定义本身的代价：跳过最近一个月是为了避开短期反转噪声，代价就是在趋势拐点上一定慢半拍。'
          '7 月 1 日这天，它同时是“过去一年涨了 5.2 倍”的第 3 名，和“刚刚跌停、正在破位”的下跌中继，而因子只看得到前者。'))

B.append(('h2', '7.3 四层原因，从远到近'))
B.append(('ul', [
    '选股层（因子本身）：动量排名的分母是一年前的股价。涨了 5 倍的票永远排在最前面，可它同时也处在“上涨空间最小、回撤空间最大”的位置。因子在极端值处的区分能力最差——涨 500% 和涨 800% 谁接下来更强，12−1 动量没有答案。',
    '组合层（这一层最致命）：7 月 1 日买的 5 只全在同一条链上——铜箔、玻纤、电子布、锂电材料、通信。它们不是 5 个独立赌注，是同一个赌注下 5 注。7 月 1 日到 7 月 7 日五只全部止损，合计亏掉 17,349 元，约占当时净值的 11%；同期沪深300 只跌了 3.0%。这不是大盘风险，是赛道风险——而“大盘状态”过滤（沪深300 对 120 日均线）在 7 月 1 日显示的是“进攻”，因为它只看大盘，看不见行业内部的崩塌。',
    '执行层（止损的落地价）：规则是“收盘跌破 12% → 次日开盘卖”。7 月 6 日收盘触发，7 月 7 日开盘 43.73 直接低开，成交 43.686。规则写的是 12%，落地是 18.4%，中间 6.4 个百分点被跳空吃掉。如果 7 月 7 日是跌停开盘，这一笔根本卖不出去——回测里我默认了能成交。',
    '规则缺陷（我自己的错）：移动止损的参照点写成了“买入后的最高收盘价”。这只票买入当天收盘 50.63 就已经比买价低 5.4%，于是“从峰值回撤 12%”实际上等于“从成本回撤 16.6%”，再叠加跳空才到 18.4%。参照点应该取 min(成本, 买入后最高收盘)，而不是只用峰值。',
]))
B.append(('p', '如果只修这一处：止损位取成本 × 0.88 = 47.11 元，7 月 3 日收盘 45.68 就会触发，7 月 6 日开盘 45.99 卖出，'
          '这一笔会从 −18.4% 变成约 −14.1%，少亏约 1,130 元。改动很小，但它修正的是“止损位比你以为的更远”这个系统性偏差，不只影响这一笔。'))

B.append(('h2', '7.4 一个想当然的“修复”，被数据否掉了'))
B.append(('p', '第一反应是加一条“过热闸门”：12−1 动量超过 400% 的候选不买。听起来很对，但把六次调仓的候选动量摊开看：'))
B.append(('table', ['信号日', '入选股票的动量', '结果'], [
    ['2026-04-01', '宏和科技 +836%、鼎泰高科 +660%、中钨高新 +593%', '三只全部盈利'],
    ['2026-06-01', '宏和科技 +972%、亚翔集成 +719%、铜冠铜箔 +640%…', '全部盈利'],
    ['2026-07-01', '铜冠铜箔 +813%、国际复材 +544%、汇绿生态 +523%…', '五只全部亏损'],
    ['2026-08-03', '宏和科技 +1031%、金安国纪 +911%、国际复材 +860%', '两盈一亏'],
]))
B.append(('p', '赚钱的票动量比亏钱的票更高（宏和科技 8 月还是 +1031% 进的场，照样赚钱）。所以“动量太高所以危险”这个解释是错的：'
          '同一个因子既给了全部利润，也给了这笔最大亏损。任何在选股层把它过滤掉的做法，会连利润一起过滤掉——这条闸门如果加上，7 月躲过去了，4—6 月的宏和科技也一起没了。'))
B.append(('p', '真正能区分“这笔亏”和“那笔赚”的，不是动量大小，也不是个股基本面，而是**买入时整条产业链处在周期的哪一段**——而这一点，单只股票的动量数据看不出来。'))

B.append(('h2', '7.5 20 个回合的盈亏结构（判断止损到底有没有用）'))
B.append(('table', ['统计项', '数值'], [
    ['完整回合数', str(len(TRIPS)) + ' 个（' + str(len(W)) + ' 胜 ' + str(len(L)) + ' 负）'],
    ['盈利合计 / 亏损合计', money(sum(t['net'] for t in W)) + ' 元 / ' + money(sum(t['net'] for t in L)) + ' 元'],
    ['盈亏比（profit factor）', '%.2f' % pf],
    ['平均盈利 / 平均亏损', money(sum(t['net'] for t in W) / len(W)) + ' 元 / ' + money(sum(t['net'] for t in L) / len(L)) + ' 元'],
    ['平均收益率（盈 / 亏）', '%.1f%% / %.1f%%' % (sum(t['ret'] for t in W) / len(W) * 100, sum(t['ret'] for t in L) / len(L) * 100)],
    ['平均持有天数（盈 / 亏）', '%.1f 天 / %.1f 天' % (sum(t['days'] for t in W) / len(W), sum(t['days'] for t in L) / len(L))],
    ['由止损退出的回合', '17 个（共 20 个）'],
]))
B.append(('p', '这张表说明止损确实在干活：盈利回合平均持有 20.7 天、赚 36%，亏损回合平均 8.3 天就被砍掉、只亏 12.6%。'
          '如果不止损，这 9 个亏损回合里至少有一半会变成 −30% 以上的坑。同时也说明另一件事：'
          '策略不是靠“胜率高”赚钱的（11 胜 9 负，胜率 55%），是靠“让赢家跑、让输家快走”的赔率结构赚钱的。'))
B.append(('p', '而最大亏损这一笔的真正问题，是它没能“快走”：4 天亏 18.4%，止损的滑点和跳空在这只票上被放大到 6.4 个百分点。'))

B.append(('h2', '7.6 我会改的三处（都不在选股层）'))
B.append(('ul', [
    '止损参照点改成 min(成本, 买入后最高收盘) × 0.88。修正“初始浮亏时止损位被动放大”的系统性偏差。',
    '加行业/相关性上限：同一条产业链最多 2 只。7 月这种“5 只同时止损”的集中亏损，只有这一条能真正限制住。本轮全部持仓都在电子/AI 硬件链上，这条规则会显著压低收益，也压低“一次亏 11%”的概率。',
    '承认成交摩擦：把“跌停无法卖出、涨停无法买入、开盘跳空”写进回测，而不是假设次日开盘一定能成交。最大亏损这一笔里有 6.4 个百分点就是来自这里。',
]))
B.append(('p', '这三条都不能拿 4—9 月这段行情去验证——这段行情已经用过了。它们必须在下一次真正没看过的窗口里跑，否则只是把这次的亏损“解释”成改进。'))

B.append(('h2', '7.7 一句话'))
B.append(('p', '这笔 −4,947 元不是选股失误，也不是运气：是“12−1 动量在拐点必慢半拍”＋“5 只押同一条链”＋“止损参照点写成峰值、落地又被跳空吃掉”三件事同时发生的结果。'
          '第一件是因子定义里躲不掉的代价，第二件和第三件是组合与执行层面的错，可以改，但只能在样本外验证。'))

B.append(('h1', '八、鲁棒性：换参数会不会全崩'))
B.append(('p', '把移动止损从 8% 到 20%、持股只数从 4 到 6 全部跑一遍，共 18 格。如果只有一个格子是正的，那就是过拟合；如果整片都是正的，至少方向不是靠运气。'))
srows = []
for trail in [None, 0.20, 0.15, 0.12, 0.10, 0.08]:
    row = ['不止损' if trail is None else '%.0f%%' % (trail * 100)]
    for n in [4, 5, 6]:
        hit = [s for s in sens if s['trail'] == trail and s['n_on'] == n][0]
        row.append('%s / %s' % (pct(hit['ret'], 1), pct(hit['dd'], 1, False)))
    srows.append(row)
B.append(('table', ['移动止损', '4 只（收益/回撤）', '5 只（收益/回撤）', '6 只（收益/回撤）'], srows))
B.append(('p', '18 格全为正，方向是稳的：止损越松、收益越高但回撤越大，止损越紧、回撤越小收益也降。'
          '但绝对水平从 +30% 漂到 +78%，说明这个数字本身对参数很敏感——它不是可以写进计划的预期收益。'
          '我选 12%/5 只，是因为它落在网格中位、回撤最低的一档，不是为了挑收益最高的那一格（最高的是 20%/4 只，+77.27%）。'))

B.append(('h1', '九、和你“少操作”的偏好冲突，我如实摆出来'))
B.append(('p', '你之前说过希望买入后尽量不动、全年 3—4 批调仓。这套 v9 是每月复核 + 事件止损，半年 6 次调仓、41 笔成交，比那个偏好频繁。'
          '所以我把季度版也跑了：'))
LBL = {'Q-无止损-无趋势': '季度调仓，不止损',
       'Q-止损12-无趋势': '季度调仓 + 12% 止损',
       'Q-无止损-趋势': '季度 + 个股趋势过滤，不止损',
       'Q-止损12-趋势': '季度 + 个股趋势过滤 + 12% 止损',
       'Q-止损15-趋势': '季度 + 趋势过滤 + 15% 止损',
       'M-止损12-趋势': '月度 + 趋势过滤 + 12% 止损',
       'M-止损12': '月度 + 12% 止损（本版）'}
qrows = []
for c in qcmp:
    if c['tag'] == 'Q-无止损-趋势':
        continue
    qrows.append([LBL.get(c['tag'], c['tag']), pct(c['return']), pct(c['max_drawdown'], 1, False),
                  '%.2f' % c['sharpe'], str(c['trades']), pct(c['mean_exposure'], 1, False)])
B.append(('table', ['版本', '收益', '最大回撤', '夏普', '成交笔数', '平均仓位'], qrows))
B.append(('p', '（“季度 + 趋势过滤，不止损”一行与“季度调仓，不止损”完全相同，因为这两个季度的信号日里，趋势过滤没有改变任何一只入选股票，已省略。）'))
B.append(('p', '季度 + 12% 止损：+32.93%，回撤 16.33%，只有 16 笔——看着最符合你的偏好。但它有一个必须知道的缺陷：'
          '7 月初止损后，规则要等到 10 月初才重新买入，所以 7 月中旬到 9 月底整整两个半月是空仓，'
          '这段时间的净值是一条横线。也就是说它赢在 4—6 月，剩下三个月什么都没做。'))
B.append(('p', '我的建议：用月度版（第六节那套）。止损不是“调仓”，它是被动风控动作；真正的主动选股半年只做了 6 次。'
          '如果你确实不想盯盘，也可以只设 12% 止损提醒、把选股固定在季度——但请接受上面那条“止损后空仓到下一季”的副作用。'))

B.append(('h1', '十、这份结果有多可信（限制清单）'))
B.append(('ul', [
    '样本内，且反复使用。同一段 6 个月行情，我先跑 5 个主变体、7 个季度/月度组合、18 格参数网格，再挑出这一组。所有看过结果再改的动作都会抬高收益、且无法在样本外复现。',
    '幸存者偏差。股票池是今天的指数成分股，这半年里退市、被剔除、暴雷的股票从一开始就不在池子里。',
    '行业集中。所有持仓都集中在电子/AI 硬件上游，5 只约等于 1 个行业。若这条链在这半年没有爆发，结果会完全不同。',
    '样本量太小。6 次调仓、41 笔成交，夏普 2.63 这种精度没有统计意义，半年业绩里有多少是能力、多少是行情，这组数据回答不了。',
    '未模拟的现实：涨停/跌停无法成交、停牌、开盘集合竞价的冲击、分红再投资（用前复权近似）、退市整理期、券商最低佣金以外的杂费。',
    '前复权口径。回测里的“开盘价”是以今天为基准倒推调整过的价格，不是当时的真实报价；收益率正确，成交金额不是当时的真实金额。',
    '没有任何实盘验证。这套规则没有下过一手真单，也没有接券商。',
]))

B.append(('h1', '十一、怎么复现'))
B.append(('table', ['文件', '内容'], [
    ['qrun/engine.py', '数据装载、指标计算（含 20 日均额、120 日均线、月线收盘）'],
    ['qrun/variants.py', '策略引擎：选股、仓位、止损、成交、费用'],
    ['qrun/finalize.py', '生成期末结果、逐笔归因、CSV'],
    ['qrun/analyze_loss.py', '单次回测 + 按 FIFO 配对 20 个买卖回合，定位最大亏损'],
    ['qrun/loss_audit.json', '20 个回合的逐笔盈亏与最大亏损的上下文'],
    ['qrun/results_current.json', '本版全部指标与逐股归因'],
    ['qrun/equity_current.csv', '125 个交易日的净值、现金、市值、仓位'],
    ['qrun/trades_current.csv', '41 笔成交明细（日期、代码、方向、股数、价格、费用、原因）'],
    ['qrun/raw/', '1590 只标的的原始日线抓取结果'],
]))
B.append(('p', '运行：python finalize.py（需在 qrun 目录下，已含数据，不重新下载）。'))

B.append(('h1', '十二、下一步建议'))
B.append(('ul', [
    '换窗口做真正的样本外：把同一套规则原样跑 2025-10 至 2026-03，不改任何参数。这是唯一能判断它是“规则”还是“故事”的办法。',
    '加行业上限：同一申万一级行业最多 2 只。这一条会显著降低这半年的收益，但也会降低“整条产业链一起崩”的风险。',
    '补现实约束：涨停不追、跌停不卖、停牌顺延，让回测更接近真实成交。',
    '用平台交叉验证：把 v9 规则搬到同花顺 SuperMind 跑同一区间，两套引擎（本地 vs 平台）结果对得上，才说明代码没算错。',
    '如果要做大股票池，需要分片抓取全市场 4500 只，并把今天的成分股换成逐日的真实成分。',
]))

B.append(('h1', '十三、一句话总结'))
B.append(('p', '这半年用 10 万元跑出 +47.74%、回撤 15.92%，本质是“月线动量在一轮 AI 硬件行情里选对了赛道 + 12% 移动止损切掉了 7 月的下跌”。'
          '规则是清楚的、可以照着执行的、账目是对得上的；但它是一次样本内探索，不是一个可以拿去预期明年收益的数字。下一件该做的事，是在没看过的窗口里原样再跑一次。'))

# ---------------- markdown ----------------
md = []
for b in B:
    if b[0] == 'title':
        md.append('# ' + b[1] + '\n')
    elif b[0] == 'sub':
        md.append('**' + b[1] + '**\n')
    elif b[0] == 'note':
        md.append('> ' + b[1] + '\n')
    elif b[0] == 'h1':
        md.append('## ' + b[1] + '\n')
    elif b[0] == 'h2':
        md.append('### ' + b[1] + '\n')
    elif b[0] == 'p':
        md.append(b[1] + '\n')
    elif b[0] == 'ul':
        for it in b[1]:
            md.append('- ' + it)
        md.append('')
    elif b[0] == 'table':
        hdr = b[1]; rows = b[2]
        md.append('| ' + ' | '.join(hdr) + ' |')
        md.append('|' + '|'.join([' --- '] * len(hdr)) + '|')
        for r in rows:
            md.append('| ' + ' | '.join(str(x) for x in r) + ' |')
        md.append('')
    elif b[0] == 'chart':
        md.append('![净值曲线](equity_curve.svg)\n')

md_path = OUT + '主板创业板动量轮动策略说明_v9_20261019.md'
open(md_path, 'w', encoding='utf-8').write('\n'.join(md))

# ---------------- svg chart ----------------
eq = full['equity']
dates = [e['date'] for e in eq]
vals = [e['total'] for e in eq]
idx = {r[0]: r[2] for r in [] }
# benchmark curve from the index, normalised to 100000 at 2026-04-01 close
import engine as E
mk = E.Market()
cs = {r[0]: r[2] for r in mk.idx['sh000300']}
b0 = cs['2026-04-01']
bvals = [100000 * cs[d] / b0 for d in dates]

W, H, PADL, PADR, PADT, PADB = 900, 380, 70, 30, 30, 45
lo = min(min(vals), min(bvals)) * 0.98
hi = max(max(vals), max(bvals)) * 1.02
def X(i): return PADL + (W - PADL - PADR) * i / (len(dates) - 1)
def Y(v): return PADT + (H - PADT - PADB) * (1 - (v - lo) / (hi - lo))
def path(vs):
    return 'M ' + ' L '.join('%.1f,%.1f' % (X(i), Y(v)) for i, v in enumerate(vs))

sv = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" font-family="Microsoft YaHei, sans-serif">' % (W, H, W, H)]
sv.append('<rect width="%d" height="%d" fill="#ffffff"/>' % (W, H))
for g in range(5):
    v = lo + (hi - lo) * g / 4
    y = Y(v)
    sv.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#e8e8e8"/>' % (PADL, y, W - PADR, y))
    sv.append('<text x="%d" y="%.1f" font-size="11" fill="#888" text-anchor="end">%s</text>' % (PADL - 8, y + 4, '{:,.0f}'.format(v)))
for i, d in enumerate(dates):
    if d[8:10] == '01' or i == 0:
        sv.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#f0f0f0"/>' % (X(i), PADT, X(i), H - PADB))
        sv.append('<text x="%.1f" y="%d" font-size="11" fill="#888" text-anchor="middle">%s</text>' % (X(i), H - PADB + 18, d[5:7] + '月'))
sv.append('<path d="%s" fill="none" stroke="#b0b0b0" stroke-width="1.6" stroke-dasharray="5,4"/>' % path(bvals))
sv.append('<path d="%s" fill="none" stroke="#c0392b" stroke-width="2.4"/>' % path(vals))
# mark max drawdown
dd_s, dd_e = cur['dd_window']
i0, i1 = dates.index(dd_s), dates.index(dd_e)
sv.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#c0392b" stroke-width="1" stroke-dasharray="3,3" opacity="0.6"/>'
          % (X(i0), Y(vals[i0]), X(i1), Y(vals[i1])))
sv.append('<text x="%.1f" y="%.1f" font-size="11" fill="#c0392b">最大回撤 %s</text>' % (X(i1) - 40, Y(vals[i1]) + 18, pct(cur['max_drawdown'], 1, False)))
sv.append('<text x="%d" y="%d" font-size="12" fill="#c0392b">— 策略净值</text>' % (W - 200, PADT + 12))
sv.append('<text x="%d" y="%d" font-size="12" fill="#999">--- 沪深300（同起点）</text>' % (W - 200, PADT + 30))
sv.append('<text x="%d" y="%d" font-size="11" fill="#666">初始 100,000 元</text>' % (PADL + 6, PADT + 14))
sv.append('</svg>')
open(OUT + 'equity_curve.svg', 'w', encoding='utf-8').write('\n'.join(sv))

# ---------------- pdf ----------------
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer,
                                Table, TableStyle, KeepTogether)
from reportlab.graphics.shapes import Drawing, Line, String, PolyLine

pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
F = 'STSong-Light'
ACC = colors.HexColor('#8c2f2f')

st_title = ParagraphStyle('t', fontName=F, fontSize=21, leading=28, textColor=colors.HexColor('#1a1a1a'))
st_sub = ParagraphStyle('s', fontName=F, fontSize=10, leading=15, textColor=colors.HexColor('#666666'))
st_note = ParagraphStyle('n', fontName=F, fontSize=10, leading=16, textColor=colors.HexColor('#5a5a5a'),
                         backColor=colors.HexColor('#f7f4ef'), borderPadding=7, spaceBefore=8)
st_h1 = ParagraphStyle('h1', fontName=F, fontSize=15, leading=21, textColor=colors.HexColor('#1a1a1a'), spaceBefore=16, spaceAfter=7)
st_h2 = ParagraphStyle('h2', fontName=F, fontSize=12, leading=18, textColor=colors.HexColor('#333333'), spaceBefore=11, spaceAfter=5)
st_p = ParagraphStyle('p', fontName=F, fontSize=10.5, leading=18, alignment=TA_LEFT, spaceAfter=6,
                      textColor=colors.HexColor('#222222'))
st_li = ParagraphStyle('li', parent=st_p, leftIndent=12, bulletIndent=2, spaceAfter=4)
st_cap = ParagraphStyle('c', fontName=F, fontSize=9, leading=13, textColor=colors.HexColor('#777777'), spaceBefore=3)

def P(t, s=st_p):
    return Paragraph(t, s)

story = []
for b in B:
    k = b[0]
    if k == 'title':
        story += [P(b[1], st_title), Spacer(1, 3)]
    elif k == 'sub':
        story += [P(b[1], st_sub), Spacer(1, 6)]
    elif k == 'note':
        story += [P(b[1], st_note), Spacer(1, 8)]
    elif k == 'h1':
        story += [P(b[1], st_h1), Line(0, 0, 1, 1)]
        story.pop()
    elif k == 'h2':
        story += [P(b[1], st_h2)]
    elif k == 'p':
        story += [P(b[1])]
    elif k == 'ul':
        story += [Paragraph(it, st_li, bulletText='·') for it in b[1]]
        story += [Spacer(1, 4)]
    elif k == 'table':
        hdr, rows = b[1], b[2]
        ncol = len(hdr)
        total = 168 * mm
        if ncol == 2:
            widths = [total * 0.32, total * 0.68]
        elif ncol == 3:
            widths = [total * 0.26, total * 0.34, total * 0.40]
        elif ncol == 4:
            widths = [total * 0.22, total * 0.30, total * 0.24, total * 0.24]
        elif ncol == 5:
            widths = [total * 0.14, total * 0.12, total * 0.10, total * 0.46, total * 0.18]
        else:
            widths = [total / ncol] * ncol
        data = [[P('<font size=9.5>' + str(h) + '</font>', st_p) for h in hdr]]
        for r in rows:
            data.append([P('<font size=9>' + str(x) + '</font>', st_p) for x in r])
        t = Table(data, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#dddddd')),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f2ede6')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ]))
        story += [t, Spacer(1, 8)]
    elif k == 'chart':
        dw = 168 * mm
        dh = dw * 380 / 900
        d = Drawing(dw, dh)
        sx = dw / W; sy = dh / H
        for g in range(5):
            v = lo + (hi - lo) * g / 4
            d.add(Line(PADL * sx, Y(v) * sy, (W - PADR) * sx, Y(v) * sy, strokeColor=colors.HexColor('#e8e8e8')))
            d.add(String((PADL - 8) * sx, Y(v) * sy - 3, '{:,.0f}'.format(v), fontName=F, fontSize=7,
                         fillColor=colors.HexColor('#888888'), textAnchor='end'))
        for i, dd in enumerate(dates):
            if dd[8:10] == '01':
                d.add(String(X(i) * sx, (H - PADB + 14) * sy, dd[5:7] + '月', fontName=F, fontSize=7,
                             fillColor=colors.HexColor('#888888'), textAnchor='middle'))
        def plc(vs, col, w, dash=None):
            pts = []
            for i, v in enumerate(vs):
                pts += [X(i) * sx, Y(v) * sy]
            ln = PolyLine(pts, strokeColor=col, strokeWidth=w)
            if dash:
                ln.strokeDashArray = dash
            return ln
        d.add(plc(bvals, colors.HexColor('#b0b0b0'), 1.1, [4, 3]))
        d.add(plc(vals, ACC, 1.7))
        d.add(String((W - 190) * sx, (PADT + 10) * sy, '— 策略净值', fontName=F, fontSize=8, fillColor=ACC))
        d.add(String((W - 190) * sx, (PADT + 28) * sy, '--- 沪深300（同起点）', fontName=F, fontSize=8,
                     fillColor=colors.HexColor('#999999')))
        story += [d, P('图 1　策略净值（红）与沪深300 同起点对照（灰）', st_cap)]
        story += [Spacer(1, 10)]

def on_page(canvas, doc):
    canvas.saveState()
    canvas.setFont(F, 8)
    canvas.setFillColor(colors.HexColor('#999999'))
    canvas.drawString(21 * mm, 12 * mm, '主板＋创业板动量轮动策略说明 v9　·　2026-04-01 至 2026-09-30　·　样本内回测，不构成投资建议')
    canvas.drawRightString(189 * mm, 12 * mm, '%d' % doc.page)
    canvas.restoreState()

pdf_path = OUT + '主板创业板动量轮动策略说明_v9_20261019.pdf'
doc = BaseDocTemplate(pdf_path, pagesize=A4, leftMargin=21 * mm, rightMargin=21 * mm,
                      topMargin=18 * mm, bottomMargin=18 * mm,
                      title='主板+创业板动量轮动策略说明 v9', author='WorkBuddy')
frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id='f')
doc.addPageTemplates([PageTemplate(id='p', frames=[frame], onPage=on_page)])
doc.build(story)
print('md   ->', md_path)
print('pdf  ->', pdf_path)
print('svg  ->', OUT + 'equity_curve.svg')
print('blocks', len(B))
