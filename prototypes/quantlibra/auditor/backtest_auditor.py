#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回测审计器（Backtest Auditor）—— 量化回测「可信度」静态审计工具

自动扫描聚宽(JoinQuant)策略代码，检测会让回测收益虚高的常见偏差。
审计结论基于项目里已实证的反模式（见 Downloads/之后的Ai策略/策略诊断结论.md）：

  规则 1  幸存者偏差      get_index_stocks 未传 date，用「今天成分股」回测「过去」
  规则 2  涨跌停成交假象   LimitOrderStyle 用 0.99/1.01×last_price 追板
  规则 3  流动性幻觉      小市值 + 持仓集中，忽略真实冲击成本
  规则 4  未来函数        end_date=context.current_dt 用当天数据做当天决策
  规则 5  成本/滑点缺失   未设置 set_order_cost / set_slippage
  规则 6  过度拟合        全局参数堆积（>60 个可调参数）
  规则 7  参数挖掘痕迹    大量 enable/use 实验开关（p-hacking）
  规则 8  样本外验证缺失  参数多但无样本外/训练验证划分
  规则 9  基准不当        小市值策略用沪深300基准虚高超额

用法：
  python3 backtest_auditor.py <策略文件.py> [--out 报告.md]
"""

import re
import sys
import os
from dataclasses import dataclass, field, asdict

# ---------------- 数据结构 ----------------

@dataclass
class Finding:
    rule_id: str          # 规则编号
    rule_name: str        # 规则名
    severity: str         # 高 / 中 / 低
    line: int             # 触发行号（1-based）
    snippet: str          # 触发代码片段
    why: str              # 为什么是问题
    fix: str              # 怎么修

@dataclass
class Rule:
    rule_id: str
    name: str
    severity: str
    summary: str
    scan: callable        # scan(lines: list[str]) -> list[Finding]

# ---------------- 规则实现 ----------------

def _line_snippet(line):
    return line.strip()[:80]

# 规则1：幸存者偏差
def _scan_survivorship(lines):
    out = []
    pat_call = re.compile(r'get_index_stocks\(')
    for i, line in enumerate(lines):
        if not pat_call.search(line):
            continue
        # 检查该行（及其续行）的调用括号内是否有 date 参数
        # 简化：只看同一行内有没有 date=；跨行调用罕见，这里逐行判断并给提示
        if 'date' not in line:
            out.append(Finding(
                'R1', '幸存者偏差', '高', i + 1, _line_snippet(line),
                'get_index_stocks 未传 date 参数，会用「当前」成分股回测「历史」：'
                '期间退市/ST/被剔除的股票被天然排除，选股池被污染，收益虚高。',
                '改为 get_index_stocks(code, date=context.previous_date) 或逐日取当日成分股。',
            ))
    return out

# 规则2：涨跌停成交假象
def _scan_limit_order(lines):
    out = []
    # 0.9x 折扣卖出 / 1.0x 溢价买入，且价格锚定 last_price —— 追板/挂板信号
    pat = re.compile(r'LimitOrderStyle\(\s*(0\.9\d|1\.0\d)\s*\*\s*.*?\.last_price')
    for i, line in enumerate(lines):
        m = pat.search(line)
        if m:
            out.append(Finding(
                'R2', '涨跌停成交假象', '高', i + 1, _line_snippet(line),
                '用 0.99/1.01×last_price 挂限价单追涨跌停，一字板实盘买不进/卖不出，'
                '回测却按成交计入，把买不到的利润算成自己的。',
                '涨跌停附近改为不成交（检查 high_limit/low_limit 后跳过），或改用更贴近真实的价格。',
            ))
    return out

# 规则3：流动性幻觉（小市值 + 持仓集中）
def _scan_liquidity(lines):
    out = []
    cap_min = cap_max = None
    holdings = None
    for i, line in enumerate(lines):
        m = re.search(r'filter_market_cap_min[\'\"]?\s*[:=]\s*(\d+)', line)
        if m:
            cap_min = int(m.group(1))
        m = re.search(r'filter_market_cap_max[\'\"]?\s*[:=]\s*(\d+)', line)
        if m:
            cap_max = int(m.group(1))
        m = re.search(r'holdings_num[\'\"]?\s*[:=]\s*(\d+)', line)
        if m:
            holdings = int(m.group(1))
    if cap_max is not None and cap_max <= 150:
        why = ('市值上限 %.0f 亿，属小市值区间；小票真实冲击成本、容量远高于回测假设，'
               '资金稍大就打滑。' % cap_max)
        if holdings is not None and holdings <= 5:
            why += ' 且持仓集中（holdings_num=%d），进一步放大单票冲击。' % holdings
        # 锚定到 cap_max 定义行
        line_no = next((i + 1 for i, l in enumerate(lines) if 'filter_market_cap_max' in l), 1)
        out.append(Finding(
            'R3', '流动性幻觉', '高', line_no,
            _line_snippet(lines[line_no - 1]),
            why,
            '小市值策略需实测单票冲击成本/容量上限，或在报告中明确标注为「小资金上限运行」。',
        ))
    return out

# 规则4：未来函数（end_date 用当天）
def _scan_lookahead(lines):
    out = []
    pat = re.compile(r'end_date\s*=\s*context\.current_dt(?!\.previous)')
    for i, line in enumerate(lines):
        if pat.search(line):
            out.append(Finding(
                'R4', '未来函数', '高', i + 1, _line_snippet(line),
                '数据终点用了 context.current_dt（当天），当天行情/成分尚未完整，'
                '等于用「今天」的数据做「今天」的决策，回测收益含未来信息。',
                '统一改用 end_date=context.previous_date，只用已收盘的昨日数据。',
            ))
    return out

# 规则5：成本/滑点缺失
def _scan_cost(lines):
    out = []
    has_cost = any('set_order_cost' in l for l in lines)
    has_slip = any('set_slippage' in l for l in lines)
    if not has_cost and not has_slip:
        out.append(Finding(
            'R5', '成本/滑点缺失', '中', 1, '(全局)',
            '未设置 set_order_cost / set_slippage，回测按零成本成交，'
            '高换手策略的真实成本会显著侵蚀收益。',
            '显式设置 set_order_cost(佣金+印花税) 与 set_slippage(滑点)，用更严口径重跑。',
        ))
    return out

# 规则6：过度拟合（参数堆积）
def _count_params(lines):
    return sum(1 for l in lines if re.match(r'^\s*g\.[a-zA-Z_]\w*\s*=', l))

def _count_switches(lines):
    return sum(1 for l in lines if re.match(r'^\s*g\.(?:enable|use)_[a-zA-Z_]\w*\s*=', l))

def _scan_overfit(lines):
    out = []
    n = _count_params(lines)
    if n > 150:
        out.append(Finding(
            'R6', '过度拟合(参数堆积)', '高', 1, '(全局) 约 %d 个全局参数' % n,
            '全局变量/可调参数多达 %d 个，远超「少数几个参数」的可解释区间；'
            '参数越多，回测越容易拟合历史噪声而非真实规律，样本外大概率失效。' % n,
            '冻结绝大多数参数，只保留有经济学含义的核心参数（<10 个），'
            '并用样本外区间或 walk-forward 验证稳健性。',
        ))
    elif n > 60:
        out.append(Finding(
            'R6', '过度拟合(参数堆积)', '中', 1, '(全局) 约 %d 个全局参数' % n,
            '全局变量约 %d 个，参数偏多，存在拟合历史噪声的风险。' % n,
            '精简参数，并补充样本外验证。',
        ))
    return out

# 规则7：参数挖掘痕迹（实验开关堆积）
def _scan_switch_mining(lines):
    out = []
    n = _count_switches(lines)
    if n > 15:
        line_no = next((i + 1 for i, l in enumerate(lines)
                        if re.match(r'^\s*g\.(?:enable|use)_[a-zA-Z_]\w*\s*=', l)), 1)
        out.append(Finding(
            'R7', '参数挖掘痕迹(实验开关堆积)', '中', line_no,
            _line_snippet(lines[line_no - 1]),
            '多达 %d 个 enable/use 实验开关。大量「试了又关」的开关是参数挖掘(p-hacking)的'
            '典型痕迹：反复调整开关组合直到回测好看，容易把偶然收益当成规律。' % n,
            '收敛开关：把默认关闭的实验开关从代码里删掉，只保留真正生效的逻辑，并做样本外验证。',
        ))
    return out

# 规则8：样本外验证缺失（依赖参数规模）
def _scan_no_oos(lines):
    out = []
    n = _count_params(lines)
    if n <= 60:
        return out  # 参数少的小策略没有 OOS 不算问题
    oos_pat = re.compile(r'样本外|样本内|验证集|训练集|out[_-]?of[_-]?sample|walk[_-]?forward|oos')
    if not any(oos_pat.search(l) for l in lines):
        out.append(Finding(
            'R8', '样本外验证缺失', '中', 1,
            '(全局) %d 个参数但无样本外/验证划分' % n,
            '策略有 %d 个参数，但代码里没有任何样本外/训练验证划分——'
            '意味着所有参数都对着同一段历史反复调出来，无法证明稳健性。' % n,
            '划分样本内/样本外两段：样本内调参、样本外只看一次；或做 walk-forward 滚动验证。',
        ))
    return out

# 规则9：基准不当（小市值策略配沪深300基准）
def _scan_benchmark(lines):
    out = []
    is_small = False
    for l in lines:
        m = re.search(r'filter_market_cap_max[\'\"]?\s*[:=]\s*(\d+)', l)
        if m and int(m.group(1)) <= 150:
            is_small = True
            break
    if not is_small:
        return out
    for i, l in enumerate(lines):
        if re.search(r'set_benchmark\(\s*[\'"]000300\.XSHG[\'"]', l):
            out.append(Finding(
                'R9', '基准不当', '低', i + 1, _line_snippet(l),
                '小市值策略用沪深300(大盘蓝筹)做基准，会系统性高估超额收益——'
                '小市值牛市里沪深300往往大幅跑输，换个匹配基准超额就大幅缩水。',
                '改用与小市值更匹配的基准，如中证2000(932000)或国证2000(399303)。',
            ))
            break
    return out

RULES = [
    Rule('R1', '幸存者偏差', '高', '成分股时间错位', _scan_survivorship),
    Rule('R2', '涨跌停成交假象', '高', '极端价成交假设', _scan_limit_order),
    Rule('R3', '流动性幻觉', '高', '小市值容量失真', _scan_liquidity),
    Rule('R4', '未来函数', '高', '当天数据当天决策', _scan_lookahead),
    Rule('R5', '成本/滑点缺失', '中', '零成本成交', _scan_cost),
    Rule('R6', '过度拟合', '高', '参数堆积', _scan_overfit),
    Rule('R7', '参数挖掘痕迹', '中', '实验开关堆积', _scan_switch_mining),
    Rule('R8', '样本外验证缺失', '中', '无OOS划分', _scan_no_oos),
    Rule('R9', '基准不当', '低', '基准错配', _scan_benchmark),
]

# ---------------- 报告生成 ----------------

def _severity_rank(sev):
    return {'高': 0, '中': 1, '低': 2}.get(sev, 3)

def generate_report(path, findings):
    """生成结构化 markdown 审计报告（模板版；可被 LLM 层进一步改写）"""
    findings = sorted(findings, key=lambda f: (_severity_rank(f.severity), f.rule_id, f.line))
    n_high = sum(1 for f in findings if f.severity == '高')
    n_mid = sum(1 for f in findings if f.severity == '中')
    n_low = sum(1 for f in findings if f.severity == '低')

    # 可信度评级
    if n_high >= 2:
        verdict = '⚠️ 高风险：存在 %d 处高严重度偏差，回测收益大概率虚高，不可直接采信' % n_high
    elif n_high == 1:
        verdict = '⚠️ 中风险：发现 1 处高严重度偏差，需修复后重新回测'
    elif n_mid >= 1:
        verdict = 'ℹ️ 低风险：无高严重度偏差，但有 %d 处中等提示，建议补足后复核' % n_mid
    else:
        verdict = '✅ 未发现已知偏差模式，回测可信度较高（规则审计的覆盖范围见底部）'

    lines = []
    lines.append('# 回测审计报告')
    lines.append('')
    lines.append('- **审计对象**：`%s`' % os.path.basename(path))
    lines.append('- **检测规则**：%d 条（幸存者偏差 / 涨跌停成交 / 流动性 / 未来函数 / 成本滑点）' % len(RULES))
    lines.append('- **发现问题**：高 %d · 中 %d · 低 %d' % (n_high, n_mid, n_low))
    lines.append('')
    lines.append('## 总体结论')
    lines.append('')
    lines.append(verdict)
    lines.append('')

    if not findings:
        lines.append('（未触发任何规则）')
        lines.append('')

    for f in findings:
        lines.append('## [%s] %s —— %s' % (f.rule_id, f.rule_name, f.severity))
        lines.append('')
        lines.append('- **位置**：第 %d 行' % f.line)
        lines.append('- **代码**：`%s`' % f.snippet.replace('`', "'"))
        lines.append('- **问题**：%s' % f.why)
        lines.append('- **修复**：%s' % f.fix)
        lines.append('')

    lines.append('---')
    lines.append('')
    lines.append('*本报告由规则引擎静态审计生成，覆盖 %d 类已知偏差模式；'
                 '静态分析有盲区，未覆盖「多重检验校正 / 交易逻辑与数据时序对齐 / '
                 '换手率与容量敏感性」等更隐蔽偏差，需人工复核。*' % len(RULES))
    return '\n'.join(lines)

# ---------------- 主流程 ----------------

def audit_file(path):
    with open(path, 'r', encoding='utf-8', errors='replace') as fp:
        text = fp.read()
    lines = text.splitlines()
    findings = []
    for rule in RULES:
        try:
            findings.extend(rule.scan(lines))
        except Exception as e:  # 规则异常不应中断整体审计
            print('  [warn] 规则 %s 执行失败: %s' % (rule.rule_id, e), file=sys.stderr)
    return findings

def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    path = argv[1]
    out_path = None
    if '--out' in argv:
        out_path = argv[argv.index('--out') + 1]

    findings = audit_file(path)
    report = generate_report(path, findings)

    if out_path:
        with open(out_path, 'w', encoding='utf-8') as fp:
            fp.write(report)
        print('报告已写入: %s' % out_path)
    else:
        print(report)
    return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv))
