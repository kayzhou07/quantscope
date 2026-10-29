# 前向输出 schema（round5/paper）

所有 JSON 一律 UTF-8、`sort_keys=False`，写盘为原子替换；每个记录都带 `freeze_id`、`freeze_at`、
`data_cutoff` 与 `schema_generated_at`。命令实现见 `cli.py`，验证见 `validate.py`。

## 1. signal（`runs/<asof>/signals/signal_<asof>.json`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema` | str | `round5-signal-1` |
| `signal_id` | str | `sig-` + 16 hex；由除 `generated_at` 外的全部字段 canonical JSON 的 sha256 前 16 位 |
| `generated_at` | str | 生成时刻（ISO8601 +08:00） |
| `asof` | str | 信号日（T 收盘） |
| `available_at` | str | 数据可用的最早时刻（本实现 = asof 15:00+08:00） |
| `planned_exec_date` | str | 计划成交日（T+1 开盘，来自交易日历） |
| `kind` | str | `initial` / `scheduled` |
| `state` | obj | 状态续算：`state`,`letter`,`state_prev`,`state_run_days`,`candidate_today`,`pending_direction`,`pending_confirm_days`,`confirm_required_days` |
| `action` | str | `target` / `risk_off` / `hold_cash` |
| `reason` | str | `state_forbidden` / `scheduled` / `no_candidate` / `no_affordable_candidate` |
| `targets` | obj | `{code: weight}`，权重合计 = `exposure` |
| `nav` | float | 信号日收盘账户净值（新现金账户 100000；继承影子账户 274560.69） |
| `exposure` | float | 目标股票暴露（B=1.0 / N=0.30 / E/C=0） |
| `holdings_before` | obj | 信号日持仓 `{code: shares}` |
| `data` | obj | `data_manifest_sha256`,`regime_csv_sha256`,`freeze_id` |
| `diagnostics` | obj | 候选数、截断数、整手丢弃明细、所选明细、每只预算 |

`PENDING_DATA` 记录（`runs/<asof>/signals/PENDING_<asof>.json`）：`status`,`reason`,`no_signal_created=true`，
**不包含 targets，不产生成交**。

## 2. paper ledger（`runs/accounts/<account>.json`）

| 字段 | 说明 |
|---|---|
| `schema` | `round5-paper-ledger-1` |
| `account_id`,`freeze_id`,`freeze_at`,`created_at` | 账户身份与冻结信息 |
| `initial_cash`,`cash` | 初始/当前现金 |
| `positions` | `{code: {shares, cost_total, last_price, last_date, open_date}}` |
| `pending_orders` | 未成交订单（含 `order_id`,`signal_id`,`side`,`requested_shares`/`target_weight`,`expires_date`,`attempts`,`last_reason`） |
| `fills` | 成交：date/order_id/signal_id/code/side/shares/price/amount/commission/stamp/transfer/fee_total |
| `nav_history` | 每日：nav/cash/market_value/exposure/n_positions/fees/state/target_exposure/pending_orders/data_asof/freeze_at/generated_at |
| `processed_signals` | `{signal_id: fill_batch_id}`，保证同一信号只下一批单 |
| `events` | 公司行动、订单过期等 |
| `last_processed_date` | 已推进到哪一天 |

恒等式：每日 `|nav − (cash + Σ shares×last_price)| ≤ 1e-6`，违反即 `LedgerError` 中止。

## 3. daily audit（`runs/<asof>/daily_audit.json`）

`status=OK` 时含 `audit`：nav、cash、market_value、exposure、n_positions、fees、state、target_exposure、
`cash_reason`（互斥：`state_forbidden`/`pending_fill`/`no_candidate`/`no_affordable_candidate`/`off_window`/
`fully_invested`/`design_cap_or_lot_rounding`/`cash_above_cap`）、`identity_ok`、`data_asof`、`freeze_at`。
数据缺失时 `status=PENDING_DATA`，不写任何账户变化。

## 4. validation（`runs/validation/validation.json`）

`schema=round5-validation-1`，`checks[]` 每项含 `name,pass,detail|error,seconds`，`pass` 为全部核心项为真。

## 5. 命令与静态输入

```
freeze-check
state-check --asof 2026-10-19 --cutoffs 2018-12-28,2021-06-30,2023-12-29,2026-10-19
monthly-signal --asof <T> [--kind initial|scheduled] [--account <id>] [--nav <float>]
daily-audit --asof <T> [--account <id>]
paper-advance --to <T> [--account <id>]
monthly-report --month YYYY-MM [--account <id>]
validate
```

输入样例见 `samples/`：`sample_signal.json`（冻结规则的信号形状）、`sample_bars.json`（一日原始行情
形状）、`sample_fill_expected.json`（该样例在合成行情下应产生的成交）。
