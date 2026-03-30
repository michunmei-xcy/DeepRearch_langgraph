# 迁移指南：HelloAgents → LangGraph

## 目标

将 `helloagents-deepresearch` 后端从 HelloAgents 自定义框架迁移到 LangGraph，保持原有功能不变：
- 三阶段研究流程（规划 → 执行 → 报告）
- 实时 SSE 流式输出
- 多搜索后端支持
- NoteTool 持久化

迁移后的收益：
- 用 LangGraph 标准图结构替代手写 orchestration，便于扩展和维护
- 原生 `astream_events` 替代手写线程池 + 事件队列
- 可接入 LangGraph 生态（LangSmith 追踪、checkpointer 持久化等）

---

## 现状：代码结构

```
backend/src/
├── main.py            FastAPI 入口，两个端点：/research 和 /research/stream
├── agent.py           DeepResearchAgent 主编排器（需要完全重写）
├── config.py          配置管理（基本保留）
├── models.py          SummaryState、TodoItem 数据模型（需要改写）
├── prompts.py         三个 Agent 的系统提示词（完全保留）
├── utils.py           工具函数（完全保留）
└── services/
    ├── planner.py     规划服务（改写为 LangGraph node）
    ├── search.py      搜索服务（包装为 @tool）
    ├── summarizer.py  总结服务（改写为 LangGraph node）
    ├── reporter.py    报告服务（改写为 LangGraph node）
    ├── notes.py       NoteTool 逻辑（包装为 @tool）
    ├── tool_events.py 工具调用追踪（可复用或用 LangSmith 替代）
    └── text_processing.py  文本处理（完全保留）
```

---

## 需要做的工作

### 1. 替换依赖

`pyproject.toml` 中去掉 `hello-agents`，新增：

```toml
langgraph = ">=0.2.0"
langchain-core = ">=0.3.0"
langchain-openai = ">=0.2.0"    # 处理 OpenAI 兼容端点（含 Ollama、LMStudio、custom）
langchain-community = ">=0.3.0" # DuckDuckGo search 等
tavily-python = "*"             # 保留
```

### 2. 改写 `models.py`

把 `SummaryState` 从 `dataclass` 改为 LangGraph 要求的 `TypedDict`。
`TodoItem` 保持 dataclass 不变（它是数据项，不是图状态）。

### 3. 改写 `config.py`（LLM 初始化部分）

把 `HelloAgentsLLM` 换成 `ChatOpenAI`（可对接 Ollama/LMStudio/custom）。
配置读取逻辑基本不变。

### 4. 将工具包装为 LangChain `@tool`

- `search.py`：把 `dispatch_search()` 包装为 `@tool`
- `notes.py`：把 note 的 create/read/update 包装为 `@tool`

> **决策**：`execute_task_node` 使用 `create_react_agent` + ReAct 模式，LLM 自主决定何时调用搜索工具，因此工具必须包装为 `@tool`。

### 5. 改写三个服务为 LangGraph 节点函数

- `planner.py` → `planner_node(state) -> dict`
- `summarizer.py` → **删除**，搜索+总结合并进 `execute_task_node` 的 ReAct agent
- `reporter.py` → `reporter_node(state) -> dict`

### 6. 重写 `agent.py` 为 StateGraph

用 LangGraph `StateGraph` 替代 `DeepResearchAgent` 类，定义节点、边和条件边。

### 7. 改写 `main.py` 的流式端点

把手写的线程池 + 事件队列换成 `graph.astream_events()`。

---

## 方法

### 状态设计

LangGraph 要求图状态是 `TypedDict`，用 `Annotated[list, operator.add]` 表示追加语义（和原来的 dataclass 字段一致）。

```python
# models.py 改写后

from typing import TypedDict, Annotated
import operator

class ResearchState(TypedDict):
    research_topic: str
    todo_items: Annotated[list[TodoItem], operator.add]   # 规划阶段写入，执行阶段更新
    sources_gathered: Annotated[list[str], operator.add]  # 每个 task 追加来源
    running_summary: str                                  # 最新摘要
    structured_report: str                                # 最终报告
    current_task_index: int                               # 当前执行到第几个 task
```

> `TodoItem` 保持 dataclass 不变，放在 state 的列表里。

---

### LLM 初始化

`ChatOpenAI` 支持自定义 `base_url`，可以对接 Ollama、LMStudio 或其他 OpenAI 兼容服务，不需要 `HelloAgentsLLM`。

