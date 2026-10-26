# Autoresearch Demo：从文献库到 reviewed experiment plan

## 一句话

让一个 Autoresearch swarm 读项目材料，自己跑完 **literature gap scan → hypothesis → experiment design → methods review**，最后由 reviewer 攻击设计，PI 收口并写出 reviewed experiment plan。

## 团队

选择 `autoresearch` 模板，得到 5 个 agent：

| Agent | 角色 |
| --- | --- |
| Principal Investigator | orchestrator，拆任务、派活、收口 |
| Literature Collector | 查文献、把论文写进知识图 |
| Idea Generator | 从 gap 生成假设 |
| Experiment Designer | 把假设变成实验设计 |
| Skeptical Reviewer | 攻击方法、统计、新颖性 |

## 使用的材料

本机如果已有下面两个 case file，就在 Knowledge 步骤上传：

```text
examples/glossary-faithfulness/hypotheses.md
examples/glossary-faithfulness/experiment_design_section.md
```

这些 case materials 被 `.gitignore` 忽略，只在本地存在；如果没有它们，直接用下面的 **Fallback seed** 粘贴到 Knowledge 的 Text 框，Demo 一样能跑。

### Fallback seed

把下面这段整体粘贴到 **Knowledge → Text**，点 **Add text**：

```text
Research question: Does appending a short, definition-only glossary of terms taken from the passage increase claim-level faithfulness in biomedical question answering over MedQuAD, relative to an identical passage-only instruction? Faithfulness means each factual claim in the answer is supported by the passage.

Use 30 MedQuAD items, Qwen2.5-7B-Instruct, temperature 0, and a ~200-token glossary. The contribution is the confound accounting (glossary content vs. prompt length/shape), not the definition-injection question itself. The project should produce a reviewed experiment plan, not a measured result.
```

## 操作步骤

### 1. 创建项目

1. 打开 <http://127.0.0.1:3000>。
2. 在 message 栏左侧的模型下拉框选择 **DeepSeek-V4.1-Flash**。
3. 点左侧栏的 `+`（New project）。
4. Project name 输入：

```text
Autoresearch Demo 1026
```

5. 点 **Confirm**。
6. Team 步骤选择 **autoresearch** 卡片。
7. 点 **Continue**。
8. Knowledge 步骤：
   - 粘贴 Fallback seed 到 Text，点 **Add text**；或
   - 点 **Add files**，选择上面两个本地 case files。
9. 点 **Create**。

### 2. 发主任务

进入 **General**，确认模型是 `DeepSeek-V4.1-Flash`，粘贴：

```text
请用项目里的材料跑完整的研究流程。研究问题是：在 MedQuAD 生物医学问答中，给模型附加一段仅含定义的 glossary，是否能提高 claim-level faithfulness（每条事实性 claim 都能由 passage 支持），相对于只有 passage 的对照？请按 literature gap scan → hypothesis → experiment design → methods review 的顺序推进；reviewer 必须在收口前攻击实验设计。最后输出一份 reviewed experiment plan。
```

按 Enter。顶部 plan bar 会出现四个步骤；PI 会开始委派 specialist。

### 3. 在 PI 和 designer 交接时私聊 Idea Generator

当 plan 进入 **experiment design**，或者你在 speaker 里看到 Experiment Designer 开始工作时：

1. 在左侧 chat 列表点 **Idea Generator**。
2. 确认模型是 `DeepSeek-V4.1-Flash`。
3. 粘贴：

```text
现在 PI 和 experiment designer 正在讨论设计。请告诉我：你在这个项目里给 PI 的假设是什么？请引用当前项目知识图里支持这个 gap 的上传文档，并引用你自己记忆里的一行。如果当前项目记忆里没有，就明确说没有，不要引用其他项目。
```

4. 按 Enter，等它回答。

**展示点**：它应该引用当前项目的 hypothesis 和上传材料；如果它说“当前项目记忆里没有”，这是正确的项目隔离，不是失败。

### 4. 回到 General，推进或回答 PI 的决策问题

- 如果 PI 已经完成四步并写入 report：直接进入第 5 步。
- 如果 PI 停下来问你一个问题（例如两个设计版本冲突、glossary 定义来源），粘贴：

```text
请按你认为最可辩护的方案继续，不要停下来问我；完成四个步骤，并写入 report artifact。
```

### 5. 在 General 里 @Literature Collector

等 General 的输入框恢复可用后：

1. 在 General 输入框里输入 `@`。
2. 在弹出列表里点 **Literature Collector**。
3. 粘贴：

```text
在这个项目的知识图里，哪份上传材料最接近我们的 prior work？你从它验证到的具体数字或 claim 是什么？请引用文档标题。
```

4. 按 Enter，等它回答。

**展示点**：被 @ 的 specialist 会用当前项目的知识图和自己的 gap-scan 记忆回答；它应该拒绝把没有上传的外部论文说成“项目里的文件”。

### 6. 收口展示

等顶部四个步骤全部变绿：

1. 点 **Artifacts**：展示 report 和中间设计稿，点开下载。
2. 点 **Knowledge**：展示上传文档和 agent 抽出的概念/关系；点一个节点读取文本。
3. 回到 **General**：展示 PI 的最终总结和 reviewer 的攻击记录。

## 检查点

- [ ] plan 的四个步骤全部 `done`
- [ ] Artifacts 里有 `reviewed experiment plan` 或等价 report，下载 200
- [ ] Knowledge 里有当前项目的上传文档和实体/关系
- [ ] Idea Generator 引用的是当前 glossary 项目的假设，不是另一个项目
- [ ] @Literature Collector 的回答引用当前项目文档，而不是编造外部论文

## 时间与降级

- 现场完整跑一轮通常 **12–18 分钟**；网络或模型慢时会更久。
- 课前先跑一次并保留完成的项目；现场如果超过 10 分钟还没收口，切到预跑项目展示 Artifacts 和 Knowledge，同时让现场任务继续在后台跑。
- 想展示模型选择，可以在 Idea Generator 私聊里把模型切到 `DeepSeek-V4-Pro` 再问一次；但那一轮会明显更慢、更贵，课堂默认用 Flash 即可。

## 讲解要点

1. **团队是 spec**：roles、tools、budget 都在 `examples/swarms/autoresearch.yaml`，不是写死的代码。
2. **orchestrator 不替 specialist 干活**：PI 只拆解、派活、验收。
3. **两种记忆**：知识图是项目共享库；agent 私有记忆只服务该 agent，且现在按 project 隔离。
4. **reviewer 是产品的一部分**：设计被 reject 两次是正常流程，不是 demo 出错。
5. **产出可追溯**：每一步的草稿、review、最终 report 都在 Artifacts / Knowledge 里。
