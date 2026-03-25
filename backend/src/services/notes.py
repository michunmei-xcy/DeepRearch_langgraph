"""Note tools: create / read / update markdown notes as LangChain @tool."""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

NOTES_WORKSPACE = os.getenv("NOTES_WORKSPACE", "./notes")


def _notes_dir() -> Path:
    path = Path(NOTES_WORKSPACE)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _note_path(note_id: str) -> Path:
    return _notes_dir() / f"{note_id}.md"


@tool
def create_note(title: str, content: str, note_type: str, tags: list[str]) -> str:
    """Create a new research note and return its note_id.
    Use this to persist task findings so they can be retrieved later."""
    note_id = uuid.uuid4().hex[:8]
    tag_line = ", ".join(tags)
    text = f"# {title}\n\n**type:** {note_type}  \n**tags:** {tag_line}\n\n{content}\n"
    try:
        _note_path(note_id).write_text(text, encoding="utf-8")
        logger.info("Created note %s: %s", note_id, title)
        return f"笔记已创建，ID: {note_id}"
    except Exception as exc:
        logger.error("Failed to create note: %s", exc)
        return f"笔记创建失败：{exc}"


@tool
def read_note(note_id: str) -> str:
    """Read a previously saved note by its note_id. Returns the full note content."""
    path = _note_path(note_id)
    if not path.exists():
        return f"笔记 {note_id} 不存在。"
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.error("Failed to read note %s: %s", note_id, exc)
        return f"笔记读取失败：{exc}"


@tool
def update_note(note_id: str, title: str, content: str, tags: list[str]) -> str:
    """Update an existing note with new content. Use this to append new findings to a task note."""
    path = _note_path(note_id)
    if not path.exists():
        return f"笔记 {note_id} 不存在，无法更新。"
    tag_line = ", ".join(tags)
    text = f"# {title}\n\n**tags:** {tag_line}\n\n{content}\n"
    try:
        path.write_text(text, encoding="utf-8")
        logger.info("Updated note %s", note_id)
        return f"笔记 {note_id} 已更新。"
    except Exception as exc:
        logger.error("Failed to update note %s: %s", note_id, exc)
        return f"笔记更新失败：{exc}"