```python
# config.py 中统一用这个初始化 LLM
from langchain_openai import ChatOpenAI

def get_llm():
    return ChatOpenAI(
        model=settings.llm_model_id,
        base_url=settings.llm_base_url,   # 如 http://localhost:11434/v1
        api_key=settings.llm_api_key or "ollama",
        streaming=True,
    )
```

---

### 工具层

把原来的 `SearchTool`、`NoteTool` 包装为标准 LangChain `@tool`，传给 `create_react_agent`。

**决策：`execute_task_node` 使用 ReAct 模式**，LLM 自主决定何时调用搜索工具，而非固定调用顺序。因此：
- `web_search`：包装 `dispatch_search` + `prepare_research_context`，返回格式化后的搜索内容字符串
- `save_note` / `read_note`：包装 note 操作
- `_config` 通过闭包或工厂函数注入，让工具能读取运行时配置

**`summarizer.py` 可以删除**：搜索和总结合并进 ReAct agent，不再需要单独的 `SummarizationService`。

```python
# services/search.py
from langchain_core.tools import tool

@tool
def web_search(query: str, backend: str = "duckduckgo") -> dict:
    """Search the web and return results with sources."""
    # 复用原有 dispatch_search 逻辑
    ...

# services/notes.py
@tool
def save_note(title: str, content: str, note_type: str, tags: list[str]) -> str:
    """Save a note to the workspace."""
    ...

@tool
def read_note(note_id: str) -> str:
    """Read a note by ID."""
    ...
```

---

### 节点函数

每个节点接收完整 state，返回**部分更新的 dict**，LangGraph 自动 merge。

**planner_node**：调 LLM，解析 JSON，生成 todo_items，发 `todo_list` SSE 事件，返回 `{todo_items, current_task_index: 0}`。

**execute_task_node（ReAct 模式）**：
- 从 state 取出当前 task（by `current_task_index`）
- 发 `task_status(in_progress)` 事件
- 用 `create_react_agent(llm, tools)` 构建 ReAct agent（每个 task 独立实例）
- 用 `astream_events` 流式运行 agent，从中提取：
  - `on_chat_model_stream` → 发 `task_summary_chunk` 事件，同时累积完整 summary
  - `on_tool_end` → 发 `tool_call` 事件
- 过滤 thinking token 后，将 summary 写入 `task.summary`，task.status 改为 "completed"
- 发 `task_status(completed)` 事件
- 返回 `{todo_items（原地修改后的同一列表）, current_task_index+1}`

**reporter_node**：汇总所有 task.summary，调 LLM 生成报告，发 `final_report` 事件。

> **`todo_items` 用方式A**（plain list，无 Annotated reducer）：planner 一次性写入，execute_task 原地修改 dataclass 字段后返回同一列表引用。

---

### 图结构

```python
# agent.py 改写后

from langgraph.graph import StateGraph, START, END

def should_continue(state: ResearchState) -> str:
    if state["current_task_index"] < len(state["todo_items"]):
        return "execute_task"
    return "reporter"

def build_graph():
    builder = StateGraph(ResearchState)

    builder.add_node("planner",      planner_node)
    builder.add_node("execute_task", execute_task_node)
    builder.add_node("reporter",     reporter_node)

    builder.add_edge(START, "planner")
    builder.add_edge("planner", "execute_task")
    builder.add_conditional_edges(
        "execute_task",
        should_continue,
        {"execute_task": "execute_task", "reporter": "reporter"},
    )
    builder.add_edge("reporter", END)

    return builder.compile()

graph = build_graph()
```

流程图：

```
START
  ↓
planner          ← 生成 todo_items，current_task_index=0
  ↓
execute_task     ← 搜索 + 总结第 N 个 task，index+1
  ↓ (还有 task)
execute_task     ← 循环
  ↓ (全部完成)
reporter         ← 汇总生成报告
  ↓
END
```

---

### 流式输出

LangGraph 的 `astream_events` 会在每个节点运行、每个 LLM token 生成时发出事件，不需要手写线程池。

