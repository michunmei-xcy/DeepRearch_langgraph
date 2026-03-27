"""Orchestrator coordinating the deep research workflow."""

from __future__ import annotations

import logging
from typing import Any

from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from config import Configuration
from models import ResearchState
from services.planner import make_planner_node
from services.executor import make_execute_task_node
from services.reporter import make_reporter_node

logger = logging.getLogger(__name__)

def _init_llm(config:Configuration) -> ChatOpenAI:
    """Instantiate HelloAgentsLLM following configuration preferences."""
    llm_kwargs: dict[str, Any] = {"temperature": 0.0}

    model_id = config.llm_model_id or config.local_llm
    if model_id:
        llm_kwargs["model"] = model_id

    provider = (config.llm_provider or "").strip()

    if provider == "ollama":
        llm_kwargs["base_url"] = config.sanitized_ollama_url()
        if config.llm_api_key:
            llm_kwargs["api_key"] = config.llm_api_key
        else:
            llm_kwargs["api_key"] = "ollama"
    elif provider == "lmstudio":
        llm_kwargs["base_url"] = config.lmstudio_base_url
        if config.llm_api_key:
            llm_kwargs["api_key"] = config.llm_api_key
    else:
        if config.llm_base_url:
            llm_kwargs["base_url"] = config.llm_base_url
        if config.llm_api_key:
            llm_kwargs["api_key"] = config.llm_api_key

    return ChatOpenAI(**llm_kwargs)


def _should_continue(state: ResearchState) -> str: 
    """决定 execute_task之后是继续循环还是去 reporter"""
    # 当前索引 >= 任务总数，说明所有任务都做完了，去 reporter；
    # 否则继续执行下一个任务，回到 executor。
    cur_index=state["current_task_index"]
    if cur_index>=len(state["todo_items"]):
        return "reporter"
    else:
        return "executor"

def build_graph(config: Configuration | None = None) -> Any:
    # 初始化llm
    config=config or Configuration.from_env()
    llm=_init_llm(config)
    # 1.创建三个结点函数
    planner_node=make_planner_node(llm)
    executor_node=make_execute_task_node(llm)
    reporter_node=make_reporter_node(llm)
    # 2.创建StateGrpah
    graph = StateGraph(ResearchState)
    # 3. 注册节点
    graph.add_node("planner",planner_node)
    graph.add_node("executor",executor_node)
    graph.add_node("reporter",reporter_node)
    # 4.固定边
    graph.add_edge(START,"planner")         #入口 -> planner
    graph.add_edge("planner", "executor")   # planner完成 → executor
    graph.add_edge("reporter", END)         # reporter完成 → 结束
    # 5.条件边：executor 完成后，根据_should_continue 决定去哪
    graph.add_conditional_edges(
        "executor",
        _should_continue,
        # 映射表：_should_continue函数返回值 → 实际跳转的节点名  
        {
            "executor": "executor",
            "reporter": "reporter",
        }
    )
    # 6.编译
    return graph.compile()





# class DeepResearchAgent:
#     """Coordinator orchestrating TODO-based research workflow using HelloAgents."""

#     def __init__(self, config: Configuration | None = None) -> None:
#         """Initialise the coordinator with configuration and shared tools."""
#         self.config = config or Configuration.from_env()
#         self.llm = self._init_llm()

#         self.note_tool = (
#             NoteTool(workspace=self.config.notes_workspace)
#             if self.config.enable_notes
#             else None
#         )
#         self.tools_registry: ToolRegistry | None = None
#         if self.note_tool:
#             registry = ToolRegistry()
#             registry.register_tool(self.note_tool)
#             self.tools_registry = registry
#         # 工具调用追踪器
#         self._tool_tracker = ToolCallTracker(
#             self.config.notes_workspace if self.config.enable_notes else None
#         )
#         # 一个开关，控制是否把工具调用事件往外推（流式模式才需要推，同步模式不需要）
#         self._tool_event_sink_enabled = False
#         # 线程锁，流式模式下多个 task 并行执行，防止同时修改 state 导致数据混乱 
#         self._state_lock = Lock()

#         self.todo_agent = self._create_tool_aware_agent(
#             name="研究规划专家",
#             system_prompt=todo_planner_system_prompt.strip(),
#         )
#         self.report_agent = self._create_tool_aware_agent(
#             name="报告撰写专家",
#             system_prompt=report_writer_instructions.strip(),
#         )

#         self._summarizer_factory: Callable[[], ToolAwareSimpleAgent] = lambda: self._create_tool_aware_agent(  # noqa: E501
#             name="任务总结专家",
#             system_prompt=task_summarizer_instructions.strip(),
#         )

