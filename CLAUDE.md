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
  main.py              FastAPI 入口，/healthz /research /research/stream
  agent.py             DeepResearchAgent 主编排器（待重写为 StateGraph）
  config.py            Configuration Pydantic 模型
  models.py            TodoItem / SummaryState / SummaryStateOutput
  prompts.py           三个 Agent 的系统提示词（不改）
  utils.py             deduplicate_and_format_sources / format_sources / strip_thinking_tokens
  services/
    planner.py         LLM → JSON → TodoItem 列表
    search.py          dispatch_search / prepare_research_context
    summarizer.py      同步 + 流式两种模式
    reporter.py        汇总 task summary → 最终 Markdown 报告
    notes.py           build_note_guidance()
    tool_events.py     ToolCallTracker → tool_call SSE 事件
    text_processing.py strip_tool_calls()

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
- **串行执行**：原并行改串行（已知取舍），升级路径为 LangGraph Send API fan-out
- **thinking 过滤**：流式用状态机 `flush_visible()` 过滤 `<think>...</think>`，需新建 `services/streaming.py` 实现 `ThinkingFilterHandler(BaseCallbackHandler)`

## 上下文管理（待改进）

| 优先级 | 问题                                | 方案                                                       |
| ------ | ----------------------------------- | ---------------------------------------------------------- |
| P1     | `content` 字段无截断                | `utils.py` deduplicate_and_format_sources 加截断           |
| P2     | 无总 token 预算                     | `config.py` 新增 `max_total_context_tokens`                |
| P3     | reporter prompt 随 task 数无限增长  | `reporter.py` 每个 summary 拼入前截断                      |
| P4     | `MAX_TOKENS_PER_SOURCE=2000` 硬编码 | `config.py` 新增 `max_tokens_per_source`，`search.py` 引用 |

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
- [x] 上下文改进方案规划完成
- [ ] **实际迁移代码尚未动手**