```python
# main.py 流式端点改写

@app.post("/research/stream")
async def research_stream(request: ResearchRequest):
    async def event_generator():
        async for event in graph.astream_events(
            {"research_topic": request.topic},
            version="v2",
        ):
            kind = event["event"]
            node = event.get("metadata", {}).get("langgraph_node", "")

            if kind == "on_chat_model_stream" and node == "execute_task":
                # 实时 token 流
                chunk = event["data"]["chunk"].content
                yield f"data: {json.dumps({'type': 'task_summary_chunk', 'content': chunk})}\n\n"

            elif kind == "on_chain_end" and node == "planner":
                # 规划完成，发送 todo 列表
                todo_items = event["data"]["output"]["todo_items"]
                yield f"data: {json.dumps({'type': 'todo_list', 'tasks': [t.__dict__ for t in todo_items]})}\n\n"

            elif kind == "on_chain_end" and node == "reporter":
                report = event["data"]["output"]["structured_report"]
                yield f"data: {json.dumps({'type': 'final_report', 'report': report})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

前端 `api.ts` 几乎不用改，SSE 事件格式保持一致即可。

---

## 迁移流程（建议顺序）

### Step 1：环境准备
- [x] 更新 `pyproject.toml`，替换 `hello-agents` 为 `langgraph` + `langchain-openai`
- [ ] `uv sync` 安装新依赖，确认能 import

### Step 2：改写 `models.py`
- [x] 将 `SummaryState` 改为 `ResearchState(TypedDict)`
- [x] 确认 `TodoItem` dataclass 字段不变
- [x] 决定 `todo_items` 的 reducer 策略（方式A或B，见上文）:策略A

### Step 3：初始化 LLM
- [ ] 在 `config.py` 中添加 `get_llm()` 返回 `ChatOpenAI`
- [ ] 验证能连通 Ollama/LMStudio/custom 端点

### Step 4：包装工具
- [ ] 在 `services/search.py` 中用 `@tool` 包装 `dispatch_search`
- [ ] 在 `services/notes.py` 中用 `@tool` 包装 note 操作
- [ ] 单独测试工具调用是否正常

### Step 5：改写 planner node
- [ ] 在 `services/planner.py` 中实现 `planner_node(state) -> dict`
- [ ] 复用原有 `parse_todo_items` JSON 解析逻辑
- [ ] 单独测试：传入 topic，能否返回正确的 todo_items

### Step 6：改写 execute_task node
- [ ] 在 `services/summarizer.py` 中实现 `execute_task_node(state) -> dict`
- [ ] 整合搜索 + 总结逻辑
- [ ] 单独测试：传入带 1 个 todo_item 的 state，能否返回 summary

### Step 7：改写 reporter node
- [ ] 在 `services/reporter.py` 中实现 `reporter_node(state) -> dict`
- [ ] 单独测试：传入多个已完成 todo_items，能否生成报告

### Step 8：组装图
- [ ] 在 `agent.py` 中用 `StateGraph` 连接三个节点
- [ ] 添加条件边 `should_continue`
- [ ] 用同步 `graph.invoke()` 做端到端测试

### Step 9：改写流式端点
- [ ] 在 `main.py` 中把 `/research/stream` 改为用 `asyncio.Queue + event_sink + graph.ainvoke` 模式（见下方代码示例）
- [ ] 对齐前端期望的全部 10 种事件类型（见"SSE 事件完整列表"）
- [ ] 加 try/except，异常时发 `{"type": "error", "detail": str(exc)}`，finally 发 `{"type": "done"}`
- [ ] 浏览器端测试 SSE 流

### Step 10：收尾
- [ ] **保留** `services/tool_events.py`，改写为 `ToolCallSink`（不要删除，前端依赖 `tool_call` 事件）
- [ ] 清理 `agent.py` 中旧的 `DeepResearchAgent` 类
- [ ] 更新 `.env.example` 中的配置项

---

## 文件改动汇总

| 文件 | 操作 | 说明 |
|------|------|------|
| `pyproject.toml` | 修改 | 替换 hello-agents 依赖 |
| `models.py` | 改写 | SummaryState → ResearchState TypedDict |
| `config.py` | 局部修改 | 添加 get_llm() 用 ChatOpenAI |
| `agent.py` | 完全重写 | DeepResearchAgent → StateGraph |
| `main.py` | 局部修改 | /research/stream 换 astream_events |
| `services/planner.py` | 改写 | 改为 node 函数 |
| `services/summarizer.py` | 改写 | 改为 node 函数 |
| `services/reporter.py` | 改写 | 改为 node 函数 |
| `services/search.py` | 局部修改 | 添加 @tool 包装 |
| `services/notes.py` | 局部修改 | 添加 @tool 包装 |
| `prompts.py` | 不改 | 系统提示词直接复用 |
| `utils.py` | 不改 | 工具函数直接复用 |
| `services/text_processing.py` | 不改 | 直接复用 |
| `services/tool_events.py` | **保留+改写** | 改为 ToolCallSink，`record()` 直接调 event_sink；**不要删除**，前端 App.vue 依赖 `tool_call` 事件 |
| `services/streaming.py` | **新建** | `ThinkingFilterHandler(BaseCallbackHandler)`，流式过滤 `<think>` token |
| `frontend/` | 不改 | SSE 格式保持兼容即可 |

---

## 已知行为变化

> **并行 → 串行**：原代码用 ThreadPoolExecutor 并行执行多个 task，本次迁移改为 LangGraph StateGraph 串行执行。
> 功能完整，但多任务时速度会变慢。
> 升级路径：用 LangGraph **Send API** 实现 fan-out 并行（独立优化，不在本次迁移范围内）。

---

## SSE 事件完整列表

前端（App.vue）期望 10 种事件，全部需要从节点内通过 `event_sink` 发出：

| 事件类型 | 发出时机 | 发出节点 |
|---|---|---|
| `status` | 每个阶段开始时 | planner_node, execute_task_node |
| `todo_list` | 规划完成后 | planner_node |
| `task_status` (in_progress) | 每个 task 开始前 | execute_task_node |
| `task_status` (completed/skipped/failed) | 每个 task 结束后 | execute_task_node |
| `sources` | 搜索完成后 | execute_task_node |
| `task_summary_chunk` | LLM token 流 | ThinkingFilterHandler 回调 |
| `tool_call` | 工具被调用时 | ToolCallSink.record() |
| `final_report` | 报告生成后 | reporter_node |
| `report_note` | 笔记持久化后 | reporter_node |
| `error` | 任意异常 | main.py except 块 |
| `done` | 流结束 | main.py finally 块 |

---

## 流式端点正确写法（asyncio.Queue 模式）

```python
@app.post("/research/stream")
async def research_stream(request: ResearchRequest):
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    def event_sink(event: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    async def event_generator():
        try:
            graph_task = asyncio.create_task(
                graph.ainvoke(
                    {"research_topic": request.topic},
                    config={"configurable": {
                        "event_sink": event_sink,
                        "config": Configuration.from_env(),
                    }},
                )
            )
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.1)
                    if event is None:
                        break
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    if graph_task.done():
                        while not queue.empty():
                            e = queue.get_nowait()
                            if e is not None:
                                yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
                        break
            if graph_task.exception():
                raise graph_task.exception()
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'detail': str(exc)})}\n\n"
        finally:
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