#         self.planner = PlanningService(self.todo_agent, self.config)
#         self.summarizer = SummarizationService(self._summarizer_factory, self.config)
#         self.reporting = ReportingService(self.report_agent, self.config)
#         self._last_search_notices: list[str] = []

#     # ------------------------------------------------------------------
#     # Public API
#     # ------------------------------------------------------------------
    

#     def _create_tool_aware_agent(self, *, name: str, system_prompt: str) -> ToolAwareSimpleAgent:
#         """Instantiate a ToolAwareSimpleAgent sharing tool registry and tracker."""
#         return ToolAwareSimpleAgent(
#             name=name,
#             llm=self.llm,
#             system_prompt=system_prompt,
#             enable_tool_calling=self.tools_registry is not None,
#             tool_registry=self.tools_registry,
#             tool_call_listener=self._tool_tracker.record,
#         )

#     def _set_tool_event_sink(self, sink: Callable[[dict[str, Any]], None] | None) -> None:
#         """Enable or disable immediate tool event callbacks."""
#         self._tool_event_sink_enabled = sink is not None
#         self._tool_tracker.set_event_sink(sink)

#     def run(self, topic: str) -> SummaryStateOutput:
#         """Execute the research workflow and return the final report."""
#         state = SummaryState(research_topic=topic)
#         state.todo_items = self.planner.plan_todo_list(state)
#         self._drain_tool_events(state)

#         if not state.todo_items:
#             logger.info("No TODO items generated; falling back to single task")
#             state.todo_items = [self.planner.create_fallback_task(state)]

#         for task in state.todo_items:
#             self._execute_task(state, task, emit_stream=False)

#         report = self.reporting.generate_report(state)
#         self._drain_tool_events(state)
#         state.structured_report = report
#         state.running_summary = report
#         self._persist_final_report(state, report)

#         return SummaryStateOutput(
#             running_summary=report,
#             report_markdown=report,
#             todo_items=state.todo_items,
#         )

#     def run_stream(self, topic: str) -> Iterator[dict[str, Any]]:
#         """Execute the workflow yielding incremental progress events."""
#         # 建 state，
#         state = SummaryState(research_topic=topic)
#         logger.debug("Starting streaming research: topic=%s", topic)
#         yield {"type": "status", "message": "初始化研究流程"}

#         # 规划 todo_items
#         state.todo_items = self.planner.plan_todo_list(state)
#         for event in self._drain_tool_events(state, step=0):
#             yield event
#         if not state.todo_items:
#             state.todo_items = [self.planner.create_fallback_task(state)]

#         # 给每个 task 分配 channel_map（step 编号 + stream_token）
#         channel_map: dict[int, dict[str, Any]] = {}
#         for index, task in enumerate(state.todo_items, start=1):
#             token = f"task_{task.id}"
#             task.stream_token = token
#             channel_map[task.id] = {"step": index, "token": token}

#         yield {
#             "type": "todo_list",
#             "tasks": [self._serialize_task(t) for t in state.todo_items],
#             "step": 0,
#         }

#         # 建 event_queue（线程安全的 Queue）
#         event_queue: Queue[dict[str, Any]] = Queue()

#         # 定义 enqueue() 函数：往 queue 里放事件，自动附加 step/stream_token
#         def enqueue(
#             event: dict[str, Any],
#             *,
#             task: TodoItem | None = None,
#             step_override: int | None = None,
#         ) -> None:
#             payload = dict(event)
#             target_task_id = payload.get("task_id")
#             if task is not None:
#                 target_task_id = task.id
#                 payload["task_id"] = task.id

#             channel = channel_map.get(target_task_id) if target_task_id is not None else None
#             if channel:
#                 payload.setdefault("step", channel["step"])
#                 payload["stream_token"] = channel["token"]
#             if step_override is not None:
#                 payload["step"] = step_override
#             event_queue.put(payload)

#         def tool_event_sink(event: dict[str, Any]) -> None:
#             enqueue(event)

#         self._set_tool_event_sink(tool_event_sink)

#         threads: list[Thread] = []

#         # 定义 worker(task) 函数：在子线程里执行单个 task，所有事件走 enqueue
#         def worker(task: TodoItem, step: int) -> None:
#             try:
#                 enqueue(
#                     {
#                         "type": "task_status",
#                         "task_id": task.id,
#                         "status": "in_progress",
#                         "title": task.title,
#                         "intent": task.intent,
#                         "note_id": task.note_id,
#                         "note_path": task.note_path,
#                     },
#                     task=task,
#                 )

