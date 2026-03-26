"""Execute-task node: ReAct agent that searches and summarises one TodoItem."""

from __future__ import annotations

import logging
from typing import Callable

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from models import ResearchState, TodoItem
from prompts import task_executor_instructions
from services.notes import create_note, read_note, update_note
from services.search import web_search

logger = logging.getLogger(__name__)


def make_execute_task_node(llm: ChatOpenAI) -> Callable:
    agent = create_react_agent(llm, tools=[web_search, create_note, read_note, update_note])

    def execute_task_node(state: ResearchState, config: RunnableConfig) -> dict:
        # 1. 取出当前任务
        todo_items: list[TodoItem] = list(state["todo_items"])
        idx = state["current_task_index"]

        # 越界：所有任务已完成
        if idx >= len(todo_items):
            return {"current_task_index": idx}

        task = todo_items[idx]

        # 2. 通知前端：任务开始
        event_sink = (config.get("configurable") or {}).get("event_sink")
        if event_sink:
            event_sink({"type": "task_status", "task_id": task.id, "status": "in_progress"})

        # 3. 构建 prompt：把任务上下文拼入执行指令
        prompt = (
            f"研究主题：{state['research_topic']}\n"
            f"任务名称：{task.title}\n"
            f"任务目标：{task.intent}\n"
            f"建议检索词：{task.query}\n\n"
            f"必须使用 web_search 工具至少搜索一次。\n\n"
            + task_executor_instructions
        )

        # 4. 调用 ReAct agent
        try:
            result = agent.invoke({"messages": [HumanMessage(content=prompt)]}, config)
            messages = result.get("messages", [])
        except Exception as exc:
            logger.error("execute_task_node failed for task %d: %s", task.id, exc)
            task.status = "failed"
            task.summary = "暂无可用信息"
            todo_items[idx] = task
            if event_sink:
                event_sink({"type": "task_status", "task_id": task.id, "status": "failed"})
            return {"todo_items": todo_items, "current_task_index": idx + 1}

        # 5. 提取 sources：取第一个 web_search ToolMessage 的内容
        sources_text = ""
        for msg in messages:
            if isinstance(msg, ToolMessage) and getattr(msg, "name", "") == "web_search":
                sources_text = msg.content if isinstance(msg.content, str) else str(msg.content)
                break

        if event_sink and sources_text:
            event_sink({"type": "sources", "task_id": task.id, "content": sources_text})

        # 6. 提取 summary：最后一条无 tool_calls 的 AIMessage
        summary = ""
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
                summary = msg.content if isinstance(msg.content, str) else str(msg.content)
                break

        # 7. 更新任务状态并写回列表
        task.summary = summary or "暂无可用信息"
        task.sources_summary = sources_text or None
        task.status = "completed"
        todo_items[idx] = task

        logger.info("Task %d (%s) completed, summary length=%d", task.id, task.title, len(task.summary))

        # 8. 通知前端：任务完成 + 摘要
        if event_sink:
            event_sink({"type": "task_status", "task_id": task.id, "status": "completed"})
            event_sink({"type": "task_summary_chunk", "task_id": task.id, "chunk": task.summary})

        # 9. 返回更新后的 state
        return {
            "todo_items": todo_items,
            "current_task_index": idx + 1,
        }

    return execute_task_node
