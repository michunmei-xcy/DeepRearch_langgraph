"""State models used by the deep research workflow."""
"""把 `SummaryState` 从 `dataclass` 改为 LangGraph 要求的 `TypedDict`。
`TodoItem` 保持 dataclass 不变（它是数据项，不是图状态）。"""
from dataclasses import dataclass, field
import operator
from typing import Optional, TypedDict
from typing_extensions import Annotated


@dataclass(kw_only=True)
class TodoItem:
    """单个待办任务项。"""
    id: int
    title: str
    intent: str
    query: str
    status: str = field(default="pending")
    summary: Optional[str] = field(default=None)
    sources_summary: Optional[str] = field(default=None)
    notices: list[str] = field(default_factory=list)
    note_id: Optional[str] = field(default=None)
    note_path: Optional[str] = field(default=None)
    stream_token: Optional[str] = field(default=None)


class ResearchState(TypedDict):
    research_topic: str   # Report topic
    todo_items: list
    web_research_results: Annotated[list, operator.add]
    sources_gathered: Annotated[list, operator.add]
    current_task_index: int
    research_loop_count: int   # Research loop count
    structured_report: Optional[str]
    report_note_id: Optional[str]
    report_note_path: Optional[str]
