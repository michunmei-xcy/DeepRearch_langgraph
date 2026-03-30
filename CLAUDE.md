# helloagents-deepresearch

> 每次启动先读此文件 → `MIGRATE_TO_LANGGRAPH.md` → `agent.py` → `main.py`
> 阅读/修改源码后，将新发现更新到本文件

## 项目概述

深度研究 Agent：输入主题 → 拆解任务 → 搜索网页 → 总结内容 → 生成报告。
正从 hello-agents 自定义框架迁移到 **LangGraph**。

**技术栈**：FastAPI + LangGraph + LangChain | ChatOpenAI（兼容 Ollama/LMStudio）| Tavily/DuckDuckGo/Perplexity/SearxNG | Vue 3 + TypeScript（SSE）

## 目录结构

```
backend/src/
  main.py              FastAPI 入口，/healthz /research /research/stream（待重写）
  agent.py             build_graph() → StateGraph（已重写）
  config.py            Configuration Pydantic 模型（已更新，新增 max_* 字段）
  models.py            TodoItem(dataclass) / ResearchState(TypedDict)（已重写）
  prompts.py           三个 Agent 的系统提示词（不改）
  utils.py             deduplicate_and_format_sources / format_sources / strip_thinking_tokens
  services/
    planner.py         make_planner_node(llm) → LangGraph 节点（已重写）
    executor.py        make_execute_task_node(llm) → ReAct agent 节点（新建）
    reporter.py        make_reporter_node(llm) → LangGraph 节点（已重写）
    search.py          dispatch_search / prepare_research_context
    notes.py           create_note / read_note / update_note @tool（已重写）
    summarizer.py      已废弃，全部注释（executor.py 替代）
    tool_events.py     待重写为 ToolCallSink
    text_processing.py strip_tool_calls()（已废弃）

frontend/src/
  App.vue              主界面，SSE 处理在 L699-939
  services/api.ts      runResearchStream()，ReadableStream 解析
```

## 研究流程

`planner_node`（主题→3~5个TodoItem）→ `execute_task_node`（每个task：搜索→格式化→LLM总结，循环）→ `reporter_node`（汇总→Markdown报告）

## SSE 事件（前端期望 10 种）

`status` | `todo_list` | `task_status`(in_progress/completed/skipped/failed) | `sources` | `task_summary_chunk` | `tool_call` | `final_report` | `report_note` | `error` | `done`

## 迁移设计约定

- **状态**：`ResearchState` 用 TypedDict；`todo_items: list[TodoItem]` plain list（无 Annotated reducer），planner 一次写入，execute_task 原地修改
- **事件**：LangGraph 管图结构，`event_sink` 回调从节点内发 SSE；`main.py` 用 `asyncio.Queue + graph.ainvoke`；`tool_events.py` 改写为 `ToolCallSink`
- **串行执行**：原并行改串行（已知取舍），**后续计划改回多线程并行**（用 LangGraph Send API fan-out，或复用老代码 Thread + Queue 模式）
- **thinking 过滤**：流式用状态机 `flush_visible()` 过滤 `<think>...</think>`，需新建 `services/streaming.py` 实现 `ThinkingFilterHandler(BaseCallbackHandler)`
- **来源实时推送**：现在来源和总结一起跳出（agent 跑完才发事件）。理想是每次 web_search ToolMessage 返回时立即发 sources 事件。需要用 `agent.astream_events()` 替换 `agent.invoke()`，在流式事件里捕获每个 ToolMessage 实时推送

## 上下文管理

| 优先级 | 问题                                | 方案                                                         | 状态 |
| ------ | ----------------------------------- | ------------------------------------------------------------ | ---- |
| P1     | `content` 字段无截断                | `utils.py` deduplicate_and_format_sources 加截断             | 待做 |
| P2     | 无总 token 预算                     | `utils.py` 加 `max_total_context_tokens` 参数并实现截断逻辑，search.py 传参 | 临时删掉 search.py 的传参绕过，待实现 |
| P3     | reporter prompt 随 task 数无限增长  | `reporter.py` 每个 summary 拼入前截断至 MAX_SUMMARY_CHARS    | 已做 |
| P4     | `MAX_TOKENS_PER_SOURCE=2000` 硬编码 | `config.py` 新增 `max_tokens_per_source`，`search.py` 引用   | 已加字段，search.py 引用待做 |

## 环境变量

```
LLM_PROVIDER=custom  LLM_MODEL_ID=  LLM_API_KEY=  LLM_BASE_URL=
SEARCH_API=tavily    TAVILY_API_KEY=
MAX_WEB_RESEARCH_LOOPS=3  FETCH_FULL_PAGE=True
ENABLE_NOTES=True  NOTES_WORKSPACE=./notes
```

## 进展

- [x] pyproject.toml 依赖替换
- [x] MIGRATE_TO_LANGGRAPH.md 迁移指南完成
- [x] config.py — 新增 max_tokens_per_source / max_total_context_tokens / max_reporter_summary_chars
- [x] models.py — SummaryState → ResearchState(TypedDict)，SummaryStateOutput 删除
- [x] services/planner.py — 重写为 make_planner_node(llm)
- [x] services/executor.py — 新建，make_execute_task_node(llm)，ReAct agent
- [x] services/reporter.py — 重写为 make_reporter_node(llm)
- [x] services/notes.py — 重写为 @tool 函数（create_note/read_note/update_note）
- [x] agent.py — 重写为 build_graph() + _init_llm() + _should_continue()
- [x] main.py — SSE 流式端点（asyncio.Queue + graph.ainvoke）已跑通
- [ ] executor.py — 提取 summary 后过滤 `<think>` token（strip_thinking_tokens）
- [ ] services/tool_events.py — 重写为 ToolCallSink，发 tool_call 事件给前端
- [ ] services/streaming.py — 新建 ThinkingFilterHandler
- [ ] utils.py — deduplicate_and_format_sources 加截断（P1）
- [ ] search.py — 引用 config.max_tokens_per_source（P2/P4）

## 待做功能增强

### F1 — 探索性预搜索注入 Planner
规划前先做一次探索性搜索，把搜索摘要注入 Planner 的 Prompt，让子任务拆解基于真实的最新信息，而不是 LLM 的训练记忆。
- 改动点：`planner_node` 在调 LLM 之前先调 `web_search`，把结果拼入 prompt
- 新增 ResearchState 字段：`pre_search_summary: str`（可选）

### F2 — 领域专家 Agent 质量评估 + 重试
每个子任务完成后，Expert Agent 评估总结质量，不合格最多重试 2 次，重试时必须换搜索关键词。
- Expert Agent 用轻量模型（config 新增 `expert_model_id` 字段），只做评估不做生成
- 只有 Executor（Summarizer）和 Reporter 用强模型
- 评估结果字段：`passed: bool`、`suggestion: str`（指导下一次用不同关键词搜索）
- 改动点：
  - `models.py` — TodoItem 新增 `retry_count: int`、`expert_suggestion: str` 字段
  - `services/expert.py` — 新建，`make_expert_node(llm_light)`，输入 task.summary，输出评估结果
  - `agent.py` — 图中 executor 后加条件边：passed → 下一任务 / not passed & retry<2 → 回到 executor（换 query）
  - `config.py` — 新增 `expert_model_id`、`expert_llm_base_url` 等字段