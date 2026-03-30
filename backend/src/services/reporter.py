"""Service that consolidates task results into the final report."""

from __future__ import annotations

import logging
from typing import Callable

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI

from models import ResearchState
from prompts import report_writer_instructions

import os
from services.notes import create_note

logger = logging.getLogger(__name__)
MAX_SUMMARY_CHARS = 2000

def make_reporter_node(llm:ChatOpenAI) -> Callable:
    """Return a LangGraph node function for task planning."""

    def reporter_node(state:ResearchState,config:RunnableConfig) -> dict:
        # 1. 取 event_sink
        event_sink=(config.get("configurable")or {}).get("event_sink")

        # 2.拼每个任务的摘要块
        
        tasks_block = []
        for task in state["todo_items"]:
            summary = task.summary or "暂无可用信息"
            if len(summary) > MAX_SUMMARY_CHARS:
                summary = summary[:MAX_SUMMARY_CHARS] + "... [truncated]"
            sources_block = task.sources_summary or "暂无来源"
            tasks_block.append(
                f"### 任务 {task.id}: {task.title}\n"
                f"- 任务目标：{task.intent}\n"
                f"- 检索查询：{task.query}\n"
                f"- 执行状态：{task.status}\n"
                f"- 任务总结：\n{summary}\n"
                f"- 来源概览：\n{sources_block}\n"
            )
        
        # 3.拼 prompt
        prompt = (
            f"研究主题：{state['research_topic']}\n\n"
            f"任务概览：\n{''.join(tasks_block)}\n\n"
            + report_writer_instructions
        )

        # 4. 调用 LLM 生成报告
        try:
            response=llm.invoke([HumanMessage(content=prompt)])
            report_text=(
                response.content
                if isinstance(response.content,str)
                else str(response.content)
            )
            report_text = report_text.strip() or "报告生成失败。"
        except Exception as exc:
            logger.error("reporter_node failed: %s", exc)
            if event_sink:
                event_sink({"type": "error", "content": str(exc)})
            return {"structured_report": "报告生成失败。"}

        logger.info("Report generated, length=%d", len(report_text))

        # 5. 发 SSE 事件
        if event_sink:
            event_sink({"type": "final_report", "report": report_text})

        # 把报告存为 note（env ENABLE_NOTES 控制是否启用）
        if os.getenv("ENABLE_NOTES", "true").lower() == "true":
            result = create_note.invoke({
                "title": f"研究报告：{state['research_topic']}",
                "content": report_text,
                "note_type": "conclusion",
                "tags": ["deep_research", "report"],
            })
            # result 是 "笔记已创建，ID: xxxxxxxx" 这样的字符串
            if event_sink:
                event_sink({"type": "report_note", "content": result})

        # 6. 返回 state 更新
        return {"structured_report": report_text}
    
    return reporter_node