## 节点内发送事件的模式

```python
def planner_node(state: ResearchState, config: RunnableConfig) -> dict:
    sink = config["configurable"]["event_sink"]
    cfg  = config["configurable"]["config"]

    sink({"type": "status", "message": "开始规划研究任务", "step": 0})
    todo_items = PlanningService(...).plan_todo_list(state)
    if not todo_items:
        todo_items = [PlanningService.create_fallback_task(state)]  # 空结果时 fallback
    sink({"type": "todo_list", "tasks": [asdict(t) for t in todo_items], "step": 0})
    return {"todo_items": todo_items, "current_task_index": 0}


def execute_task_node(state: ResearchState, config: RunnableConfig) -> dict:
    sink = config["configurable"]["event_sink"]
    idx  = state["current_task_index"]
    task = state["todo_items"][idx]
    step = idx + 1

    sink({"type": "task_status", "task_id": task.id,
          "status": "in_progress", "title": task.title, "intent": task.intent, "step": step})

    search_result = dispatch_search(task.query, ...)
    if not search_result:
        task.status = "skipped"
        sink({"type": "task_status", "task_id": task.id, "status": "skipped", "step": step})
        return {"todo_items": state["todo_items"], "current_task_index": idx + 1}

    context = prepare_research_context(task, search_result)
    sink({"type": "sources", "task_id": task.id,
          "latest_sources": format_sources(search_result), "step": step})

    # ThinkingFilterHandler 在 on_llm_new_token 里自动发 task_summary_chunk 事件
    handler = ThinkingFilterHandler(sink, task.id, step, cfg.strip_thinking_tokens)
    llm.invoke([...], config={"callbacks": [handler]})
    summary = handler.full_visible_text

    task.status = "completed"
    task.summary = summary
    sink({"type": "task_status", "task_id": task.id,
          "status": "completed", "summary": summary, "step": step})

    return {
        "todo_items": state["todo_items"],   # 方式A：直接返回同一个列表引用
        "current_task_index": idx + 1,
        "web_research_results": [context],
        "sources_gathered": [format_sources(search_result)],
    }
```

