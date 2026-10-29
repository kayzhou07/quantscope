# 第五轮 P0：证据变更记录（修复后）

日期：2026-10-20　｜　对应复核：`round4/verify/VERIFY4.md`（**保留，不回写**）+ 本目录 `verify/VERIFY4_POSTFIX.md`
　｜　封存：`round5/SEAL/`（freeze_id `R5-A-FREEZE-20261020T221609+0800`，freeze_at 2026-10-20T22:16:09+08:00，数据截止 2026-10-19）

## 1. 流程缺陷（先承认）

`ROUND4.md` 与 `CANDIDATE_A_FREEZE.md` 在复核（VERIFY4）之后被**原地修改**，且未保留修改前字节。
因此复核时记录的哈希版本已不存在，本文件用"已审计哈希 + 逐条差异 + 当前哈希"来重建可追溯性：

| 文件 | 复核时哈希（VERIFY4 记录） | 当前哈希（修复后） |
|---|---|---|
| `round4/ROUND4.md` | `337900a4810e968300f39af67c71e87ad09eae9a284b8c99332c501935b57a19` | 见 §4 实测 |
| `round4/CANDIDATE_A_FREEZE.md` | `4128e5018b9796cbc051d648f8a447a0e38bd821ba55c5e527c6fd8e940c1513` | 见 §4 实测 |
| `round4/verify/VERIFY4.md` | 未修改（保留原文件） | 见 §4 实测（与审计一致） |

**教训/改进**：以后对已审计产物只做"新增 addendum 文件"，不原地改写；若必须改，先复制 `*.as_audited.*` 快照。

### 1.1 追加事件：两份修订稿在磁盘上意外消失并重建（2026-10-20T23:1x）

在执行第五轮 P0 验收时发现 `round4/ROUND4.md` 与 `round4/CANDIDATE_A_FREEZE.md` **从磁盘消失**
（`round4/` 目录 mtime 2026-10-20 22:02:53；同目录其余脚本、`round4/out/`、`round4/verify/` 均完好，
`round3/REPRODUCTION.md`、`round5/` 各文件均完好）。原因 **UNVERIFIED**（不排除 OneDrive 同步/外部移动）。
处置：
- 按最后修订内容**逐字重建**两份文件（本次写入），并在文件顶部标注"重建说明"；
- 重建后由 `round5/verify_postfix.py` 校验 F1/F2/F6 关键表述与旧 VERIFY4 保留状态（全部 PASS，见
  `round5/verify/VERIFY4_POSTFIX.md`）；
- **局限**：重建稿的字节哈希不等于复核时哈希（后者已丢失），且重建无法证明与丢失前逐字节一致；
  但结论、表格数字与审计引用均与 `round4/out/` 原始结果及 VERIFY4 记录一致。
- 同一事件也解释了用户侧"桌面 ROUND4.md / CANDIDATE_A_FREEZE.md 找不到"的一部分可能：文件曾存在于
  工作区 `round4/`，而非桌面路径；本轮已再次交付于 `round4/`。

## 2. 三项必改

### F1 — 费用压力汇总 CSV 行错误（已修）
- 旧值（错误）：`out/phase_stress_summary.csv` 中 `stress_b100n30_top15`：+93.8588% / -29.3642% / Sharpe 0.5653 / 费用 15,839.78。
- 原因 UNVERIFIED（旧运行使用了错误的变体配置，非候选 A 的 plain 月度变体）。
- 新值（正确）：**+149.6728% / -25.2261% / Sharpe 0.7469 / 费用 14,351.62**，与
  `out/phase_stress_b100n30_top15.csv` 的 x2cost 行、`verify/v4_overlay.log` 的独立复跑一致。
- 现由可复现脚本 `round4/run_stress.py` 生成（该脚本为事后补存）。
- 证据：`round4/out/phase_stress_summary.csv`、`phase_stress_b100n30_top15.csv`、`round4/verify/v4_overlay.log`。

### F2 — 冻结文件回撤峰日期错误（已修）
- 旧：`最大回撤 | -23.98%（峰 2020-03-23 前高，谷 2020-03-23）`。
- 新：`最大回撤 | -23.98%（峰 2017-11-22，谷 2020-03-23）`。
- 证据：独立复核重算与 `round5/attrib/out/a_nav.csv` 复算一致（峰 2017-11-22、谷 2020-03-23、修复 2020-09-02）。

### F6 — 候选 A 的选择不是纯训练期（已披露，非改数）
- `ROUND4.md` §1 删去"训练期最好的 b100_n30_top15"表述，表头改为"预注册候选集"，并加 F6 说明；
- §4.5 低波分数：承认训练窗口 `b100_n30_lowvol`（+156.05% / Sharpe 1.01）优于 A（+139.17% / 0.91），
  放弃它部分依据是 2024-09 以后表现 → 同样用了 OOS；
- §5 明示：**A 的选择也含 OOS 信息**，§1 的 +14.96% 只能表述为"预注册候选集的第一眼 OOS 观测"；
- `CANDIDATE_A_FREEZE.md` 顶部加入同一披露。
- 证据：`ROUND4.md` §1/§4.5/§5；`CANDIDATE_A_FREEZE.md` 顶部选择披露。

## 3. 其他随修订补充的内容（不改结论）
- `ROUND4.md` §2 加注：Phase A/A2 的 vol/Sharpe 用引擎 n−1 口径，Phase B 之后用 n 口径，差 ≤5.4e-5 / 2.3e-4。
- `ROUND4.md` §8 回填 VERIFY4 判定（1,468 项 0 偏离、28/28 无未来函数、负结果复现、与第三轮 S0 一致），并列出 F1/F2/F6 修复。
- 补存 `round4/run_phaseH.py` 与 `round4/run_stress.py`（原为内联运行，provenance 弱）。
- 新增 `round5/` 全套（封存、前向脚本、归因、平台差异计划、第三轮 errata、OOS 窗口口径补全）。

## 4. 当前文件哈希与验收

见 `round5/verify/VERIFY4_POSTFIX.md`：该文件由脚本 `round5/verify_postfix.py` 生成，逐项检查
F1/F2/F6 是否已落地、旧 VERIFY4 是否未被改写、封存快照是否可校验，并记录当前哈希与未覆盖事项。
