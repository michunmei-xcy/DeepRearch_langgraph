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

### 5. 改写三个服务为 LangGraph 节点函数

- `planner.py` → `planner_node(state) -> dict`
- `summarizer.py` → `execute_task_node(state) -> dict`
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

把原来的 `SearchTool`、`NoteTool` 包装为标准 LangChain tool，方便在 node 里调用或传给 ReAct agent。

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

```python
# planner node
def planner_node(state: ResearchState) -> dict:
    response = llm_with_tools.invoke([
        SystemMessage(PLANNER_PROMPT),
        HumanMessage(state["research_topic"]),
    ])
    todo_items = parse_todo_items(response.content)  # 复用原有 JSON 解析逻辑
    return {
        "todo_items": todo_items,
        "current_task_index": 0,
    }

# execute_task node（搜索 + 总结）
def execute_task_node(state: ResearchState) -> dict:
    idx = state["current_task_index"]
    task = state["todo_items"][idx]

    # 搜索
    search_result = web_search.invoke({"query": task.query})

    # 总结
    response = llm.invoke([
        SystemMessage(SUMMARIZER_PROMPT),
        HumanMessage(format_research_context(task, search_result)),
    ])
    summary = strip_thinking_tokens(response.content)

    # 更新 task 状态（注意：todo_items 是 Annotated[list, operator.add]，
    # 需要用替换整个列表或用自定义 reducer 的方式更新单个 item）
    updated_items = state["todo_items"].copy()
    updated_items[idx] = dataclasses.replace(task, status="completed", summary=summary)

    return {
        "todo_items": updated_items,           # 如果用自定义 reducer 则只传更新项
        "sources_gathered": search_result["sources"],
        "current_task_index": idx + 1,
    }

# reporter node
def reporter_node(state: ResearchState) -> dict:
    all_summaries = "\n\n".join(t.summary for t in state["todo_items"])
    response = llm.invoke([
        SystemMessage(REPORTER_PROMPT),
        HumanMessage(all_summaries),
    ])
    return {"structured_report": response.content}
```

> **注意**：`todo_items` 用了 `operator.add`（追加语义），但执行阶段是**更新**已有项而非追加新项。
> 有两种解决方式：
> - **方式A（推荐）**：`todo_items` 改为普通字段（不用 `Annotated`），planner 一次性写入，后续节点直接替换整个列表。
> - **方式B**：为 `todo_items` 写自定义 reducer，根据 `id` merge。

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
- [ ] 更新 `pyproject.toml`，替换 `hello-agents` 为 `langgraph` + `langchain-openai`
- [ ] `uv sync` 安装新依赖，确认能 import

### Step 2：改写 `models.py`
- [ ] 将 `SummaryState` 改为 `ResearchState(TypedDict)`
- [ ] 确认 `TodoItem` dataclass 字段不变
- [ ] 决定 `todo_items` 的 reducer 策略（方式A或B，见上文）

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
- [ ] 在 `main.py` 中把 `/research/stream` 改为用 `graph.astream_events()`
- [ ] 对齐前端期望的事件类型（`todo_list`、`task_summary_chunk`、`final_report`、`done`）
- [ ] 浏览器端测试 SSE 流

### Step 10：收尾
- [ ] 删除 `services/tool_events.py`（如果不再需要手动追踪）
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
| `services/tool_events.py` | 可删除 | 用 astream_events 事件替代 |
| `frontend/` | 不改 | SSE 格式保持兼容即可 |

---

## 参考资料

- LangGraph 官方文档：https://langchain-ai.github.io/langgraph/
- StateGraph API：https://langchain-ai.github.io/langgraph/reference/graphs/
- astream_events：https://python.langchain.com/docs/how_to/streaming/#using-stream-events
- ChatOpenAI with custom base_url：https://python.langchain.com/docs/integrations/chat/openai/
- create_react_agent（如果想用 prebuilt）：https://langchain-ai.github.io/langgraph/reference/prebuilt/