#                 for event in self._execute_task(state, task, emit_stream=True, step=step):
#                     enqueue(event, task=task)
#             except Exception as exc:  # pragma: no cover - defensive guardrail
#                 logger.exception("Task execution failed", exc_info=exc)
#                 enqueue(
#                     {
#                         "type": "task_status",
#                         "task_id": task.id,
#                         "status": "failed",
#                         "detail": str(exc),
#                         "title": task.title,
#                         "intent": task.intent,
#                         "note_id": task.note_id,
#                         "note_path": task.note_path,
#                     },
#                     task=task,
#                 )
#             finally:
#                 enqueue({"type": "__task_done__", "task_id": task.id})
#         # 为每个 task 启动一个 Thread(target=worker)
#         for task in state.todo_items:
#             step = channel_map.get(task.id, {}).get("step", 0)
#             thread = Thread(target=worker, args=(task, step), daemon=True)
#             threads.append(thread)
#             thread.start()

#         active_workers = len(state.todo_items)
#         finished_workers = 0

#         try:
#             while finished_workers < active_workers:
#                 event = event_queue.get()
#                 if event.get("type") == "__task_done__":
#                     finished_workers += 1
#                     continue
#                 yield event

#             while True:
#                 try:
#                     event = event_queue.get_nowait()
#                 except Empty:
#                     break
#                 if event.get("type") != "__task_done__":
#                     yield event
#         finally:
#             self._set_tool_event_sink(None)
#             for thread in threads:
#                 thread.join()

#         report = self.reporting.generate_report(state)
#         final_step = len(state.todo_items) + 1
#         for event in self._drain_tool_events(state, step=final_step):
#             yield event
#         state.structured_report = report
#         state.running_summary = report

#         note_event = self._persist_final_report(state, report)
#         if note_event:
#             yield note_event

#         yield {
#             "type": "final_report",
#             "report": report,
#             "note_id": state.report_note_id,
#             "note_path": state.report_note_path,
#         }
#         yield {"type": "done"}

#     # ------------------------------------------------------------------
#     # Execution helpers
#     # ------------------------------------------------------------------
#     def _execute_task(
#         self,
#         state: SummaryState,
#         task: TodoItem,
#         *,
#         emit_stream: bool,
#         step: int | None = None,
#     ) -> Iterator[dict[str, Any]]:
#         """Run search + summarization for a single task."""
#         task.status = "in_progress"

#         search_result, notices, answer_text, backend = dispatch_search(
#             task.query,
#             self.config,
#             state.research_loop_count,
#         )
#         self._last_search_notices = notices
#         task.notices = notices

#         if emit_stream:
#             for event in self._drain_tool_events(state, step=step):
#                 yield event
#         else:
#             self._drain_tool_events(state)

#         if notices and emit_stream:
#             for notice in notices:
#                 if notice:
#                     yield {
#                         "type": "status",
#                         "message": notice,
#                         "task_id": task.id,
#                         "step": step,
#                     }

#         if not search_result or not search_result.get("results"):
#             task.status = "skipped"
#             if emit_stream:
#                 for event in self._drain_tool_events(state, step=step):
#                     yield event
#                 yield {
#                     "type": "task_status",
#                     "task_id": task.id,
#                     "status": "skipped",
#                     "title": task.title,
#                     "intent": task.intent,
#                     "note_id": task.note_id,
#                     "note_path": task.note_path,
#                     "step": step,
#                 }
#             else:
#                 self._drain_tool_events(state)
#             return
#         else:
#             if not emit_stream:
#                 self._drain_tool_events(state)

#         sources_summary, context = prepare_research_context(
#             search_result,
#             answer_text,
#             self.config,
#         )

#         task.sources_summary = sources_summary

#         with self._state_lock:
#             state.web_research_results.append(context)
#             state.sources_gathered.append(sources_summary)
#             state.research_loop_count += 1

#         summary_text: str | None = None

#         if emit_stream:
#             for event in self._drain_tool_events(state, step=step):
#                 yield event
#             yield {
#                 "type": "sources",
#                 "task_id": task.id,
#                 "latest_sources": sources_summary,
#                 "raw_context": context,
#                 "step": step,
#                 "backend": backend,
#                 "note_id": task.note_id,
#                 "note_path": task.note_path,
#             }

#             summary_stream, summary_getter = self.summarizer.stream_task_summary(state, task, context)
#             try:
#                 for event in self._drain_tool_events(state, step=step):
#                     yield event
#                 for chunk in summary_stream:
#                     if chunk:
#                         yield {
#                             "type": "task_summary_chunk",
#                             "task_id": task.id,
#                             "content": chunk,
#                             "note_id": task.note_id,
#                             "step": step,
#                         }
#                     for event in self._drain_tool_events(state, step=step):
#                         yield event
#             finally:
#                 summary_text = summary_getter()
#         else:
#             summary_text = self.summarizer.summarize_task(state, task, context)
#             self._drain_tool_events(state)

