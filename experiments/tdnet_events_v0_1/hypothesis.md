# TDnet Events v0.1

> **状态**：观察库准备  
> **实验类型**：日本公告事件观察  
> **交易方向**：暂不交易  
> **数据频率**：公告时间戳 + 日频价格  
> **目标**：验证事件解析稳定性和事件后收益分布，为未来 PEAD 实验做准备。

---

## 假设

日本市场中，部分低关注、小盘、个人投资者占比较高的公司，在正面公告后可能存在反应不足。v0.1 不交易，只建立事件库并观察后续收益。

---

## 记录事件

v0.1 只记录：

- upward_revision
- dividend_increase
- share_buyback
- earnings_release
- other

暂不做：

- NLP tone
- 复杂 surprise
- 自动交易
- 盘中公告交易
- PEAD 实盘

---

## 事件表字段

```text
event_id
announcement_datetime
code
company_name
document_type
event_label
title
url_or_doc_id
parsed_flag
parse_confidence
notes
next_1d_return
next_5d_return
next_20d_return
next_60d_return
```

---

## Go / No-Go

未来进入 PEAD 回测或模拟交易前，至少满足：

- 事件抓取可复现
- 公告时间戳可用于判断可交易时点
- 核心事件分类准确率可接受
- 有足够样本解释事件后收益分布
- 事件后收益在成本和滑点后仍可能有研究价值

---

## 退出条件

- 解析错误率过高
- 公告时间戳无法可靠判断可交易窗口
- 事件分类过于主观，无法复现
- T+1 后没有可观察漂移
