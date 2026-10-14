### CrewAI
```text
crew.kickoff()                         agent.kickoff()
    │                                      │
    ▼                                      ▼
_execute_tasks()                       _prepare_kickoff()
    │                                      │  新建 executor，task=None
    ▼                                      │  recall(用户消息, limit=20)
task.execute_sync()                        │
    │                                      ▼
    ▼                                  executor.invoke()
agent.execute_task()                       │
    │  recall(task.description, limit=5)   │  末尾调用 _save_to_memory()
    ▼                                      │  task 为空，直接返回
executor.invoke()                          ▼
    │                                      _finalize_kickoff()
    │  末尾调用 _save_to_memory()           │  guardrail
    │  task 在，这里真正写入                ▼
    ▼                                  _save_kickoff_to_memory()  ← 这里真正写入
task 收成 TaskOutput
    │
    ▼
下一个 task，或 crew 结束时 drain_writes()
```
#### Crew 跑 task
从上往下，一次 crew.kickoff() 是整支 crew 的生命周期，记忆写入发生在每个 task 内部，不是 crew 结束时。

Crew.kickoff() 做插值、发开始事件，然后按 process 进入 _run_sequential_process() 或 _run_hierarchical_process()。两者都进 _execute_tasks()。
_execute_tasks() 按顺序处理 task。同步 task 走 task.execute_sync()；async_execution=True 的走 task.execute_async()，结果仍回到同一条收尾。上游 task 的输出在这里拼成 context 字符串传下去，这是 task 依赖，不是 memory recall。
Task._execute_core() 选定 agent，发 TaskStartedEvent，调用 agent.execute_task(task, context, tools)。
Agent.execute_task() 先准备 prompt，再交给 executor：
_prepare_task_execution() 生成 task prompt，附上 context，再 _retrieve_memory_context()。有 agent.memory 或 crew._memory 时，用 task.description 调 recall(limit=5)，命中内容追加进 prompt。
接着做 knowledge retrieval。
_finalize_task_prompt() 里 create_agent_executor(task=task)。这个 executor 带着当前 task、agent 和 crew。
executor.invoke() 跑 ReAct 循环。如果 task 要求 human input，循环结束后先处理反馈。然后调用 _save_to_memory(formatted_answer)，再把 {"output": ...} 返回给 execute_task()。
_save_to_memory() 这时会写：executor 上有 task，且 agent.memory 或 crew._memory 存在，且不是 read-only，且最终输出不是「Delegate work to coworker」。它把 task、role、expected output、result 交给 extract_memories()，再 remember_many()。remember_many() 提交后台线程后立刻返回。
控制权回到 execute_task() 的 _finalize_task_execution()，再回到 Task._execute_core()。task 的 guardrail、TaskOutput 都发生在这次写入之后。写入用的是 guardrail 之前的 executor 原文。
_execute_tasks() 把这个 TaskOutput 放进列表，继续下一个 task。下一个 task 的 recall 会先 drain_writes()，所以能看见上一个 task 刚提交的保存。
全部 task 结束后 _create_crew_output() 会 drain_writes()。kickoff() 的 finally 再 drain 一次，覆盖异常退出。
从下往上，_save_to_memory() 只有一个有效调用者：带着 task 的 executor。

调用点是 AgentExecutor.invoke() 和 invoke_async() 的最后一行。若把 executor_class 换成 CrewAgentExecutor，对应的是它的 invoke() / ainvoke()，位置相同：循环和 human feedback 之后、return 之前。
这个 executor 来自 create_agent_executor(task=...)，而它只在 execute_task() / aexecute_task() 准备 prompt 时创建或更新。
execute_task() 的调用者是 Task._execute_core()，同步入口是 execute_sync()，异步入口是 execute_async() 里的线程。
这两个 task 方法的调用者是 Crew._execute_tasks() / _aexecute_tasks()。
再往上是 _run_sequential_process() 或 _run_hierarchical_process()，公开入口是 Crew.kickoff() 和 Crew.kickoff_async()。
所以 crew 路径上，每个成功跑完的 task 各写一次。crew 结束时只是等待这些后台写入完成。

#### 单独 agent.kickoff
从上往下，agent.kickoff(messages) 不创建 Task，也不经过 Crew._execute_tasks()。

Agent.kickoff() 若发现自己在事件循环里，直接返回 kickoff_async()。否则继续同步路径。akickoff() 是 kickoff_async() 的别名。三条公开方法最后都进 _finalize_kickoff()。
_prepare_kickoff() 做三件事：
新建一个 AgentExecutor，构造参数里没有 task，也没有 crew。
若 agent.memory 存在，用用户消息调 recall(limit=20)，把结果追加到发给 executor 的 input。这里不回落 crew._memory。
返回这个 executor 和 inputs。
_execute_and_build_output() 调用 executor.invoke(inputs)。invoke() 末尾仍会调用 _save_to_memory()，但这个 executor 的 task 是 None，方法第一段就 return。这次调用发生了，没有写入。
invoke() 返回后，_finalize_kickoff() 先跑 agent guardrail。guardrail 可能让 executor 再跑一轮，那几轮里的 _save_to_memory() 同样因为没有 task 而空返回。
guardrail 通过后，_finalize_kickoff() 调用 _save_kickoff_to_memory(messages, output.raw)。agent.memory 存在时，把 input、role、最终 result 交给 extract_memories() 再 remember_many()。这里的 result 已经是 guardrail 之后的文本。
然后发 LiteAgentExecutionCompletedEvent 并返回 LiteAgentOutput。这条路径没有 crew 的 finally drain；后台保存要等下次 recall() 里的 drain_writes()，或调用方自己 drain。
从下往上，_save_kickoff_to_memory() 只有一个调用者：_finalize_kickoff()。

_finalize_kickoff() 的调用者是 kickoff() 和 kickoff_async()，都在 _execute_and_build_output() 成功返回之后。
akickoff() 只转发到 kickoff_async()。
若 kickoff() 发现当前在事件循环里，它不自己跑，而是返回 kickoff_async() 的协程，收尾仍是同一个 _finalize_kickoff()。