#         task.summary = summary_text.strip() if summary_text else "暂无可用信息"
#         task.status = "completed"

#         if emit_stream:
#             for event in self._drain_tool_events(state, step=step):
#                 yield event
#             yield {
#                 "type": "task_status",
#                 "task_id": task.id,
#                 "status": "completed",
#                 "summary": task.summary,
#                 "sources_summary": task.sources_summary,
#                 "note_id": task.note_id,
#                 "note_path": task.note_path,
#                 "step": step,
#             }
#         else:
#             self._drain_tool_events(state)

#     def _drain_tool_events(
#         self,
#         state: SummaryState,
#         *,
#         step: int | None = None,
#     ) -> list[dict[str, Any]]:
#         """Proxy to the shared tool call tracker."""
#         events = self._tool_tracker.drain(state, step=step)
#         if self._tool_event_sink_enabled:
#             return []
#         return events

#     @property
#     def _tool_call_events(self) -> list[dict[str, Any]]:
#         """Expose recorded tool events for legacy integrations."""
#         return self._tool_tracker.as_dicts()

#     def _serialize_task(self, task: TodoItem) -> dict[str, Any]:
#         """Convert task dataclass to serializable dict for frontend."""
#         return {
#             "id": task.id,
#             "title": task.title,
#             "intent": task.intent,
#             "query": task.query,
#             "status": task.status,
#             "summary": task.summary,
#             "sources_summary": task.sources_summary,
#             "note_id": task.note_id,
#             "note_path": task.note_path,
#             "stream_token": task.stream_token,
#         }

#     def _persist_final_report(self, state: SummaryState, report: str) -> dict[str, Any] | None:
#         if not self.note_tool or not report or not report.strip():
#             return None

#         note_title = f"研究报告：{state.research_topic}".strip() or "研究报告"
#         tags = ["deep_research", "report"]
#         content = report.strip()

#         note_id = self._find_existing_report_note_id(state)
#         response = ""

#         if note_id:
#             response = self.note_tool.run(
#                 {
#                     "action": "update",
#                     "note_id": note_id,
#                     "title": note_title,
#                     "note_type": "conclusion",
#                     "tags": tags,
#                     "content": content,
#                 }
#             )
#             if response.startswith("❌"):
#                 note_id = None

#         if not note_id:
#             response = self.note_tool.run(
#                 {
#                     "action": "create",
#                     "title": note_title,
#                     "note_type": "conclusion",
#                     "tags": tags,
#                     "content": content,
#                 }
#             )
#             note_id = self._extract_note_id_from_text(response)

#         if not note_id:
#             return None

#         state.report_note_id = note_id
#         if self.config.notes_workspace:
#             note_path = Path(self.config.notes_workspace) / f"{note_id}.md"
#             state.report_note_path = str(note_path)
#         else:
#             note_path = None

#         payload = {
#             "type": "report_note",
#             "note_id": note_id,
#             "title": note_title,
#             "content": content,
#         }
#         if note_path:
#             payload["note_path"] = str(note_path)

#         return payload

#     def _find_existing_report_note_id(self, state: SummaryState) -> str | None:
#         if state.report_note_id:
#             return state.report_note_id

#         for event in reversed(self._tool_tracker.as_dicts()):
#             if event.get("tool") != "note":
#                 continue

#             parameters = event.get("parsed_parameters") or {}
#             if not isinstance(parameters, dict):
#                 continue

#             action = parameters.get("action")
#             if action not in {"create", "update"}:
#                 continue

#             note_type = parameters.get("note_type")
#             if note_type != "conclusion":
#                 title = parameters.get("title")
#                 if not (isinstance(title, str) and title.startswith("研究报告")):
#                     continue

#             note_id = parameters.get("note_id")
#             if not note_id:
#                 note_id = self._tool_tracker._extract_note_id(event.get("result", ""))  # type: ignore[attr-defined]

#             if note_id:
#                 return note_id

#         return None

#     @staticmethod
#     def _extract_note_id_from_text(response: str) -> str | None:
#         if not response:
#             return None

#         match = re.search(r"ID:\s*([^\n]+)", response)
#         if not match:
#             return None

#         return match.group(1).strip()


# def run_deep_research(topic: str, config: Configuration | None = None) -> SummaryStateOutput:
#     """Convenience function mirroring the class-based API."""
#     agent = DeepResearchAgent(config=config)
#     return agent.run(topic)
