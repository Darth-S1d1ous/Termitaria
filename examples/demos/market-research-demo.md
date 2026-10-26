# Market Research Demo：Copper Desk 的有来源市场简报

## 一句话

让 Copper Desk 团队在 General 里完成一次真实的市场研究：查上周 COMEX 铜价 → 用来源解释涨跌 → reviewer 拒绝没有来源的数字 → 最后产出 report + deck。

## 团队

选择 `copper-desk` 模板，得到 3 个 agent：

| Agent | 角色 |
| --- | --- |
| Desk Lead | orchestrator，拆任务、派活、收口，写 report/deck |
| Market Researcher | 查行情、财报和新闻；每个数字必须带工具返回的来源 |
| Reviewer | 拒绝任何不在任务或工具结果里的数字；只给 keep/drop 意见 |

模板自带 `market-data` 和 `filings` 两个 skill。市场数据来自 Yahoo Finance 等工具，新闻/原因来自 web search。

## 操作步骤

### 1. 创建项目

1. 打开 <http://127.0.0.1:3000>。
2. 在 message 栏左侧选择模型 **DeepSeek-V4.1-Flash**。
3. 点左侧栏的 `+`（New project）。
4. Project name 输入：

```text
Copper Desk Demo 1026
```

5. 点 **Confirm**。
6. Team 步骤选择 **copper-desk** 卡片。
7. 点 **Continue**。
8. Knowledge 步骤可以留空；如果想让知识图一开始有主题，可在 Text 里填：

```text
Track COMEX copper front-month (HG=F) for the most recent full trading week.
```

点 **Add text**，然后点 **Create**。

### 2. 在 General 发主任务

进入 **General**，确认模型是 `DeepSeek-V4.1-Flash`，粘贴：

```text
请做一份上周 COMEX 铜期货（HG=F）的市场简报。要求：
1) 给出最近五个完整交易日的日期、收盘价、周平均价和周涨跌幅；
2) 用至少两个可点击来源解释本周涨跌的主要驱动；
3) 每个数字和归因都必须来自你实际调用的工具，查不到就明确写“查不到”，不要补造；
4) 最后输出一份带来源的 report 和一份 deck。
```

按 Enter。Desk Lead 会拆任务并委派 Market Researcher；Reviewer 会在成稿前检查数字来源。

### 3. 在 General @Market Researcher

等 General 输入框恢复可用后：

1. 在输入框里输入 `@`。
2. 在弹出列表里点 **Market Researcher**。
3. 粘贴：

```text
请给出你实际查到铜价的来源 URL、查询时间，以及你使用的 ticker/合约。不要凭记忆补充数字。
```

4. 按 Enter，等它回答。

**展示点**：它应该给出实际工具返回的 URL 和查询时间；如果没查到，应该明确说没查到，而不是给一个看起来合理的价格。

### 4. 在 General @Reviewer

再次等输入框恢复可用，然后：

1. 输入 `@`，选择 **Reviewer**。
2. 粘贴：

```text
这份 brief 里哪些数字或句子你会拒绝？请列出你的判定标准，并指出最容易被模型编造的数字或归因。
```

3. 按 Enter。

**展示点**：Reviewer 只给 keep/drop 意见，不重写报告；它会明确说“不在任务或工具结果里的数字一律不接受”。

### 5. 如果 Desk Lead 停下来提问

Desk Lead 可能问你要不要接受某个缺失数据、或是否允许换一个来源。粘贴：

```text
请按你认为最可辩护的方案继续，找不到的数据就明确写“找不到”；完成 report 和 deck。
```

### 6. 收口展示

等 plan 完成：

1. 点 **Artifacts**：展示 report 和 deck，点开下载。
2. 点 **Knowledge**：展示项目知识图里的公司、指标、来源等实体和关系。
3. 回到 **General**：展示最终 brief 和 Reviewer 的拒绝记录。

## 检查点

- [ ] plan 完成
- [ ] Artifacts 里有 report 和 deck，下载 200
- [ ] report 里每个数字都能指回一个来源 URL
- [ ] Reviewer 明确拒绝过至少一条没有来源的数字/结论
- [ ] Knowledge 里有本次研究写入的实体/关系

## 时间与降级

- 现场完整跑一轮通常 **8–15 分钟**，取决于搜索和数据源响应。
- 课前先跑一次，保留完成的项目；现场如果搜索很慢，直接切到预跑项目展示 report/deck，同时让现场任务继续跑。
- 如果 web search 不可用，让 Researcher 明确说“查不到”，让 Reviewer 拒绝未来源数字——这本身就是这个 Demo 想展示的行为，不要临时编数据。

## 讲解要点

1. **同一个 harness，不同团队**：Autoresearch 和 Copper Desk 只是两份 `SwarmSpec`，控制面代码不变。
2. **来源是硬约束**：Researcher 的每个数字都来自工具；Reviewer 不负责重写，只负责拒绝。
3. **项目知识图是共享库**：价格、公司、来源、关系留在项目里，后面的 session 还能用。
4. **@mention 是真实路由**：不是“让 orchestrator 转述”，而是直接叫醒指定的 specialist，它会读当前项目的知识图和自己的记忆。
5. **交付物是产品的一部分**：report 和 deck 直接进 Artifacts，不是聊天里的一段文本。
