# Japan Small/Micro QVM v0.1

> **状态**：研究准备  
> **实验类型**：日本小盘 / 微盘多因子  
> **交易方向**：long-only  
> **数据频率**：日频价格 + 公开财报  
> **主调仓频率**：季度  
> **对照频率**：月度

---

## 假设

日本小盘 / 微盘股票中，公开财务质量和估值信号在低换手、long-only、成本税后约束下仍有研究价值。Momentum 不作为核心 alpha，只作为低换手确认或风险过滤。

---

## 不验证

- 不验证 GTAA
- 不验证短线择时
- 不验证做空
- 不验证高频执行
- 不验证复杂机器学习
- 不验证海外市场迁移

---

## 股票池

主样本为日本上市普通股。v0.1 仅允许使用截至 `rebalance_date` 已公开、可审计的信息。

初始排除规则：

- 非普通股
- ETF / REIT / infrastructure fund / 优先股等
- 上市未满 252 个交易日
- 过去 60 个交易日价格或成交额数据不足
- 过去 60 个交易日 median trading value 低于配置阈值
- 截至调仓日不可交易
- 无法获得必要财务字段

所有排除结果必须输出到 `excluded_YYYYMM.csv`，不能只在代码里静默过滤。

---

## 因子

变量总数限制在 8 个以内。

Quality 候选：

- operating_profit / total_assets
- gross_profit / total_assets
- equity_ratio 或 debt_to_assets

Value 候选：

- book_to_market
- earnings_yield
- dividend_yield 仅作辅助

Momentum 候选：

- 12-1 month return
- 6-1 month return 或 52-week-high distance 二选一

处理规则：

- winsorize：1% / 99%
- missing value：不补成 0，必须记录 `missing_flag`
- standardize：横截面 z-score
- industry neutral：v0.1 不强制，只报告行业暴露

---

## 组合构建

研究组合：

- 20-50 只
- 理论等权
- 用于验证因子信号

可执行组合：

- 由资金量、100 股单位、未复权价格、ADV 约束决定
- 允许现金留存
- 可能只有 8-30 只

默认规则：

```text
买入：排名进入 top 10% 或前 50
持有：仍在 top 20% 或前 100 则继续持有
```

该规则用于降低换手，避免每次调仓全量重排。

---

## 交易约束

每笔订单必须经过：

```text
shares = floor(target_value / price / 100) * 100
if shares == 0: skip and keep cash
if order_value > ADV_cap: reduce order or skip
if price is limit-up/limit-down affected: mark uncertain fill
```

v0.1 默认 ADV 约束：

```text
单笔买入金额 <= 过去 60 日 median trading value 的 0.5%
单笔卖出金额 <= 过去 60 日 median trading value 的 0.5%
```

---

## 成本与税务

每次报告至少输出：

- gross
- after_cost_pre_tax
- after_cost_after_tax_taxable
- after_cost_after_tax_NISA_like

成本三档：

- optimistic：commission + 0.5 * estimated spread
- base：commission + 1.0 * estimated spread + small impact
- pessimistic：commission + 2.0 * estimated spread + larger impact

NISA-like 口径必须标记：

```text
loss_not_deductible = true
```

---

## 模拟实盘准入

进入小额实盘前至少满足：

- 回测可复现
- 100 股单位和 ADV 约束全部生效
- 悲观成本下策略不崩溃
- 理论组合和可执行组合差异可解释
- 至少完成一个模拟调仓周期
- `failure_cases.md` 已人工复核

---

## 退出条件

任一条件触发，实验进入复核或暂停：

- 同一 config 无法复现结果
- 成本后表现完全依赖乐观滑点假设
- 可执行组合长期偏离研究组合，且差异无法解释
- 组合由单一股票或单一行业主导
- 模拟实盘滑点显著高于基准成本模型
- 发现 look-ahead、survivorship 或公司行动处理错误