---

## 上下文管理改进（独立于 LangGraph 迁移，可单独做）

### 问题列表

| 编号 | 文件 | 问题 | 影响 |
|---|---|---|---|
| P1 | `utils.py` | `content` 字段无截断（`raw_content` 有，`content` 没有）| Bug，某些搜索 API 返回超长 content |
| P2 | `utils.py` | 无总 token 预算，5 个来源可能叠出超大字符串 | 风险，summarizer prompt 可能爆炸 |
| P3 | `reporter.py` | task summary 无截断，随 task 数无限增长 | 风险，reporter prompt 随任务数增长 |
| P4 | `search.py` | `MAX_TOKENS_PER_SOURCE = 2000` 硬编码，不可配置 | 可维护性差 |

### 实现顺序

`config.py` → `utils.py` → `search.py` → `reporter.py`

### 改动 1：config.py 新增 3 个字段

```python
max_tokens_per_source: int = Field(
    default=2000,
    description="每个搜索结果来源最多包含的 token 数",
)
max_total_context_tokens: int = Field(
    default=8000,
    description="单次 LLM 调用中所有来源合并后的最大 token 数（0=不限制）",
)
max_reporter_summary_chars: int = Field(
    default=2000,
    description="reporter prompt 中每个 task summary 的最大字符数",
)
```

在 `from_env()` 的 `env_aliases` 里加：
```python
"max_tokens_per_source": os.getenv("MAX_TOKENS_PER_SOURCE"),
"max_total_context_tokens": os.getenv("MAX_TOTAL_CONTEXT_TOKENS"),
"max_reporter_summary_chars": os.getenv("MAX_REPORTER_SUMMARY_CHARS"),
```

### 改动 2：utils.py 修复 content 截断 + 加总预算

函数签名加参数：
```python
def deduplicate_and_format_sources(
    search_response,
    max_tokens_per_source: int,
    *,
    fetch_full_page: bool = False,
    max_total_context_tokens: int = 0,   # 0 = 不限制，向后兼容
) -> str:
```

循环前计算 `char_limit`，对 `content` 加截断（P1）：
```python
char_limit = max_tokens_per_source * CHARS_PER_TOKEN
for source in unique_sources.values():
    content = source.get("content", "")
    if len(content) > char_limit:
        content = f"{content[:char_limit]}... [truncated]"
    # ...原有 raw_content 截断逻辑里删掉重复的 char_limit = ... 那行
```

返回前加总预算截断（P2）：
```python
result = "".join(formatted_parts).strip()
if max_total_context_tokens > 0:
    total_char_limit = max_total_context_tokens * CHARS_PER_TOKEN
    if len(result) > total_char_limit:
        result = f"{result[:total_char_limit]}... [context truncated]"
return result
```

### 改动 3：search.py 删除硬编码常量

删除：`MAX_TOKENS_PER_SOURCE = 2000`

`dispatch_search()` 里替换：
```python
"max_tokens_per_source": config.max_tokens_per_source,
```

`prepare_research_context()` 里替换：
```python
context = deduplicate_and_format_sources(
    search_result or {"results": []},
    max_tokens_per_source=config.max_tokens_per_source,
    fetch_full_page=config.fetch_full_page,
    max_total_context_tokens=config.max_total_context_tokens,
)
```

### 改动 4：reporter.py 截断 task summary

```python
summary_char_limit = self._config.max_reporter_summary_chars
for task in state.todo_items:
    summary_block = task.summary or "暂无可用信息"
    if len(summary_block) > summary_char_limit:
        summary_block = f"{summary_block[:summary_char_limit]}... [truncated]"
    # ...其余不变
```

---

## 参考资料

- LangGraph 官方文档：https://langchain-ai.github.io/langgraph/
- StateGraph API：https://langchain-ai.github.io/langgraph/reference/graphs/
- LangChain BaseCallbackHandler：https://python.langchain.com/docs/how_to/custom_callbacks/
- ChatOpenAI with custom base_url：https://python.langchain.com/docs/integrations/chat/openai/
- LangGraph Send API（并行升级路径）：https://langchain-ai.github.io/langgraph/how-tos/map-reduce/
