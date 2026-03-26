"""Planner node: LLM -> JSON -> TodoItem list."""

from __future__ import annotations
import json
import logging
from typing import Any, Callable, List

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI

from models import ResearchState, TodoItem
from prompts import get_current_date, todo_planner_instructions, todo_planner_system_prompt
from utils import strip_thinking_tokens
logger = logging.getLogger(__name__)

def make_planner_node(llm:ChatOpenAI) -> Callable:
    """Return a LangGraph node function for task planning."""

    def planner_node(state:ResearchState,config:RunnableConfig) -> dict:
        # 1.准备prompt
        prompt=todo_planner_instructions.format(
            current_date=get_current_date(),
            research_topic=state["research_topic"],
        )

        # 2.调用LLM 
        response=llm.invoke(
            SystemMessage(content=todo_planner_system_prompt),
            HumanMessage(content=prompt),
        )

        # 3.提取回答内容解析JSON
        raw=response.content if hasattr(response,"content") else str(response)
        tasks_payload=_extract_tasks(raw)

        todo_items:List[TodoItem]=[]

        # 4. 将 JSON 负载转换为 TodoItem 对象列表
        for idx, item in enumerate(tasks_payload, start=1):
            title = str(item.get("title") or f"任务{idx}").strip()
            intent = str(item.get("intent") or "聚焦主题的关键问题").strip()
            query = str(item.get("query") or state["research_topic"]).strip()
            
            if not query:
                query = state["research_topic"]
                
            todo_items.append(TodoItem(id=idx, title=title, intent=intent, query=query))

        # 5.兜底策略：如果 LLM 没产出任务，创建一个默认任务
        if not todo_items:
            logger.info("Planner produced no tasks; using fallback")
            todo_items = [TodoItem(
                id=1,
                title="基础背景梳理",
                intent="收集主题的核心背景与最新动态",
                query=f"{state['research_topic']} 最新进展",
            )]

        logger.info("Planner produced %d tasks", len(todo_items))

        # 6.事件下发（用于UI更新或日志记录）
        event_sink=(config.get("configurable") or {}).get("event_sink")
        if event_sink:
            event_sink({
                "type": "todo_list",
                "tasks": [
                    {
                        "id": t.id,
                        "title": t.title,
                        "intent": t.intent,
                        "query": t.query,
                        "status": t.status,
                    }
                    for t in todo_items
                ],
            })
        return {"todo_items": todo_items, "current_task_index": 0}
    return planner_node


    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------
def _extract_tasks(raw: str) -> List[dict[str, Any]]:
    """Parse planner output into a list of task dicts."""
    # 去除大模型思考过程中的思绪令牌（如 <think>...</think>）
    text = strip_thinking_tokens(raw).strip()

    # 尝试解析带有 "tasks" 键的 JSON 对象
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            if isinstance(obj, dict) and isinstance(obj.get("tasks"), list):
                return [i for i in obj["tasks"] if isinstance(i, dict)]
        except json.JSONDecodeError:
            pass

    # 尝试直接解析 JSON 数组
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end > start:
        try:
            arr = json.loads(text[start:end + 1])
            if isinstance(arr, list):
                return [i for i in arr if isinstance(i, dict)]
        except json.JSONDecodeError:
            pass

    return []