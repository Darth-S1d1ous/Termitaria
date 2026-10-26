# Termitaria 课堂 Demo 手册

这份手册包含两个可以在课堂上现场演示的端到端 Demo：

1. [Autoresearch：从文献库到 reviewed experiment plan](autoresearch-demo.md)
2. [Market Research：Copper Desk 的有来源市场简报](market-research-demo.md)

两个 Demo 都走真实 UI，不调用隐藏 API：创建项目 → 选团队模板 → 发消息 → 看多 agent 协作 → 打开 Artifacts / Knowledge。

## 课前准备

```bash
# 在仓库根目录
make up
```

打开 <http://127.0.0.1:3000>。

- **模型**：在 message 栏左侧的模型下拉框里选择 **DeepSeek-V4.1-Flash**。它最快、最便宜，适合课堂现场。
- **不要选 Nebius / NVIDIA**：当前测试只用 DeepSeek。云端模型默认不出现在下拉框里；除非显式设置 `MODEL_CLOUD_ENABLED=1`，否则不会产生云端调用。
- **项目名**：建议带日期或 `demo` 后缀，例如 `Autoresearch Demo 1026`、`Copper Desk Demo 1026`。不要删除旧项目，方便对比。
- **课前预跑**：两个 Demo 各先完整跑一遍，保留一个已完成的项目作为 fallback。现场再新建项目跑实时流程；如果现场网络或模型慢，直接切到预跑项目展示 Artifacts 和 Knowledge。

## 三个常用操作

### 1. 创建项目

1. 点左侧栏最下方的 `+`（New project）。
2. 输入项目名，点 **Confirm**。
3. **Team** 步骤选模板：
   - Autoresearch → `autoresearch`
   - Market research → `copper-desk`
4. **Knowledge** 步骤可以粘贴 seed text，也可以上传本地文件。
5. 点 **Create**。

### 2. 在 General 里发消息

- General 是项目的主频道。消息发给 orchestrator，由它拆解和派活。
- 发送前确认模型下拉框是 `DeepSeek-V4.1-Flash`。
- agent 思考时 message 输入框会禁用，等它结束再发下一轮，不要重复提交。

### 3. 在 General 里 @ 一个 specialist

1. 在 General 的 message 输入框里输入 `@`。
2. 会弹出 agent 列表；点你要找的 agent（例如 `Literature Collector`、`Market Researcher`、`Reviewer`）。
3. 输入你的问题，按 Enter。
4. 被 @ 的 agent 会收到这一轮任务，并可以引用项目知识图和自己的记忆来回答。

也可以直接点左侧 chat 列表里的 agent 名字，进入 1:1 私聊。私聊不会打扰 orchestrator。

## 一个 15 分钟的展示节奏

| 时间 | 做什么 | 讲什么 |
| --- | --- | --- |
| 0–2 min | 打开一个已完成项目，指一下 plan、General、Artifacts | 这是一个多 agent harness，不是单个聊天机器人 |
| 2–4 min | 新建项目，选团队模板，粘贴 seed | 团队是 SwarmSpec，角色、工具、预算都在 YAML 里 |
| 4–7 min | 发主任务，看 plan 和 agent 委派 | orchestrator 拆任务，specialist 各做一块 |
| 7–10 min | 私聊 Idea Generator，再在 General @Literature Collector | 每个 agent 有自己记忆；项目知识图共享；@ 可以指定 specialist |
| 10–13 min | 打开 Artifacts 和 Knowledge | 产出是 report/deck；知识图是项目级共享记忆 |
| 13–15 min | Q&A / 切到预跑项目 | 现场慢时用预跑结果兜底 |

## 演示时可能遇到

- **PI 问你一个决策问题**：这是正常行为。把对应 Demo 文档里的“继续/决策提示词”粘贴回 General。
- **Knowledge 页暂时没有节点**：Graphiti 抽取是异步的，页面每 4 秒刷新；先继续讲 plan，过一会儿再切回来。
- **Market demo 的搜索失败**：让 reviewer 拒绝没有来源的数字，这本身就是 demo 的一部分；“找不到就写找不到”是系统的设计原则。
- **上传文件与草稿的区别**：用户上传的材料通过 `kg` 读取；agent 自己写的草稿在项目 workspace 里，通过 `read_file` 读取。课堂展示不需要展开这一点，除非有人问。
- **不要在 agent 思考时重复按 Enter**：输入框禁用就是还在跑；等它恢复再发下一条。

## Demo 文档

- [Autoresearch Demo](autoresearch-demo.md)
- [Market Research Demo](market-research-demo.md)
